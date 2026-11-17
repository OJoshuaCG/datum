"""
Veredicto de destructividad PRECALCULADO (``model_migration_destructive_verdicts``).

Lo que se mide acá, en tres bloques:

1. La huella (``verdict_fingerprint``) cambia con TODO lo que alimenta el veredicto y con nada más,
   y un valor guardado ilegible se trata como "sin veredicto".
2. La migración ``f7a9c1e3b5d6`` sobre SQLite: idempotente, ``UNIQUE``, ``CHECK`` y ``CASCADE``.
3. Por la API: crear una versión guarda su veredicto; leer usa lo guardado SIN reanalizar; y un
   veredicto viejo, dañado o ausente jamás se sirve: se recalcula (la corrección no depende de
   haber enganchado todos los puntos de escritura).

Verificar con ``.venv/bin/python scripts/run_tests_direct.py tests.test_destructive_verdicts``.
"""

import importlib.util
import json
import pathlib
from dataclasses import replace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from app.controllers.destructive_approval_controller import DestructiveApprovalController
from app.core.database import Database
from app.models.model_migration import ModelMigration
from app.models.model_migration_destructive_verdict import ModelMigrationDestructiveVerdict
from app.services.db_admin.migrations import ManifestStatement, MigrationSpec
from app.services.destructive_approval_catalog import DIRECTION_DOWN, DIRECTION_UP
from app.services.destructive_verdicts import (
    decode_reasons,
    encode_reasons,
    verdict_fingerprint,
)
from tests.test_destructive_approval_api import _edit_up_sql, _version_row, _versions
from tests.test_environment_guard import DROP_SQL, SAFE_SQL, _blueprint

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_MIGRATION_PATH = next(_ROOT.glob("alembic/versions/*_f7a9c1e3b5d6_destructive_verdicts.py"))
_VERDICTS_TABLE = "model_migration_destructive_verdicts"
_FINGERPRINT = "a" * 64


def _spec(**overrides) -> MigrationSpec:
    fields = {
        "id": 1,
        "version": "0001",
        "name": "drop",
        "up_sql": "DROP TABLE clientes",
        "up_sql_mysql": None,
        "up_sql_postgresql": None,
        "down_sql": "CREATE TABLE clientes (id INT)",
        "checksum": "checksum-a",
    }
    fields.update(overrides)
    return MigrationSpec(**fields)


# --------------------------------------------------------------------------- #
# 1. Huella y serialización                                                    #
# --------------------------------------------------------------------------- #
def test_the_fingerprint_is_stable_for_the_same_data():
    assert verdict_fingerprint(_spec(), DIRECTION_UP) == verdict_fingerprint(_spec(), DIRECTION_UP)


def test_the_fingerprint_differs_per_direction():
    spec = _spec()
    assert verdict_fingerprint(spec, DIRECTION_UP) != verdict_fingerprint(spec, DIRECTION_DOWN)


@pytest.mark.parametrize(
    "change",
    [
        {"up_sql": "DROP TABLE otra"},
        {"up_sql_mysql": "DROP TABLE clientes"},
        {"up_sql_postgresql": "DROP TABLE clientes"},
        {"kind": "data"},
        {"has_non_portable": True},
        {"source_engine": "mysql"},
    ],
)
def test_the_up_fingerprint_changes_with_every_input_of_the_up_verdict(change):
    assert verdict_fingerprint(_spec(**change), DIRECTION_UP) != verdict_fingerprint(
        _spec(), DIRECTION_UP
    )


def test_the_up_fingerprint_changes_when_a_manifest_row_is_flagged_destructive():
    plain = _spec(manifest=(ManifestStatement(seq=1, up_sql="DROP TABLE clientes"),))
    flagged = _spec(
        manifest=(ManifestStatement(seq=1, up_sql="DROP TABLE clientes", destructive=True),)
    )
    assert verdict_fingerprint(plain, DIRECTION_UP) != verdict_fingerprint(flagged, DIRECTION_UP)


