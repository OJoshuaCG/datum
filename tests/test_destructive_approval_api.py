"""
API de aprobaciones de migraciones destructivas: listar, aprobar (base o blueprint) y revocar.

Lo que decide el negocio y se mide acá:

- Aprueba una SEGUNDA persona: ni el creador de la versión ni su último editor (``creator`` /
  ``last_editor`` en ``public_context.rule``). Una máquina nunca aprueba.
- La aprobación queda atada al hash del SQL revisado: si cambió, 409 ``sql_changed``; aprobar
  de nuevo tras editar reemplaza la anterior (``superseded``) y repetir el mismo hash es 409.
- Aprobar para todo el blueprint exige la capacidad en CADA entorno bloqueante.
- Revocar no pide step-up, es idempotente y lo hace quien aprobó (aunque haya perdido la
  capacidad) o quien tiene ``approve_destructive`` en el alcance (no hay camino por
  ``access.admin``).

Verificar con
``.venv/bin/python scripts/run_tests_direct.py tests.test_destructive_approval_api``.
"""

import json
from types import SimpleNamespace

import pytest

from app.controllers.destructive_approval_controller import DestructiveApprovalController
from app.exceptions import AppHttpException
from tests.access_request_helpers import client_as, create_user
from tests.csrf_helpers import attach_csrf
from tests.test_api_gateway_users import _code
from tests.test_capability_grant_crud import _audits
from tests.test_environment_guard import (
    DROP_SQL,
    SAFE_SQL,
    _blueprint,
    _db,
    _env_ids,
    _owner,
    _server,
)

REASON = "Revisado el SQL con el equipo de datos"
CODE_SELF_APPROVAL = "access.self_approval_forbidden"
CODE_FORBIDDEN = "access.forbidden"
APPROVER_PASSWORD = "ContraseñaDelAprobador123"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _second_owner(admin_client, username: str):
    datos = create_user(admin_client, username, gateway_role="owner")
    return client_as(datos, username)


def _viewer(admin_client, username: str = "lector"):
    datos = create_user(admin_client, username, gateway_role="viewer")
    return client_as(datos, username)


def _versions(client, model_id: int, **params) -> list[dict]:
    r = client.get(f"/api/v1/database-models/{model_id}/destructive-versions", params=params)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _version_row(client, model_id: int, version: str, direction: str = "up", **params) -> dict:
    rows = _versions(client, model_id, **params)
    (row,) = [r for r in rows if r["version"] == version and r["direction"] == direction]
    return row


def _approve_blueprint(client, model_id, version="0001", *, direction="up", sql_hash=None, reason=REASON):
    if sql_hash is None:
        sql_hash = _version_row(client, model_id, version, direction)["sql_hash"]
    return client.post(
        f"/api/v1/database-models/{model_id}/migrations/{version}/destructive-approvals",
        json={"direction": direction, "expected_sql_hash": sql_hash, "reason": reason},
    )


def _approve_database(client, db_id, model_id, version="0001", *, direction="up", reason=REASON):
    sql_hash = _version_row(client, model_id, version, direction)["sql_hash"]
    return client.post(
        f"/api/v1/managed-databases/{db_id}/migrations/{version}/destructive-approvals",
        json={"direction": direction, "expected_sql_hash": sql_hash, "reason": reason},
    )


def _revoke(client, approval_id, reason="Ya no corresponde ejecutarla"):
    return client.post(
        f"/api/v1/destructive-approvals/{approval_id}/revoke", json={"reason": reason}
    )


def _edit_up_sql(client, model_id, version, up_sql):
    r = client.patch(
        f"/api/v1/database-models/{model_id}/migrations/{version}", json={"up_sql": up_sql}
    )
    assert r.status_code == 200, r.text


@pytest.fixture()
def drop_blueprint(admin_client):
    """Blueprint con la versión 0001 destructiva, escrita por el admin (creador y editor)."""
    return _blueprint(
        admin_client, "bp-aprob", [{"version": "0001", "name": "drop", "up_sql": DROP_SQL}]
    )


@pytest.fixture()
def production_database(admin_client, server_payload, drop_blueprint):
    server_id = _server(admin_client, server_payload)
    owner_id = _owner(admin_client, server_id)
    database_id = _db(
        admin_client,
        server_id=server_id,
        owner_id=owner_id,
        model_id=drop_blueprint,
        name="proddb",
        environment_id=_env_ids(admin_client)["production"],
    )
    return database_id


