"""
Política de las aprobaciones destructivas: hash del SQL fuente, identidad de la PERSONA y
estado de aprobación (A-5).

Es lógica pura: no necesita base ni cliente. Lo que protegen estos tests:

- El hash ata la aprobación al SQL que el aprobador REVISÓ. Si ``None`` y la cadena vacía dieran
  el mismo hash, "sin override" y "override vacío" compartirían aprobación.
- La regla de cuatro ojos compara personas: el id de un token de agente comparte espacio de
  enteros con los usuarios y, usado tal cual, bloquearía a un inocente y dejaría pasar al autor.
- ``superseded`` NO es ``revoked``: reemplazar una aprobación no fue una decisión de revocar.

Verificar con
``.venv/bin/python scripts/run_tests_direct.py tests.test_destructive_approval_policy``.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.models.enums import EngineType
from app.services import destructive_approval_policy as policy
from app.services.destructive_approval_catalog import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_REVOKED,
    APPROVAL_STATUS_STALE_HASH,
    APPROVAL_STATUS_UNAPPROVED,
    DIRECTION_DOWN,
    DIRECTION_UP,
    HASH_BASIS,
    REVOKE_REASON_SUPERSEDED,
    SCOPE_BLUEPRINT,
    SCOPE_DATABASE,
)

CURRENT_HASH = "a" * 64
OLD_HASH = "b" * 64
DATABASE_ID = 7


def _source(
    up_sql="DROP TABLE t", up_sql_mysql=None, up_sql_postgresql=None, down_sql=None, manifest=()
):
    return SimpleNamespace(
        up_sql=up_sql,
        up_sql_mysql=up_sql_mysql,
        up_sql_postgresql=up_sql_postgresql,
        down_sql=down_sql,
        manifest=manifest,
    )


def _approval(
    approval_id,
    *,
    scope=SCOPE_BLUEPRINT,
    managed_database_id=None,
    sql_hash=CURRENT_HASH,
    live=True,
    revoke_reason=None,
    hash_basis=HASH_BASIS,
):
    return SimpleNamespace(
        id=approval_id,
        scope=scope,
        managed_database_id=managed_database_id,
        sql_hash=sql_hash,
        hash_basis=hash_basis,
        live_key=1 if live else None,
        revoked_at=None if live else datetime(2026, 1, 1),
        revoke_reason=revoke_reason,
    )


# --------------------------------------------------------------------------- #
# approval_hash                                                               #
# --------------------------------------------------------------------------- #
def test_the_hash_is_stable_and_hex_sha256():
    first = policy.approval_hash(_source(), DIRECTION_UP)
    second = policy.approval_hash(_source(), DIRECTION_UP)
    assert first == second
    assert len(first) == 64
    assert set(first) <= set("0123456789abcdef")


def test_none_and_empty_string_do_not_share_a_hash():
    without_override = policy.approval_hash(_source(up_sql_mysql=None), DIRECTION_UP)
    empty_override = policy.approval_hash(_source(up_sql_mysql=""), DIRECTION_UP)
    assert without_override != empty_override


@pytest.mark.parametrize("field", ["up_sql", "up_sql_mysql", "up_sql_postgresql"])
def test_changing_any_up_field_changes_the_up_hash(field):
    baseline = policy.approval_hash(_source(), DIRECTION_UP)
    changed = policy.approval_hash(_source(**{field: "DROP TABLE otra"}), DIRECTION_UP)
    assert changed != baseline


def test_the_up_hash_ignores_the_down_sql_and_the_down_hash_ignores_the_up_sql():
    assert policy.approval_hash(_source(down_sql="x"), DIRECTION_UP) == policy.approval_hash(
        _source(down_sql="y"), DIRECTION_UP
    )
    assert policy.approval_hash(_source(up_sql="a", down_sql="z"), DIRECTION_DOWN) == (
        policy.approval_hash(_source(up_sql="b", down_sql="z"), DIRECTION_DOWN)
    )


def test_up_and_down_hashes_of_the_same_text_differ():
    source = _source(up_sql="DROP TABLE t", down_sql="DROP TABLE t")
    assert policy.approval_hash(source, DIRECTION_UP) != policy.approval_hash(
        source, DIRECTION_DOWN
    )


def test_the_down_hash_covers_the_manifest_reverses():
    # Con manifiesto usable el rollback ejecuta los reversos de sus filas, no ``down_sql``: si el
    # hash los ignorara, se aprobaría un texto y se ejecutaría otro.
    benign_down = "SELECT 1"
    with_drop = _source(
        down_sql=benign_down, manifest=(SimpleNamespace(down_sql="DROP TABLE x"),)
    )
    with_other = _source(
        down_sql=benign_down, manifest=(SimpleNamespace(down_sql="DROP TABLE y"),)
    )
    assert policy.approval_hash(with_drop, DIRECTION_DOWN) != policy.approval_hash(
        with_other, DIRECTION_DOWN
    )


class _ManifestReverseRunner:
    """``statement_lists`` como con manifiesto usable: el reverso efectivo sale de sus filas."""

    def __init__(self, down_statements):
        self._down_statements = down_statements

    def statement_lists(self, spec, engine):
        return [spec.up_sql], list(self._down_statements), True


def test_a_benign_down_sql_does_not_hide_a_destructive_manifest_reverse():
    spec = SimpleNamespace(
        version="0001",
        up_sql="CREATE TABLE x (id INT)",
        down_sql="SELECT 1",
        kind="schema",
        has_non_portable=False,
    )
    runner = _ManifestReverseRunner(["DROP TABLE x"])

    destructive = policy.destructive_down_versions(runner, [spec], EngineType.mysql)

    assert "0001" in destructive


def test_a_version_without_effective_reverse_is_not_listed():
    spec = SimpleNamespace(
        version="0001",
        up_sql="CREATE TABLE x (id INT)",
        down_sql=None,
        kind="schema",
        has_non_portable=False,
    )

    destructive = policy.destructive_down_versions(
        _ManifestReverseRunner([]), [spec], EngineType.mysql
    )

    assert destructive == {}


def test_an_unknown_direction_is_rejected():
    with pytest.raises(ValueError):
        policy.approval_hash(_source(), "sideways")


# --------------------------------------------------------------------------- #
# Identidad de la persona                                                      #
# --------------------------------------------------------------------------- #
def test_a_person_actor_resolves_to_their_own_id():
    actor = SimpleNamespace(id=5, username="ana", is_machine=False)
    assert policy.person_id(actor) == 5


def test_a_machine_resolves_to_its_issuer_not_to_its_token_id():
    issuer = SimpleNamespace(id=11, username="emisor")
    machine = SimpleNamespace(id=5, username="token", is_machine=True, issuer=issuer)
    assert policy.person_id(machine) == 11


def test_a_machine_without_issuer_and_no_actor_are_unknown():
    machine = SimpleNamespace(id=5, username="token", is_machine=True, issuer=None)
    assert policy.person_id(machine) is None
    assert policy.person_id(None) is None


def test_an_api_token_creator_counts_as_unknown_but_an_integration_creator_does_not():
    assert policy.author_person_id(5, "api_token") is None
    assert policy.author_person_id(5, "integration_token") == 5
    assert policy.author_person_id(5, None) == 5
    assert policy.author_person_id(None, None) is None


# --------------------------------------------------------------------------- #
# classify_approval (A-5)                                                      #
# --------------------------------------------------------------------------- #
def test_no_rows_is_unapproved():
    status, row = policy.classify_approval([], CURRENT_HASH, DATABASE_ID)
    assert (status, row) == (APPROVAL_STATUS_UNAPPROVED, None)


def test_a_live_row_with_the_current_hash_is_approved():
    row = _approval(1)
    status, satisfying = policy.classify_approval([row], CURRENT_HASH, DATABASE_ID)
    assert status == APPROVAL_STATUS_APPROVED
    assert satisfying is row


def test_a_live_row_with_an_old_hash_is_stale_never_approved():
    status, satisfying = policy.classify_approval(
        [_approval(1, sql_hash=OLD_HASH)], CURRENT_HASH, DATABASE_ID
    )
    assert (status, satisfying) == (APPROVAL_STATUS_STALE_HASH, None)


def test_a_row_with_another_hash_basis_does_not_approve():
    status, _ = policy.classify_approval(
        [_approval(1, hash_basis="otra_base_v9")], CURRENT_HASH, DATABASE_ID
    )
    assert status == APPROVAL_STATUS_STALE_HASH


def test_a_hand_revoked_row_with_the_current_hash_is_revoked():
    status, row = policy.classify_approval(
        [_approval(1, live=False, revoke_reason="no corresponde")], CURRENT_HASH, DATABASE_ID
    )
    assert status == APPROVAL_STATUS_REVOKED
    assert row.id == 1


def test_a_superseded_row_is_not_revoked():
    status, _ = policy.classify_approval(
        [_approval(1, live=False, revoke_reason=REVOKE_REASON_SUPERSEDED)],
        CURRENT_HASH,
        DATABASE_ID,
    )
    assert status == APPROVAL_STATUS_UNAPPROVED


def test_a_revoked_row_with_an_old_hash_is_unapproved():
    status, _ = policy.classify_approval(
        [_approval(1, live=False, sql_hash=OLD_HASH, revoke_reason="no corresponde")],
        CURRENT_HASH,
        DATABASE_ID,
    )
    assert status == APPROVAL_STATUS_UNAPPROVED


def test_approved_wins_over_stale_and_revoked():
    rows = [
        _approval(1, sql_hash=OLD_HASH),
        _approval(2, live=False, revoke_reason="no corresponde"),
        _approval(3),
    ]
    status, row = policy.classify_approval(rows, CURRENT_HASH, DATABASE_ID)
    assert (status, row.id) == (APPROVAL_STATUS_APPROVED, 3)


def test_the_database_scoped_row_wins_over_the_blueprint_one():
    rows = [
        _approval(1, scope=SCOPE_BLUEPRINT),
        _approval(2, scope=SCOPE_DATABASE, managed_database_id=DATABASE_ID),
    ]
    _, row = policy.classify_approval(rows, CURRENT_HASH, DATABASE_ID)
    assert row.id == 2


def test_between_equal_scopes_the_lowest_id_wins():
    rows = [_approval(9), _approval(4)]
    _, row = policy.classify_approval(rows, CURRENT_HASH, DATABASE_ID)
    assert row.id == 4


def test_a_row_scoped_to_another_database_does_not_apply():
    rows = [_approval(1, scope=SCOPE_DATABASE, managed_database_id=DATABASE_ID + 1)]
    status, _ = policy.classify_approval(rows, CURRENT_HASH, DATABASE_ID)
    assert status == APPROVAL_STATUS_UNAPPROVED


def test_without_a_database_only_blueprint_rows_count():
    rows = [_approval(1, scope=SCOPE_DATABASE, managed_database_id=DATABASE_ID)]
    status, _ = policy.classify_approval(rows, CURRENT_HASH, None)
    assert status == APPROVAL_STATUS_UNAPPROVED
    status, _ = policy.classify_approval([_approval(2)], CURRENT_HASH, None)
    assert status == APPROVAL_STATUS_APPROVED