def test_the_down_fingerprint_changes_with_the_rollback_sql_and_the_manifest_reverse():
    base = _spec()
    assert verdict_fingerprint(replace(base, down_sql="DROP TABLE x"), DIRECTION_DOWN) != (
        verdict_fingerprint(base, DIRECTION_DOWN)
    )
    with_reverse = _spec(
        manifest=(ManifestStatement(seq=1, up_sql="DROP TABLE clientes", down_sql="SELECT 1"),)
    )
    other_reverse = _spec(
        manifest=(ManifestStatement(seq=1, up_sql="DROP TABLE clientes", down_sql="SELECT 2"),)
    )
    assert verdict_fingerprint(with_reverse, DIRECTION_DOWN) != verdict_fingerprint(
        other_reverse, DIRECTION_DOWN
    )


def test_renumbering_or_renaming_does_not_invalidate_the_fingerprint():
    # Cambiar la versión o el checksum no cambia qué destruye el SQL; invalidar acá haría que
    # cada renumerado dejara todo el blueprint sin veredicto.
    base = _spec()
    renumbered = _spec(version="0007", checksum="checksum-b", name="otro nombre")
    for direction in (DIRECTION_UP, DIRECTION_DOWN):
        assert verdict_fingerprint(base, direction) == verdict_fingerprint(renumbered, direction)


def test_fields_are_length_prefixed_so_shifting_text_between_them_changes_the_fingerprint():
    first = _spec(up_sql="ab", up_sql_mysql="c")
    second = _spec(up_sql="a", up_sql_mysql="bc")
    assert verdict_fingerprint(first, DIRECTION_UP) != verdict_fingerprint(second, DIRECTION_UP)


def test_none_and_the_empty_string_do_not_share_a_fingerprint():
    assert verdict_fingerprint(_spec(up_sql_mysql=None), DIRECTION_UP) != verdict_fingerprint(
        _spec(up_sql_mysql=""), DIRECTION_UP
    )


def test_an_unknown_direction_is_rejected():
    with pytest.raises(ValueError):
        verdict_fingerprint(_spec(), "sideways")


def test_reasons_round_trip_and_an_empty_verdict_means_not_destructive():
    reasons = {"mysql": ["DROP TABLE"], "postgresql": ["DROP TABLE", "manifiesto"]}
    assert decode_reasons(encode_reasons(reasons)) == {
        "mysql": ("DROP TABLE",),
        "postgresql": ("DROP TABLE", "manifiesto"),
    }
    assert decode_reasons(encode_reasons({})) == {}


@pytest.mark.parametrize(
    "stored_text",
    ["", "not json", "[]", '"text"', '{"mysql": "DROP"}', '{"mysql": [1]}', '{"mysql": null}'],
)
def test_an_unreadable_stored_verdict_decodes_to_none(stored_text):
    assert decode_reasons(stored_text) is None