# --------------------------------------------------------------------------- #
# Aprobar                                                                      #
# --------------------------------------------------------------------------- #
def test_a_second_owner_approves_at_blueprint_scope(admin_client, drop_blueprint):
    approver = _second_owner(admin_client, "aprobador")
    r = _approve_blueprint(approver, drop_blueprint)
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["scope"] == "blueprint"
    assert data["managed_database_id"] is None
    assert data["direction"] == "up"
    assert data["is_live"] is True
    assert data["approved_by"]["username"] == "aprobador"
    assert data["reason"] == REASON

    row = _version_row(admin_client, drop_blueprint, "0001")
    assert row["approval_status"] == "approved"
    assert row["effective_approval_id"] == data["id"]


def test_a_second_owner_approves_at_database_scope(
    admin_client, drop_blueprint, production_database
):
    approver = _second_owner(admin_client, "aprobador")
    r = _approve_database(approver, production_database, drop_blueprint)
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["scope"] == "database"
    assert data["managed_database_id"] == production_database

    for_that_database = _version_row(
        admin_client, drop_blueprint, "0001", managed_database_id=production_database
    )
    assert for_that_database["approval_status"] == "approved"
    # Sin la base, solo cuentan las de alcance blueprint: esta no aplica.
    assert _version_row(admin_client, drop_blueprint, "0001")["approval_status"] == "unapproved"


def test_a_non_destructive_version_cannot_be_approved(admin_client):
    model_id = _blueprint(
        admin_client, "bp-seguro", [{"version": "0001", "name": "ok", "up_sql": SAFE_SQL}]
    )
    approver = _second_owner(admin_client, "aprobador")
    r = _approve_blueprint(approver, model_id, sql_hash="a" * 64)
    assert r.status_code == 422, r.text
    assert _code(r) == "destructive_approval.not_destructive"
    assert _versions(admin_client, model_id) == []


def test_a_missing_down_sql_cannot_be_approved(admin_client, drop_blueprint):
    approver = _second_owner(admin_client, "aprobador")
    r = _approve_blueprint(approver, drop_blueprint, direction="down", sql_hash="a" * 64)
    assert r.status_code == 422, r.text
    assert _code(r) == "destructive_approval.down_sql_missing"


def test_approving_an_unknown_version_is_404(admin_client, drop_blueprint):
    approver = _second_owner(admin_client, "aprobador")
    r = _approve_blueprint(approver, drop_blueprint, version="0099", sql_hash="a" * 64)
    assert r.status_code == 404, r.text
    assert _code(r) == "destructive_approval.not_found"


def test_a_malformed_hash_or_short_reason_is_rejected_by_validation(admin_client, drop_blueprint):
    approver = _second_owner(admin_client, "aprobador")
    bad_hash = _approve_blueprint(approver, drop_blueprint, sql_hash="no-es-un-hash")
    short_reason = _approve_blueprint(approver, drop_blueprint, reason="corto")
    assert bad_hash.status_code == 422
    assert short_reason.status_code == 422


def test_a_user_without_the_capability_cannot_approve(admin_client, drop_blueprint):
    viewer = _viewer(admin_client)
    r = _approve_blueprint(viewer, drop_blueprint, sql_hash="a" * 64)
    assert r.status_code == 403, r.text
    assert _code(r) == CODE_FORBIDDEN


def test_approving_without_a_fresh_step_up_is_refused(
    admin_client, drop_blueprint, expire_step_up
):
    approver = _second_owner(admin_client, "aprobador")
    sql_hash = _version_row(approver, drop_blueprint, "0001")["sql_hash"]
    expire_step_up(approver)
    r = _approve_blueprint(approver, drop_blueprint, sql_hash=sql_hash)
    assert r.status_code == 403, r.text
    assert _code(r) == "access.step_up_required"


def test_a_machine_actor_never_approves():
    machine = SimpleNamespace(id=5, username="token", is_machine=True)
    with pytest.raises(AppHttpException) as raised:
        DestructiveApprovalController._assert_not_machine(machine)
    assert raised.value.status_code == 403
    assert raised.value.public_context["code"] == "destructive_approval.machine_actor"


# --------------------------------------------------------------------------- #
# Cuatro ojos                                                                  #
# --------------------------------------------------------------------------- #
def test_the_creator_cannot_approve_their_own_version(admin_client, drop_blueprint):
    r = _approve_blueprint(admin_client, drop_blueprint)
    assert r.status_code == 409, r.text
    assert _code(r) == CODE_SELF_APPROVAL
    assert r.json()["detail"]["public_context"]["rule"] == "creator"
    assert _version_row(admin_client, drop_blueprint, "0001")["approval_status"] == "unapproved"


