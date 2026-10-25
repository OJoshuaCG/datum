"""
Esquema de las aprobaciones destructivas: tabla ``migration_destructive_approvals``, columnas
``model_migrations.last_sql_editor_*`` y migración ``e4f6a8c0b2d3``.

Tres bloques:

1. DDL real sobre SQLite en memoria con el mismo ``Operations`` de Alembic que usa el arranque:
   idempotencia paso a paso, downgrade, CHECKs, ``UNIQUE`` con ``live_key`` y ``CASCADE``.
2. Cada camino que ESCRIBE el SQL de una versión registra al último editor (por la API, por los
   caminos internos del controller y por una guarda sobre el fuente que atrapa un escritor nuevo).
3. Head único del grafo de migraciones.

Verificar con
``.venv/bin/python scripts/run_tests_direct.py tests.test_destructive_approval_schema``.
"""

import ast
import importlib.util
import pathlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from app.core.actor import admin_actor, token_actor
from app.models.base import Base
from app.models.migration_destructive_approval import MigrationDestructiveApproval
from app.services.capability_catalog import GatewayRole

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PATH = next(_ROOT.glob("alembic/versions/*_e4f6a8c0b2d3_destructive_approvals.py"))
_APPROVALS_TABLE = "migration_destructive_approvals"
_EDITOR_COLUMNS = {"last_sql_editor_admin_id", "last_sql_editor_username"}
_SQL_HASH = "a" * 64