# --------------------------------------------------------------------------- #
# 2. Migración f7a9c1e3b5d6                                                    #
# --------------------------------------------------------------------------- #
def _load_migration_module():
    module_spec = importlib.util.spec_from_file_location("mig_f7a9c1e3b5d6", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


migration = _load_migration_module()


@pytest.fixture
def connection():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as live_connection:
        live_connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        live_connection.execute(
            sa.text("CREATE TABLE model_migrations (id INTEGER PRIMARY KEY, version VARCHAR(10))")
        )
        live_connection.execute(
            sa.text("INSERT INTO model_migrations (id, version) VALUES (100, '0001')")
        )
        yield live_connection


def _run(live_connection, step):
    context = MigrationContext.configure(live_connection)
    with Operations.context(context):
        step()


def _insert_verdict(live_connection, *, direction="up", migration_id=100):
    live_connection.execute(
        sa.text(
            f"INSERT INTO {_VERDICTS_TABLE} (model_migration_id, direction, fingerprint, "
            "reasons_by_engine) VALUES (:migration_id, :direction, :fingerprint, '{}')"
        ),
        {"migration_id": migration_id, "direction": direction, "fingerprint": _FINGERPRINT},
    )


def test_upgrade_creates_the_table_and_is_idempotent(connection):
    _run(connection, migration.upgrade)
    _run(connection, migration.upgrade)  # reintento tras una corrida a medias: no debe chocar
    assert _VERDICTS_TABLE in sa.inspect(connection).get_table_names()


def test_downgrade_drops_the_table_and_is_idempotent(connection):
    _run(connection, migration.upgrade)
    _run(connection, migration.downgrade)
    _run(connection, migration.downgrade)
    assert _VERDICTS_TABLE not in sa.inspect(connection).get_table_names()


def test_one_verdict_per_migration_and_direction(connection):
    _run(connection, migration.upgrade)
    _insert_verdict(connection, direction="up")
    _insert_verdict(connection, direction="down")  # la otra dirección sí cabe
    with pytest.raises(IntegrityError):
        _insert_verdict(connection, direction="up")


def test_an_unknown_direction_is_refused_by_the_check_constraint(connection):
    _run(connection, migration.upgrade)
    with pytest.raises(IntegrityError):
        _insert_verdict(connection, direction="sideways")


def test_deleting_the_version_deletes_its_verdicts(connection):
    _run(connection, migration.upgrade)
    _insert_verdict(connection, direction="up")
    connection.execute(sa.text("DELETE FROM model_migrations WHERE id = 100"))
    remaining = connection.execute(sa.text(f"SELECT COUNT(*) FROM {_VERDICTS_TABLE}")).scalar_one()
    assert remaining == 0


def test_a_verdict_for_a_missing_version_is_refused(connection):
    _run(connection, migration.upgrade)
    with pytest.raises(IntegrityError):
        _insert_verdict(connection, migration_id=999)


def test_the_migration_revises_the_approvals_head():
    assert migration.down_revision == "e4f6a8c0b2d3"


# --------------------------------------------------------------------------- #
# 3. Por la API                                                                #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def drop_blueprint(admin_client):
    """Blueprint con la versión 0001 destructiva y sin rollback."""
    return _blueprint(
        admin_client, "bp-veredictos", [{"version": "0001", "name": "drop", "up_sql": DROP_SQL}]
    )


def _stored_verdicts(model_id: int) -> dict[tuple[str, str], ModelMigrationDestructiveVerdict]:
    """Veredictos guardados del blueprint, indexados por ``(versión, dirección)``."""
    session = Database().get_declarative_base_session()
    try:
        rows = (
            session.query(ModelMigration.version, ModelMigrationDestructiveVerdict)
            .join(
                ModelMigrationDestructiveVerdict,
                ModelMigrationDestructiveVerdict.model_migration_id == ModelMigration.id,
            )
            .filter(ModelMigration.model_id == model_id)
            .all()
        )
        for _, verdict in rows:
            session.expunge(verdict)
        return {(version, verdict.direction): verdict for version, verdict in rows}
    finally:
        session.close()


def _overwrite_stored_verdict(model_id: int, *, fingerprint=None, reasons_text=None) -> None:
    session = Database().get_declarative_base_session()
    try:
        verdict_rows = (
            session.query(ModelMigrationDestructiveVerdict)
            .join(
                ModelMigration,
                ModelMigration.id == ModelMigrationDestructiveVerdict.model_migration_id,
            )
            .filter(ModelMigration.model_id == model_id)
            .all()
        )
        assert verdict_rows, "no había veredictos guardados que alterar"
        for verdict_row in verdict_rows:
            if fingerprint is not None:
                verdict_row.fingerprint = fingerprint
            if reasons_text is not None:
                verdict_row.reasons_by_engine = reasons_text
        session.commit()
    finally:
        session.close()


def _forbid_live_analysis(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise AssertionError("se reanalizó el SQL en vez de usar el veredicto guardado")

    monkeypatch.setattr(DestructiveApprovalController, "_reasons_by_engine", fail)


def test_creating_a_version_stores_its_verdict_without_any_read(admin_client, drop_blueprint):
    stored = _stored_verdicts(drop_blueprint)

    up_verdict = stored[("0001", DIRECTION_UP)]
    assert json.loads(up_verdict.reasons_by_engine) != {}  # el DROP es destructivo en algún motor
    # Sin down_sql no hay veredicto de bajada que guardar.
    assert ("0001", DIRECTION_DOWN) not in stored


def test_reading_uses_the_stored_verdict_and_does_not_reanalyze(
    admin_client, drop_blueprint, monkeypatch
):
    analyzed_row = _version_row(admin_client, drop_blueprint, "0001")

    _forbid_live_analysis(monkeypatch)
    row_from_stored_verdict = _version_row(admin_client, drop_blueprint, "0001")

    assert row_from_stored_verdict["reasons_by_engine"] == analyzed_row["reasons_by_engine"]
    assert row_from_stored_verdict["sql_hash"] == analyzed_row["sql_hash"]


def test_editing_the_sql_to_a_safe_one_removes_the_version_from_the_listing(
    admin_client, drop_blueprint
):
    assert _version_row(admin_client, drop_blueprint, "0001")

    _edit_up_sql(admin_client, drop_blueprint, "0001", SAFE_SQL)

    assert [
        row for row in _versions(admin_client, drop_blueprint) if row["version"] == "0001"
    ] == []
    assert (
        json.loads(_stored_verdicts(drop_blueprint)[("0001", DIRECTION_UP)].reasons_by_engine) == {}
    )


def test_a_stale_stored_verdict_is_never_served(admin_client, drop_blueprint):
    # Una fila con huella vieja que dice "no destructiva": si se sirviera, la versión desaparecería
    # del listado y nadie podría aprobarla.
    _overwrite_stored_verdict(drop_blueprint, fingerprint="0" * 64, reasons_text="{}")

    row = _version_row(admin_client, drop_blueprint, "0001")

    assert row["reasons_by_engine"] != {}
    healed = _stored_verdicts(drop_blueprint)[("0001", DIRECTION_UP)]
    assert json.loads(healed.reasons_by_engine) != {}
    assert healed.fingerprint != "0" * 64


def test_a_corrupt_stored_verdict_falls_back_to_live_analysis(admin_client, drop_blueprint):
    # Huella VÁLIDA con contenido ilegible: lo que detecta es decode_reasons, no la huella.
    valid_fingerprint = _stored_verdicts(drop_blueprint)[("0001", DIRECTION_UP)].fingerprint
    _overwrite_stored_verdict(
        drop_blueprint, fingerprint=valid_fingerprint, reasons_text="not json"
    )

    row = _version_row(admin_client, drop_blueprint, "0001")

    assert row["reasons_by_engine"] != {}


def test_a_missing_verdict_is_recomputed_and_saved_by_the_read(admin_client, drop_blueprint):
    session = Database().get_declarative_base_session()
    try:
        session.query(ModelMigrationDestructiveVerdict).delete()
        session.commit()
    finally:
        session.close()
    assert _stored_verdicts(drop_blueprint) == {}

    row = _version_row(admin_client, drop_blueprint, "0001")

    assert row["reasons_by_engine"] != {}
    assert ("0001", DIRECTION_UP) in _stored_verdicts(drop_blueprint)


def test_a_failure_while_saving_verdicts_does_not_break_the_listing(
    admin_client, drop_blueprint, monkeypatch
):
    session = Database().get_declarative_base_session()
    try:
        session.query(ModelMigrationDestructiveVerdict).delete()
        session.commit()
    finally:
        session.close()

    def boom(*_args, **_kwargs):
        raise RuntimeError("base de metadatos de solo lectura")

    monkeypatch.setattr("app.controllers.destructive_approval_controller.store_verdict", boom)

    assert _version_row(admin_client, drop_blueprint, "0001")["reasons_by_engine"] != {}