def test_the_last_sql_editor_cannot_approve_but_a_third_person_can(admin_client, drop_blueprint):
    editor = _second_owner(admin_client, "editor")
    third = _second_owner(admin_client, "tercero")
    _edit_up_sql(editor, drop_blueprint, "0001", "DROP TABLE clientes_historicos")

    as_editor = _approve_blueprint(editor, drop_blueprint)
    assert as_editor.status_code == 409, as_editor.text
    assert _code(as_editor) == CODE_SELF_APPROVAL
    assert as_editor.json()["detail"]["public_context"]["rule"] == "last_editor"

    as_creator = _approve_blueprint(admin_client, drop_blueprint)
    assert as_creator.status_code == 409, as_creator.text
    assert as_creator.json()["detail"]["public_context"]["rule"] == "creator"

    as_third = _approve_blueprint(third, drop_blueprint)
    assert as_third.status_code == 201, as_third.text
    assert as_third.json()["data"]["author_unknown"] is False


def test_an_unknown_author_is_allowed_and_flagged(admin_client, drop_blueprint):
    from sqlalchemy import text

    from app.core.database import Database

    with Database().engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE model_migrations SET created_by_admin_id = NULL, "
                "last_sql_editor_admin_id = NULL WHERE version = '0001'"
            )
        )
    r = _approve_blueprint(admin_client, drop_blueprint)
    assert r.status_code == 201, r.text
    assert r.json()["data"]["author_unknown"] is True


# --------------------------------------------------------------------------- #
# Hash, duplicados y reemplazo                                                 #
# --------------------------------------------------------------------------- #
def test_approving_a_hash_that_no_longer_matches_is_sql_changed(admin_client, drop_blueprint):
    approver = _second_owner(admin_client, "aprobador")
    stale_hash = _version_row(approver, drop_blueprint, "0001")["sql_hash"]
    _edit_up_sql(admin_client, drop_blueprint, "0001", "DROP TABLE otra_tabla")
    r = _approve_blueprint(approver, drop_blueprint, sql_hash=stale_hash)
    assert r.status_code == 409, r.text
    assert _code(r) == "destructive_approval.sql_changed"


def test_approving_the_same_hash_twice_is_a_duplicate(admin_client, drop_blueprint):
    approver = _second_owner(admin_client, "aprobador")
    first = _approve_blueprint(approver, drop_blueprint)
    assert first.status_code == 201, first.text
    again = _approve_blueprint(approver, drop_blueprint)
    assert again.status_code == 409, again.text
    assert _code(again) == "destructive_approval.duplicate"


def test_editing_the_sql_makes_the_approval_stale_and_reapproving_supersedes_it(
    admin_client, drop_blueprint
):
    approver = _second_owner(admin_client, "aprobador")
    first = _approve_blueprint(approver, drop_blueprint).json()["data"]

    _edit_up_sql(admin_client, drop_blueprint, "0001", "DROP TABLE otra_tabla")
    assert _version_row(admin_client, drop_blueprint, "0001")["approval_status"] == "stale_hash"

    second = _approve_blueprint(approver, drop_blueprint)
    assert second.status_code == 201, second.text
    second_id = second.json()["data"]["id"]

    row = _version_row(admin_client, drop_blueprint, "0001")
    assert row["approval_status"] == "approved"
    assert row["effective_approval_id"] == second_id
    history = {approval["id"]: approval for approval in row["approvals"]}
    assert history[first["id"]]["is_live"] is False
    assert history[first["id"]]["revoke_reason"] == "superseded"
    assert history[second_id]["is_live"] is True


def test_blueprint_wide_approval_needs_authority_in_every_blocking_environment(
    admin_client, drop_blueprint
):
    """
    Un owner con la capacidad solo en un entorno no puede aprobar para TODO el blueprint: la
    aprobación valdría también en los entornos bloqueantes donde no tiene autoridad.
    """
    from tests.scope_helpers import env_id
    from tests.test_capability_grant_crud import _grant

    datos = create_user(admin_client, "acotado", gateway_role="viewer")
    scoped_client = client_as(datos, "acotado")
    response = _grant(
        admin_client, datos["id"], "blueprints.approve_destructive", scope_id=env_id("development")
    )
    assert response.status_code in (201, 202), response.text

    r = _approve_blueprint(scoped_client, drop_blueprint, sql_hash="a" * 64)
    assert r.status_code == 403, r.text


# --------------------------------------------------------------------------- #
# Revocar                                                                      #
# --------------------------------------------------------------------------- #
def test_the_approver_revokes_and_the_status_becomes_revoked(admin_client, drop_blueprint):
    approver = _second_owner(admin_client, "aprobador")
    approval_id = _approve_blueprint(approver, drop_blueprint).json()["data"]["id"]

    r = _revoke(approver, approval_id)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["is_live"] is False
    assert data["revoked_by"]["username"] == "aprobador"
    assert data["revoke_reason"] == "Ya no corresponde ejecutarla"
    assert _version_row(admin_client, drop_blueprint, "0001")["approval_status"] == "revoked"