def _load_path(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mig = _load_path("mig_e4f6a8c0b2d3", _PATH)


@pytest.fixture
def conn():
    """Tablas padre mínimas (sin las columnas que esta migración agrega) y FK activas."""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        connection.execute(sa.text("CREATE TABLE database_models (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE managed_databases (id INTEGER PRIMARY KEY)"))
        connection.execute(
            sa.text(
                "CREATE TABLE model_migrations (id INTEGER PRIMARY KEY, "
                "model_id INTEGER, version VARCHAR(10))"
            )
        )
        connection.execute(sa.text("INSERT INTO database_models (id) VALUES (1), (2)"))
        connection.execute(sa.text("INSERT INTO managed_databases (id) VALUES (10), (11)"))
        connection.execute(
            sa.text(
                "INSERT INTO model_migrations (id, model_id, version) "
                "VALUES (100, 1, '0001'), (101, 1, '0002')"
            )
        )
        yield connection


def _run(connection, fn):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        fn()


def _insert_approval(connection, **overrides):
    row = {
        "model_migration_id": 100,
        "database_model_id": 1,
        "direction": "up",
        "scope": "database",
        "managed_database_id": 10,
        "scope_key": "database:10",
        "sql_hash": _SQL_HASH,
        "version_label": "0001",
        "reason": "revisado el DROP contra el inventario",
        "approved_by_admin_id": 5,
        "approved_by_username": "aprobador",
        "live_key": 1,
        "revoked_at": None,
    }
    row.update(overrides)
    connection.execute(
        sa.text(
            "INSERT INTO migration_destructive_approvals (model_migration_id, "
            "database_model_id, direction, scope, managed_database_id, scope_key, sql_hash, "
            "version_label, reason, approved_by_admin_id, approved_by_username, live_key, "
            "revoked_at) VALUES (:model_migration_id, :database_model_id, :direction, :scope, "
            ":managed_database_id, :scope_key, :sql_hash, :version_label, :reason, "
            ":approved_by_admin_id, :approved_by_username, :live_key, :revoked_at)"
        ),
        row,
    )


# --------------------------------------------------------------------------- #
# 1. Migración y modelo                                                        #
# --------------------------------------------------------------------------- #


def test_revision_chain():
    assert mig.revision == "e4f6a8c0b2d3"
    assert mig.down_revision == "d2f4a6c8e0b1"


def test_model_is_registered_in_metadata():
    import app.models  # noqa: F401

    assert _APPROVALS_TABLE in Base.metadata.tables
    assert app.models.MigrationDestructiveApproval is MigrationDestructiveApproval


def test_upgrade_creates_the_table_matching_the_model_and_the_editor_columns(conn):
    _run(conn, mig.upgrade)
    inspector = sa.inspect(conn)

    assert inspector.has_table(_APPROVALS_TABLE)
    model_columns = {column.name for column in MigrationDestructiveApproval.__table__.columns}
    assert {column["name"] for column in inspector.get_columns(_APPROVALS_TABLE)} == model_columns

    assert {index["name"] for index in inspector.get_indexes(_APPROVALS_TABLE)} >= {
        "ix_mda_model_id",
        "ix_mda_managed_db",
    }
    unique_constraints = {
        constraint["name"]: constraint["column_names"]
        for constraint in inspector.get_unique_constraints(_APPROVALS_TABLE)
    }
    assert unique_constraints["uq_mda_live"] == [
        "model_migration_id",
        "direction",
        "scope_key",
        "live_key",
    ]
    check_names = {check["name"] for check in inspector.get_check_constraints(_APPROVALS_TABLE)}
    assert check_names == {
        "ck_migration_destructive_approvals_direction",
        "ck_migration_destructive_approvals_scope",
        "ck_migration_destructive_approvals_live_key_revoked",
    }

    assert _EDITOR_COLUMNS <= {column["name"] for column in inspector.get_columns("model_migrations")}


def test_orm_model_creates_the_same_check_constraints_via_create_all():
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    inspector = sa.inspect(engine)
    assert inspector.has_table(_APPROVALS_TABLE)
    assert {check["name"] for check in inspector.get_check_constraints(_APPROVALS_TABLE)} == {
        "ck_migration_destructive_approvals_direction",
        "ck_migration_destructive_approvals_scope",
        "ck_migration_destructive_approvals_live_key_revoked",
    }
    assert _EDITOR_COLUMNS <= {
        column["name"] for column in inspector.get_columns("model_migrations")
    }


def test_upgrade_is_idempotent_and_repairs_a_missing_index(conn):
    _run(conn, mig.upgrade)
    conn.execute(sa.text("DROP INDEX ix_mda_managed_db"))
    _run(conn, mig.upgrade)  # no debe chocar consigo misma
    names = {index["name"] for index in sa.inspect(conn).get_indexes(_APPROVALS_TABLE)}
    assert "ix_mda_managed_db" in names


def test_upgrade_resumes_after_dying_midway_with_one_editor_column_already_added(conn):
    # MySQL no tiene DDL transaccional: un intento que murió entre las dos columnas deja una.
    conn.execute(sa.text("ALTER TABLE model_migrations ADD COLUMN last_sql_editor_admin_id INTEGER"))
    _run(conn, mig.upgrade)
    columns = {column["name"] for column in sa.inspect(conn).get_columns("model_migrations")}
    assert _EDITOR_COLUMNS <= columns


def test_downgrade_drops_the_table_and_the_columns_and_is_idempotent(conn):
    _run(conn, mig.upgrade)
    _run(conn, mig.downgrade)
    inspector = sa.inspect(conn)
    assert not inspector.has_table(_APPROVALS_TABLE)
    assert not (_EDITOR_COLUMNS & {column["name"] for column in inspector.get_columns("model_migrations")})

    _run(conn, mig.downgrade)  # segunda vez: no-op, nunca nombra una constraint
    _run(conn, mig.upgrade)  # y el ciclo se puede repetir
    assert sa.inspect(conn).has_table(_APPROVALS_TABLE)


def test_downgrade_never_names_a_constraint_by_hand():
    source = _PATH.read_text(encoding="utf-8")
    downgrade_source = source[source.index("def downgrade") :]
    assert "drop_constraint" not in downgrade_source


@pytest.mark.parametrize(
    "overrides",
    [
        {"direction": "sideways"},
        {"scope": "global", "managed_database_id": None, "scope_key": "blueprint"},
        # live_key y revoked_at tienen que contar la misma historia.
        {"live_key": None, "revoked_at": None},
        {"live_key": 1, "revoked_at": "2026-10-25 10:00:00"},
        {"live_key": 2, "revoked_at": None},
    ],
)
def test_checks_reject_invalid_direction_scope_and_live_key(conn, overrides):
    _run(conn, mig.upgrade)
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            _insert_approval(conn, **overrides)


def test_a_second_live_approval_for_the_same_version_direction_and_scope_is_rejected(conn):
    _run(conn, mig.upgrade)
    _insert_approval(conn)
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            _insert_approval(conn, approved_by_admin_id=6)


def test_blueprint_scope_is_unique_even_though_its_database_id_is_null(conn):
    # Sin scope_key, el NULL de managed_database_id dentro del UNIQUE dejaría pasar el duplicado.
    _run(conn, mig.upgrade)
    blueprint_scope = {"scope": "blueprint", "managed_database_id": None, "scope_key": "blueprint"}
    _insert_approval(conn, **blueprint_scope)
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            _insert_approval(conn, **blueprint_scope)


def test_terminal_history_and_different_keys_coexist_with_the_live_row(conn):
    _run(conn, mig.upgrade)
    _insert_approval(conn)
    revoked = {"live_key": None, "revoked_at": "2026-10-25 10:00:00"}
    _insert_approval(conn, **revoked)
    _insert_approval(conn, **revoked)  # cualquier cantidad de filas terminales
    _insert_approval(conn, direction="down")  # el rollback es otra aprobación
    _insert_approval(conn, managed_database_id=11, scope_key="database:11")  # otra base
    _insert_approval(conn, model_migration_id=101, version_label="0002")  # otra versión
    count = conn.execute(sa.text("SELECT COUNT(*) FROM migration_destructive_approvals")).scalar()
    assert count == 6


def test_defaults_for_hash_basis_author_unknown_and_execution_count(conn):
    _run(conn, mig.upgrade)
    _insert_approval(conn)
    row = conn.execute(
        sa.text(
            "SELECT hash_basis, author_unknown, execution_count "
            "FROM migration_destructive_approvals"
        )
    ).one()
    assert row.hash_basis == "raw_source_v1"
    assert not row.author_unknown
    assert row.execution_count == 0


def test_deleting_the_version_database_or_blueprint_cascades_to_its_approvals(conn):
    _run(conn, mig.upgrade)
    _insert_approval(conn, model_migration_id=100)
    _insert_approval(conn, model_migration_id=101, version_label="0002")
    conn.execute(sa.text("DELETE FROM model_migrations WHERE id = 100"))
    count = conn.execute(sa.text("SELECT COUNT(*) FROM migration_destructive_approvals")).scalar()
    assert count == 1

    conn.execute(sa.text("DELETE FROM managed_databases WHERE id = 10"))
    count = conn.execute(sa.text("SELECT COUNT(*) FROM migration_destructive_approvals")).scalar()
    assert count == 0


# --------------------------------------------------------------------------- #
# 2. Cada escritor del SQL registra al último editor                           #
# --------------------------------------------------------------------------- #


def _new_model(admin_client, slug="editor", name="Editor") -> int:
    response = admin_client.post("/api/v1/database-models", json={"name": name, "slug": slug})
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _create_version(admin_client, model_id: int) -> dict:
    response = admin_client.post(
        f"/api/v1/database-models/{model_id}/migrations",
        json={"name": "inicial", "up_sql": "CREATE TABLE t (id INT PRIMARY KEY)"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _stored_migration(migration_id: int):
    from app.core.database import Database
    from app.models.model_migration import ModelMigration

    session = Database().get_declarative_base_session()
    try:
        return session.get(ModelMigration, migration_id)
    finally:
        session.close()


def _editor_of(migration_id: int) -> tuple[int | None, str | None]:
    row = _stored_migration(migration_id)
    return row.last_sql_editor_admin_id, row.last_sql_editor_username


def _patch(client, model_id: int, version: str, body: dict):
    return client.patch(f"/api/v1/database-models/{model_id}/migrations/{version}", json=body)


def test_creating_a_version_makes_the_creator_its_first_editor(admin_client):
    model_id = _new_model(admin_client)
    created = _create_version(admin_client, model_id)
    assert _editor_of(created["id"]) == (created["created_by_admin_id"], "admin")


def test_editing_the_up_sql_as_someone_else_replaces_the_editor(admin_client, owner_client):
    model_id = _new_model(admin_client, slug="editor2", name="Editor2")
    created = _create_version(admin_client, model_id)

    response = _patch(
        owner_client, model_id, created["version"], {"up_sql": "CREATE TABLE t (id INT PRIMARY KEY, n INT)"}
    )
    assert response.status_code == 200, response.text

    editor_id, editor_username = _editor_of(created["id"])
    assert editor_username == "owner-solo"
    assert editor_id != created["created_by_admin_id"]
    # El creador no cambia: la regla de cuatro ojos necesita a los DOS.
    assert _stored_migration(created["id"]).created_by_username == "admin"


def test_editing_only_the_down_sql_or_an_override_also_counts_as_an_sql_edit(
    admin_client, owner_client
):
    model_id = _new_model(admin_client, slug="editor3", name="Editor3")
    created = _create_version(admin_client, model_id)

    down_response = _patch(owner_client, model_id, created["version"], {"down_sql": "DROP TABLE t"})
    assert down_response.status_code == 200, down_response.text
    assert _editor_of(created["id"])[1] == "owner-solo"

    admin_response = _patch(
        admin_client,
        model_id,
        created["version"],
        {"up_sql_postgresql": "CREATE TABLE t (id INT PRIMARY KEY)"},
    )
    assert admin_response.status_code == 200, admin_response.text
    assert _editor_of(created["id"])[1] == "admin"


def test_a_patch_that_touches_no_sql_leaves_the_editor_alone(admin_client, owner_client):
    model_id = _new_model(admin_client, slug="editor4", name="Editor4")
    created = _create_version(admin_client, model_id)
    editor_before = _editor_of(created["id"])

    response = _patch(owner_client, model_id, created["version"], {"name": "renombrada"})
    assert response.status_code == 200, response.text
    assert _editor_of(created["id"]) == editor_before


def test_create_without_actor_leaves_the_editor_unknown(admin_client):
    from app.controllers.model_migration_controller import ModelMigrationController

    model_id = _new_model(admin_client, slug="editor5", name="Editor5")
    out = ModelMigrationController().create_migration(
        model_id, {"name": "interno", "up_sql": "CREATE TABLE t (id INT PRIMARY KEY)"}
    )
    assert _editor_of(out["id"]) == (None, None)


def test_a_machine_edit_is_attributed_to_its_issuer_not_to_the_token_pk(admin_client):
    """El PK del token puede coincidir con el id de un usuario cualquiera: no es una persona."""
    from app.controllers.model_migration_controller import ModelMigrationController

    model_id = _new_model(admin_client, slug="editor6", name="Editor6")
    issuer = admin_actor(user_id=41, username="emisor", role=GatewayRole.OWNER)
    machine = token_actor(
        token_pk=7, token_id="tok_x", name="agente-ci", scopes="", project_id=1, issuer=issuer
    )
    out = ModelMigrationController().create_migration(
        model_id, {"name": "via token", "up_sql": "CREATE TABLE t (id INT PRIMARY KEY)"}, admin=machine
    )
    assert _editor_of(out["id"]) == (41, "emisor")


def test_a_machine_without_issuer_leaves_the_editor_unknown(admin_client):
    from app.controllers.model_migration_controller import ModelMigrationController

    model_id = _new_model(admin_client, slug="editor7", name="Editor7")
    machine = token_actor(token_pk=7, token_id="tok_x", name="agente-ci", scopes="", project_id=1)
    out = ModelMigrationController().create_migration(
        model_id, {"name": "via token", "up_sql": "CREATE TABLE t (id INT PRIMARY KEY)"}, admin=machine
    )
    assert _editor_of(out["id"]) == (None, None)


def test_snapshot_versions_record_the_snapshot_actor_as_editor(admin_client):
    from app.controllers.model_migration_controller import ModelMigrationController
    from app.core.database import Database
    from app.models.model_migration import ModelMigration
    from app.services.db_admin.snapshot_layout import VersionPlan

    plans = [
        VersionPlan(
            name="estructura",
            kind="schema",
            up_sql="CREATE TABLE a (id INT PRIMARY KEY)",
            down_sql_suggested=None,
            has_non_portable=False,
        )
    ]
    model_id, _, _ = ModelMigrationController()._persist_snapshot_versions(
        {"name": "Snap", "slug": "snap_editor"},
        "mysql",
        plans,
        False,
        admin={"id": 3, "username": "legado"},
    )
    session = Database().get_declarative_base_session()
    try:
        rows = session.query(ModelMigration).filter(ModelMigration.model_id == model_id).all()
        assert [(m.last_sql_editor_admin_id, m.last_sql_editor_username) for m in rows] == [
            (3, "legado")
        ]
    finally:
        session.close()


def _function_sources_by_name(module_path: pathlib.Path) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _calls_sql_editor_fields(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            called = child.func
            called_name = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", None)
            if called_name == "_sql_editor_fields":
                return True
    return False


def test_every_constructor_of_a_model_migration_in_the_app_sets_the_editor():
    """
    Guarda sobre el fuente: un camino NUEVO que cree versiones sin pasar por
    ``create_migration`` y olvide al editor deja el campo en "desconocido" para siempre, y la
    regla de cuatro ojos lo trataría como legado.
    """
    offenders: list[str] = []
    constructors_seen = 0
    for path in sorted((_ROOT / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            called_name = called.id if isinstance(called, ast.Name) else getattr(called, "attr", None)
            if called_name != "ModelMigration":
                continue
            constructors_seen += 1
            expands_editor = any(
                keyword.arg is None and _calls_sql_editor_fields(keyword.value)
                for keyword in node.keywords
            )
            if not expands_editor:
                offenders.append(f"{path.relative_to(_ROOT)}:{node.lineno}")
    assert constructors_seen >= 2, "la guarda no encontró los constructores conocidos"
    assert offenders == []


def test_the_update_path_that_assigns_sql_columns_also_sets_the_editor():
    controller_path = _ROOT / "app" / "controllers" / "model_migration_controller.py"
    update_function = _function_sources_by_name(controller_path)["update_migration"]
    assigned_sql_columns = {
        target.attr
        for node in ast.walk(update_function)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute)
        and target.attr in {"up_sql", "up_sql_mysql", "up_sql_postgresql", "down_sql"}
    }
    assert assigned_sql_columns == {"up_sql", "up_sql_mysql", "up_sql_postgresql", "down_sql"}
    assert _calls_sql_editor_fields(update_function)


# --------------------------------------------------------------------------- #
# 3. Head único                                                                #
# --------------------------------------------------------------------------- #


def test_the_migration_graph_has_a_single_head_and_it_is_ours():
    script = _load_path(
        "check_migration_graph_mod", _ROOT / "scripts" / "check_migration_graph.py"
    )
    assert script.main() == 0

    parents: dict[str, tuple[str, ...]] = {}
    for path in (_ROOT / "alembic" / "versions").glob("*.py"):
        if path.name == "__init__.py":
            continue
        revision, down_revisions = script._parse_migration(path)
        parents[revision] = down_revisions

    referenced = {parent for down_revisions in parents.values() for parent in down_revisions}
    assert set(parents) - referenced == {"e4f6a8c0b2d3"}
    assert parents["e4f6a8c0b2d3"] == ("d2f4a6c8e0b1",)