def test_another_owner_can_revoke_an_approval_they_did_not_grant(admin_client, drop_blueprint):
    approver = _second_owner(admin_client, "aprobador")
    approval_id = _approve_blueprint(approver, drop_blueprint).json()["data"]["id"]
    r = _revoke(admin_client, approval_id)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["revoked_by"]["username"] != "aprobador"


def test_an_actor_without_the_capability_cannot_revoke(admin_client, drop_blueprint):
    approver = _second_owner(admin_client, "aprobador")
    approval_id = _approve_blueprint(approver, drop_blueprint).json()["data"]["id"]
    viewer = _viewer(admin_client)
    r = _revoke(viewer, approval_id)
    assert r.status_code == 403, r.text
    assert _code(r) == CODE_FORBIDDEN
    assert _version_row(admin_client, drop_blueprint, "0001")["approval_status"] == "approved"


def test_the_approver_still_revokes_after_losing_the_capability(admin_client, drop_blueprint):
    """Enmienda A-2: quien aprobó puede frenar su propio error aunque ya no sea owner."""
    approver_data = create_user(admin_client, "aprobador", gateway_role="owner")
    approver = client_as(approver_data, "aprobador", password=APPROVER_PASSWORD)
    approval_id = _approve_blueprint(approver, drop_blueprint).json()["data"]["id"]

    demoted = admin_client.patch(
        f"/api/v1/gateway-users/{approver_data['id']}", json={"gateway_role": "viewer"}
    )
    assert demoted.status_code == 200, demoted.text
    assert demoted.json()["data"]["gateway_role"] == "viewer"

    # Cambiar el rol invalida la sesión: en la práctica, quien aprobó vuelve a entrar como viewer.
    assert _revoke(approver, approval_id).status_code == 401
    login = approver.post(
        "/api/v1/auth/login", json={"username": "aprobador", "password": APPROVER_PASSWORD}
    )
    assert login.status_code == 200, login.text
    attach_csrf(approver)

    r = _revoke(approver, approval_id)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["revoked_by"]["username"] == "aprobador"
    assert _version_row(admin_client, drop_blueprint, "0001")["approval_status"] == "revoked"


def test_a_machine_actor_is_never_the_approver_of_record():
    """Un actor de integración lleva el ``id`` de su emisor: no hereda el permiso de revocar."""
    machine = SimpleNamespace(is_machine=True, id=1, username="integracion")
    assert DestructiveApprovalController.is_approver_of_record(machine, 1) is False


def test_revoking_is_idempotent_and_keeps_the_first_revoker(admin_client, drop_blueprint):
    approver = _second_owner(admin_client, "aprobador")
    approval_id = _approve_blueprint(approver, drop_blueprint).json()["data"]["id"]

    first = _revoke(approver, approval_id).json()["data"]
    again = _revoke(admin_client, approval_id, reason="Otro motivo distinto al primero")
    assert again.status_code == 200, again.text
    again_data = again.json()["data"]
    assert again_data["revoked_by"] == first["revoked_by"]
    assert again_data["revoked_at"] == first["revoked_at"]
    assert again_data["revoke_reason"] == first["revoke_reason"]
    assert len(_audits("migration.destructive_approval.revoke")) == 1


def test_revoking_needs_no_step_up(admin_client, drop_blueprint, expire_step_up):
    approver = _second_owner(admin_client, "aprobador")
    approval_id = _approve_blueprint(approver, drop_blueprint).json()["data"]["id"]
    expire_step_up(approver)
    r = _revoke(approver, approval_id)
    assert r.status_code == 200, r.text


def test_revoking_an_unknown_approval_is_404(admin_client):
    r = _revoke(admin_client, 999999)
    assert r.status_code in (403, 404), r.text
    if r.status_code == 404:
        assert _code(r) == "destructive_approval.not_found"


# --------------------------------------------------------------------------- #
# Auditoría                                                                    #
# --------------------------------------------------------------------------- #
def test_approving_and_revoking_leave_an_audit_entry_without_the_free_text(
    admin_client, drop_blueprint
):
    approver = _second_owner(admin_client, "aprobador")
    approval_id = _approve_blueprint(approver, drop_blueprint).json()["data"]["id"]
    _revoke(approver, approval_id)

    (granted,) = _audits("migration.destructive_approval.grant")
    (revoked,) = _audits("migration.destructive_approval.revoke")
    assert granted.admin_username == "aprobador"
    assert revoked.admin_username == "aprobador"
    for entry in (granted, revoked):
        assert REASON not in (entry.detail or "")
        assert DROP_SQL not in (entry.detail or "")
        json.loads(entry.detail)
