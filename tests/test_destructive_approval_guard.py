"""
Guard de ejecución de migraciones destructivas con aprobación (apply, apply-all y rollback).

Lo que decide el negocio y se mide acá:

- En un entorno que bloquea lo destructivo, una versión destructiva se ejecuta SOLO con una
  aprobación vigente (hash coincidente) + ``apply_destructive`` + ``confirm_token`` del dry-run +
  re-tipeo del nombre de la base. ``force`` no interviene.
- ``apply_all`` es TODO O NADA: una sola versión sin aprobar frena el lote entero antes de tocar
  la primera base; el re-tipeo es el slug del entorno; más de un entorno protegido es 422.
- ``rollback`` exige una aprobación de dirección ``down`` (la de ``up`` no la cubre).
- Una máquina (integración) conserva el bloqueo duro aunque la versión esté aprobada.

El motor se mockea (``get_current_version``, ``apply``, ``rollback_to``) como en el resto de la
suite: se verifica CUÁNTAS veces se ejecutó, no el SQL.

Verificar con ``.venv/bin/python scripts/run_tests_direct.py tests.test_destructive_approval_guard``.
"""

import pytest

from app.controllers.managed_migration_controller import ManagedMigrationController
from app.core.actor import Actor
from app.exceptions import AppHttpException
from app.services.capability_catalog import Capability
from app.services.db_admin.migrations import MigrationRunner
from tests.step_up_helpers import OPEN_WINDOW
from tests.test_api_gateway_users import _code
from tests.test_destructive_approval_api import (
    _approve_blueprint,
    _approve_database,
    _edit_up_sql,
    _revoke,
    _second_owner,
)
from tests.test_environment_guard import (
    DROP_SQL,
    SAFE_SQL,
    _blueprint,
    _db,
    _env_ids,
    _owner,
    _server,
)

CODE_BLOCKED = "environment.destructive_blocked"
CODE_REQUIRED = "destructive_approval.confirmation_required"
CODE_INVALID = "destructive_approval.confirmation_invalid"
CODE_EXPIRED = "destructive_approval.confirmation_expired"
CODE_PHRASE = "destructive_approval.confirm_phrase_mismatch"
CODE_MULTI_ENV = "destructive_approval.batch_multi_environment"
CODE_EXECUTOR = "destructive_approval.executor_forbidden"
DOWN_DROP_SQL = "DROP TABLE clientes"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
class _EngineSpy:
    """Registra qué bases ejecutaron apply / rollback_to y deja la BD "recién creada"."""

    def __init__(self, monkeypatch, current=None):
        self.applied_on: list[str] = []
        self.rolled_back_on: list[str] = []
        self.current = current
        monkeypatch.setattr(
            MigrationRunner, "get_current_version", lambda runner, *a, **k: self.current
        )
        monkeypatch.setattr(MigrationRunner, "apply", self._apply)
        monkeypatch.setattr(MigrationRunner, "rollback_to", self._rollback_to)

    # Sin parámetro ``runner``: un método YA LIGADO a este spy, puesto en la clase, no se vuelve a
    # ligar a la instancia de ``MigrationRunner``, así que Python no le pasa el runner.
    def _apply(self, target, *, db_name, **kwargs):
        self.applied_on.append(db_name)
        return []

    def _rollback_to(self, target, *, db_name, **kwargs):
        self.rolled_back_on.append(db_name)
        return []


def _protected_database(
    admin_client, server_payload, *, sql=DROP_SQL, name="proddb", env="production",
    blueprint_slug="bp-guard", port=3399, server_name="srv-env",
):
    """``(model_id, db_id)`` de una base en el entorno indicado con la versión 0001 ``sql``."""
    model_id = _blueprint(
        admin_client, blueprint_slug, [{"version": "0001", "name": "m", "up_sql": sql}]
    )
    server_id = _server(admin_client, server_payload, name=server_name, port=port)
    owner_id = _owner(admin_client, server_id)
    db_id = _db(
        admin_client, server_id=server_id, owner_id=owner_id, model_id=model_id, name=name,
        environment_id=_env_ids(admin_client)[env],
    )
    return model_id, db_id


def _plan(client, db_id: int, **params) -> dict:
    r = client.post(
        f"/api/v1/managed-databases/{db_id}/migrations/apply", params={"dry_run": "true", **params}
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _apply(client, db_id: int, **params):
    return client.post(f"/api/v1/managed-databases/{db_id}/migrations/apply", params=params)


def _approved_database(admin_client, server_payload, monkeypatch, **kwargs):
    """Base protegida con la versión 0001 aprobada (alcance base) por una segunda persona."""
    model_id, db_id = _protected_database(admin_client, server_payload, **kwargs)
    approver = _second_owner(admin_client, "aprobador")
    r = _approve_database(approver, db_id, model_id)
    assert r.status_code == 201, r.text
    return model_id, db_id, approver


# --------------------------------------------------------------------------- #
# apply por base                                                                #
# --------------------------------------------------------------------------- #
def test_approved_version_with_token_and_name_executes(
    admin_client, server_payload, monkeypatch
):
    spy = _EngineSpy(monkeypatch)
    _model_id, db_id, _approver = _approved_database(admin_client, server_payload, monkeypatch)

    plan = _plan(admin_client, db_id)
    destructive_plan = plan["destructive_plan"]
    assert destructive_plan["all_approved"] is True
    assert destructive_plan["executor_allowed"] is True
    assert destructive_plan["confirm_name_expected"] == "proddb"
    assert [v["approval_status"] for v in destructive_plan["versions"]] == ["approved"]
    assert plan["blocked_by"] == []

    r = _apply(
        admin_client, db_id,
        confirm_token=destructive_plan["confirm_token"], confirm_name="proddb",
    )
    assert r.status_code == 200, r.text
    assert spy.applied_on == ["proddb"]


def test_unapproved_version_is_blocked_and_the_plan_has_no_token(
    admin_client, server_payload, monkeypatch
):
    spy = _EngineSpy(monkeypatch)
    _model_id, db_id = _protected_database(admin_client, server_payload)

    plan = _plan(admin_client, db_id)
    assert plan["destructive_plan"]["all_approved"] is False
    assert plan["destructive_plan"]["confirm_token"] is None
    assert [v["approval_status"] for v in plan["destructive_plan"]["versions"]] == ["unapproved"]
    assert plan["blocked_by"] == ["0001"]

    r = _apply(admin_client, db_id)
    assert r.status_code == 409, r.text
    public_context = r.json()["detail"]["public_context"]
    assert public_context["code"] == CODE_BLOCKED
    assert public_context["direction"] == "up"
    assert public_context["approved_versions"] == []
    assert spy.applied_on == []


def test_approved_version_without_token_asks_for_confirmation(
    admin_client, server_payload, monkeypatch
):
    spy = _EngineSpy(monkeypatch)
    _model_id, db_id, _approver = _approved_database(admin_client, server_payload, monkeypatch)
    r = _apply(admin_client, db_id)
    assert r.status_code == 409, r.text
    assert _code(r) == CODE_REQUIRED
    assert spy.applied_on == []


def test_token_issued_for_another_target_is_invalid(admin_client, server_payload, monkeypatch):
    """El token del plan "hasta head" no sirve para ``?version=0001``: el destino va en el subject."""
    spy = _EngineSpy(monkeypatch)
    _model_id, db_id, _approver = _approved_database(admin_client, server_payload, monkeypatch)
    token = _plan(admin_client, db_id)["destructive_plan"]["confirm_token"]

    r = _apply(admin_client, db_id, version="0001", confirm_token=token, confirm_name="proddb")
    assert r.status_code == 422, r.text
    assert _code(r) == CODE_INVALID
    assert spy.applied_on == []


def test_expired_token_is_410(admin_client, server_payload, monkeypatch):
    spy = _EngineSpy(monkeypatch)
    _model_id, db_id, _approver = _approved_database(admin_client, server_payload, monkeypatch)
    # Un TTL negativo emite un token AUTÉNTICO ya vencido (la firma se verifica antes que la expiración).
    monkeypatch.setattr(
        "app.controllers.managed_migration_controller.DESTRUCTIVE_CONFIRM_TTL_SECONDS", -10
    )
    token = _plan(admin_client, db_id)["destructive_plan"]["confirm_token"]

    r = _apply(admin_client, db_id, confirm_token=token, confirm_name="proddb")
    assert r.status_code == 410, r.text
    assert _code(r) == CODE_EXPIRED
    assert spy.applied_on == []


def test_wrong_retyped_name_is_refused(admin_client, server_payload, monkeypatch):
    """El token da frescura, no intención: el servidor valida el re-tipeo del nombre."""
    spy = _EngineSpy(monkeypatch)
    _model_id, db_id, _approver = _approved_database(admin_client, server_payload, monkeypatch)
    token = _plan(admin_client, db_id)["destructive_plan"]["confirm_token"]

    for wrong_name in (None, "PRODDB", "otra_base"):
        params = {"confirm_token": token}
        if wrong_name is not None:
            params["confirm_name"] = wrong_name
        r = _apply(admin_client, db_id, **params)
        assert r.status_code == 422, r.text
        assert _code(r) == CODE_PHRASE
    assert spy.applied_on == []


def test_revoking_after_the_plan_blocks_the_execution(admin_client, server_payload, monkeypatch):
    """TOCTOU: el token se emitió con la aprobación vigente, pero revocarla lo invalida todo."""
    spy = _EngineSpy(monkeypatch)
    model_id, db_id = _protected_database(admin_client, server_payload)
    approver = _second_owner(admin_client, "aprobador")
    approval_id = _approve_database(approver, db_id, model_id).json()["data"]["id"]
    token = _plan(admin_client, db_id)["destructive_plan"]["confirm_token"]

    assert _revoke(approver, approval_id).status_code == 200

    r = _apply(admin_client, db_id, confirm_token=token, confirm_name="proddb")
    assert r.status_code == 409, r.text
    assert _code(r) == CODE_BLOCKED
    assert r.json()["detail"]["public_context"]["approval_status"] == {"0001": "revoked"}
    assert spy.applied_on == []


def test_editing_the_sql_after_approval_blocks_the_execution(
    admin_client, server_payload, monkeypatch
):
    spy = _EngineSpy(monkeypatch)
    model_id, db_id, _approver = _approved_database(admin_client, server_payload, monkeypatch)
    token = _plan(admin_client, db_id)["destructive_plan"]["confirm_token"]
    _edit_up_sql(admin_client, model_id, "0001", "DROP TABLE otra_tabla")

    r = _apply(admin_client, db_id, confirm_token=token, confirm_name="proddb")
    assert r.status_code == 409, r.text
    assert _code(r) == CODE_BLOCKED
    assert r.json()["detail"]["public_context"]["approval_status"] == {"0001": "stale_hash"}
    assert spy.applied_on == []


def test_force_does_not_bypass_an_approved_but_unconfirmed_version(
    admin_client, server_payload, monkeypatch
):
    spy = _EngineSpy(monkeypatch)
    _model_id, db_id, _approver = _approved_database(admin_client, server_payload, monkeypatch)
    r = _apply(admin_client, db_id, force="true")
    assert r.status_code == 409, r.text
    assert _code(r) == CODE_REQUIRED
    assert spy.applied_on == []


def test_non_blocking_environment_is_unchanged(admin_client, server_payload, monkeypatch):
    spy = _EngineSpy(monkeypatch)
    _model_id, db_id = _protected_database(admin_client, server_payload, env="development")
    plan = _plan(admin_client, db_id)
    assert plan["destructive_plan"] is None
    r = _apply(admin_client, db_id)
    assert r.status_code == 200, r.text
    assert spy.applied_on == ["proddb"]


def test_safe_version_in_a_protected_environment_needs_no_confirmation(
    admin_client, server_payload, monkeypatch
):
    spy = _EngineSpy(monkeypatch)
    _model_id, db_id = _protected_database(admin_client, server_payload, sql=SAFE_SQL)
    assert _plan(admin_client, db_id)["destructive_plan"] is None
    r = _apply(admin_client, db_id)
    assert r.status_code == 200, r.text
    assert spy.applied_on == ["proddb"]


def test_executor_without_apply_destructive_is_forbidden(
    admin_client, server_payload, monkeypatch
):
    """Con la versión aprobada, quien no tiene ``apply_destructive`` recibe 403 con código propio."""
    spy = _EngineSpy(monkeypatch)
    _model_id, db_id, _approver = _approved_database(admin_client, server_payload, monkeypatch)
    executor_without_capability = Actor(
        kind="admin",
        id=987654,
        username="sin_capacidad",
        capabilities=frozenset({Capability.BLUEPRINTS_APPLY}),
        step_up_until=OPEN_WINDOW,
    )

    with pytest.raises(AppHttpException) as raised:
        ManagedMigrationController().apply(
            db_id, admin=executor_without_capability, confirm_token="x", confirm_name="proddb"
        )
    assert raised.value.status_code == 403
    assert raised.value.public_context["code"] == CODE_EXECUTOR
    assert spy.applied_on == []


def test_machine_actor_stays_blocked_even_when_approved(
    admin_client, server_payload, monkeypatch
):
    """Una integración no consume aprobaciones: conserva el bloqueo duro de siempre."""
    spy = _EngineSpy(monkeypatch)
    _model_id, db_id, _approver = _approved_database(admin_client, server_payload, monkeypatch)
    machine = Actor(
        kind="integration",
        id=1,
        username="token-ci",
        capabilities=frozenset({Capability.BLUEPRINTS_APPLY, Capability.BLUEPRINTS_APPLY_DESTRUCTIVE}),
        step_up_until=OPEN_WINDOW,
    )

    with pytest.raises(AppHttpException) as raised:
        ManagedMigrationController().apply(db_id, admin=machine)
    assert raised.value.status_code == 409
    assert raised.value.public_context["code"] == CODE_BLOCKED
    assert raised.value.public_context["blocked_versions"] == ["0001"]
    assert spy.applied_on == []


def test_machine_actor_dry_run_never_receives_a_token(admin_client, server_payload, monkeypatch):
    _EngineSpy(monkeypatch)
    _model_id, db_id, _approver = _approved_database(admin_client, server_payload, monkeypatch)
    machine = Actor(
        kind="integration", id=1, username="token-ci",
        capabilities=frozenset({Capability.BLUEPRINTS_APPLY}), step_up_until=OPEN_WINDOW,
    )
    plan = ManagedMigrationController().apply(db_id, admin=machine, dry_run=True)
    assert plan["destructive_plan"]["confirm_token"] is None
    assert plan["destructive_plan"]["executor_allowed"] is False
    assert plan["blocked_by"] == ["0001"]


def test_database_without_environment_stays_permissive(admin_client, server_payload, monkeypatch):
    spy = _EngineSpy(monkeypatch)
    _model_id, db_id = _protected_database(admin_client, server_payload)
    r = admin_client.patch(f"/api/v1/managed-databases/{db_id}", json={"environment_id": None})
    assert r.status_code == 200, r.text
    r = _apply(admin_client, db_id)
    assert r.status_code == 200, r.text
    assert spy.applied_on == ["proddb"]


# --------------------------------------------------------------------------- #
# apply-all: todo o nada                                                        #
# --------------------------------------------------------------------------- #
def _batch(admin_client, server_payload, database_count=3, *, env="production"):
    """Un blueprint con 0001 destructiva y ``database_count`` bases en ``env``."""
    model_id = _blueprint(
        admin_client, "bp-batch", [{"version": "0001", "name": "m", "up_sql": DROP_SQL}]
    )
    server_id = _server(admin_client, server_payload, name="srv-batch", port=3401)
    owner_id = _owner(admin_client, server_id)
    env_id = _env_ids(admin_client)[env]
    db_ids = [
        _db(
            admin_client, server_id=server_id, owner_id=owner_id, model_id=model_id,
            name=f"batch_db_{index}", environment_id=env_id,
        )
        for index in range(database_count)
    ]
    return model_id, db_ids


def _apply_all(client, model_id: int, **params):
    return client.post(f"/api/v1/database-models/{model_id}/migrations/apply-all", params=params)


def test_batch_with_one_unapproved_database_executes_nothing(
    admin_client, server_payload, monkeypatch
):
    spy = _EngineSpy(monkeypatch)
    model_id, db_ids = _batch(admin_client, server_payload)
    approver = _second_owner(admin_client, "aprobador")
    for approved_db in db_ids[:2]:
        assert _approve_database(approver, approved_db, model_id).status_code == 201

    r = _apply_all(admin_client, model_id)
    assert r.status_code == 409, r.text
    public_context = r.json()["detail"]["public_context"]
    assert public_context["code"] == CODE_BLOCKED
    assert [d["managed_database_id"] for d in public_context["blocked_databases"]] == [db_ids[2]]
    assert spy.applied_on == []

    plan = _apply_all(admin_client, model_id, dry_run="true").json()["data"]
    confirmation = plan["destructive_confirmation"]
    assert confirmation["all_approved"] is False
    assert confirmation["confirm_token"] is None
    assert confirmation["environment_slug"] == "production"


def test_blueprint_wide_approval_lets_the_confirmed_batch_run(
    admin_client, server_payload, monkeypatch
):
    spy = _EngineSpy(monkeypatch)
    model_id, db_ids = _batch(admin_client, server_payload)
    approver = _second_owner(admin_client, "aprobador")
    assert _approve_blueprint(approver, model_id).status_code == 201

    confirmation = _apply_all(admin_client, model_id, dry_run="true").json()["data"][
        "destructive_confirmation"
    ]
    assert confirmation["all_approved"] is True
    assert confirmation["executor_allowed"] is True
    assert confirmation["environment_slug"] == "production"
    assert confirmation["confirm_token"]

    r = _apply_all(
        admin_client, model_id,
        confirm_token=confirmation["confirm_token"], confirm_phrase="production",
    )
    assert r.status_code == 200, r.text
    results = r.json()["data"]["results"]
    assert all(item["ok"] for item in results), results
    assert sorted(spy.applied_on) == sorted(f"batch_db_{i}" for i in range(len(db_ids)))


def test_batch_without_token_or_with_wrong_phrase_executes_nothing(
    admin_client, server_payload, monkeypatch
):
    spy = _EngineSpy(monkeypatch)
    model_id, _db_ids = _batch(admin_client, server_payload)
    approver = _second_owner(admin_client, "aprobador")
    assert _approve_blueprint(approver, model_id).status_code == 201
    token = _apply_all(admin_client, model_id, dry_run="true").json()["data"][
        "destructive_confirmation"
    ]["confirm_token"]

    without_token = _apply_all(admin_client, model_id)
    assert without_token.status_code == 409, without_token.text
    assert _code(without_token) == CODE_REQUIRED

    wrong_phrase = _apply_all(
        admin_client, model_id, confirm_token=token, confirm_phrase="staging"
    )
    assert wrong_phrase.status_code == 422, wrong_phrase.text
    assert _code(wrong_phrase) == CODE_PHRASE
    assert spy.applied_on == []


def test_batch_token_dies_when_an_approval_is_revoked_after_the_dry_run(
    admin_client, server_payload, monkeypatch
):
    spy = _EngineSpy(monkeypatch)
    model_id, _db_ids = _batch(admin_client, server_payload)
    approver = _second_owner(admin_client, "aprobador")
    approval_id = _approve_blueprint(approver, model_id).json()["data"]["id"]
    token = _apply_all(admin_client, model_id, dry_run="true").json()["data"][
        "destructive_confirmation"
    ]["confirm_token"]
    assert _revoke(approver, approval_id).status_code == 200

    r = _apply_all(admin_client, model_id, confirm_token=token, confirm_phrase="production")
    assert r.status_code == 409, r.text
    assert _code(r) == CODE_BLOCKED
    assert spy.applied_on == []


def test_batch_spanning_two_protected_environments_is_422(
    admin_client, server_payload, monkeypatch
):
    spy = _EngineSpy(monkeypatch)
    created = admin_client.post(
        "/api/v1/environments",
        json={
            "name": "Produccion EU", "slug": "production-eu", "rank": 90,
            "blocks_destructive_migrations": True,
        },
    )
    assert created.status_code == 201, created.text

    model_id = _blueprint(
        admin_client, "bp-multi", [{"version": "0001", "name": "m", "up_sql": DROP_SQL}]
    )
    server_id = _server(admin_client, server_payload, name="srv-multi", port=3402)
    owner_id = _owner(admin_client, server_id)
    envs = _env_ids(admin_client)
    for index, slug in enumerate(("production", "production-eu")):
        _db(
            admin_client, server_id=server_id, owner_id=owner_id, model_id=model_id,
            name=f"multi_db_{index}", environment_id=envs[slug],
        )
    approver = _second_owner(admin_client, "aprobador")
    assert _approve_blueprint(approver, model_id).status_code == 201

    plan = _apply_all(admin_client, model_id, dry_run="true").json()["data"]
    assert plan["destructive_confirmation"]["confirm_token"] is None
    assert plan["destructive_confirmation"]["environment_slugs"] == ["production", "production-eu"]

    r = _apply_all(admin_client, model_id, confirm_token="x", confirm_phrase="production")
    assert r.status_code == 422, r.text
    assert _code(r) == CODE_MULTI_ENV
    assert spy.applied_on == []


def test_batch_of_machine_actor_fails_per_item_as_blocked(
    admin_client, server_payload, monkeypatch
):
    spy = _EngineSpy(monkeypatch)
    model_id, db_ids = _batch(admin_client, server_payload, database_count=1)
    approver = _second_owner(admin_client, "aprobador")
    assert _approve_blueprint(approver, model_id).status_code == 201
    machine = Actor(
        kind="integration", id=1, username="token-ci",
        capabilities=frozenset({Capability.BLUEPRINTS_APPLY}), step_up_until=OPEN_WINDOW,
    )

    result = ManagedMigrationController().apply_all(model_id, max_databases=10, admin=machine)
    (item,) = result["results"]
    assert item["managed_database_id"] == db_ids[0]
    assert item["ok"] is False
    assert item["error_code"] == CODE_BLOCKED
    assert spy.applied_on == []


# --------------------------------------------------------------------------- #
# rollback                                                                      #
# --------------------------------------------------------------------------- #
def _rollback(client, db_id: int, **params):
    return client.post(f"/api/v1/managed-databases/{db_id}/migrations/rollback", params=params)


def _version_row_down(client, model_id: int, version: str = "0001") -> dict:
    r = client.get(f"/api/v1/database-models/{model_id}/destructive-versions")
    assert r.status_code == 200, r.text
    (row,) = [x for x in r.json()["data"] if x["version"] == version and x["direction"] == "down"]
    return row


def _protected_database_with_down(
    admin_client, server_payload, *, down_sql=DOWN_DROP_SQL, up_sql=SAFE_SQL, env="production"
):
    model_id = _blueprint(
        admin_client, "bp-down",
        [{"version": "0001", "name": "m", "up_sql": up_sql, "down_sql": down_sql}],
    )
    server_id = _server(admin_client, server_payload, name="srv-down", port=3403)
    owner_id = _owner(admin_client, server_id)
    db_id = _db(
        admin_client, server_id=server_id, owner_id=owner_id, model_id=model_id,
        name="downdb", environment_id=_env_ids(admin_client)[env],
    )
    return model_id, db_id


def _approve_down(client, db_id, model_id):
    sql_hash = _version_row_down(client, model_id)["sql_hash"]
    return client.post(
        f"/api/v1/managed-databases/{db_id}/migrations/0001/destructive-approvals",
        json={
            "direction": "down", "expected_sql_hash": sql_hash,
            "reason": "Revisado el rollback con el equipo de datos",
        },
    )


def test_destructive_down_without_a_down_approval_is_blocked(
    admin_client, server_payload, monkeypatch
):
    spy = _EngineSpy(monkeypatch, current="0001")
    model_id, db_id = _protected_database_with_down(admin_client, server_payload)

    r = _rollback(admin_client, db_id, confirm_version="0001")
    assert r.status_code == 409, r.text
    public_context = r.json()["detail"]["public_context"]
    assert public_context["code"] == CODE_BLOCKED
    assert public_context["direction"] == "down"
    assert spy.rolled_back_on == []


def test_an_up_approval_does_not_cover_the_rollback(admin_client, server_payload, monkeypatch):
    """Otro SQL, otro hash: aprobar la subida no autoriza la bajada."""
    spy = _EngineSpy(monkeypatch, current="0001")
    model_id, db_id = _protected_database_with_down(
        admin_client, server_payload, up_sql=DROP_SQL
    )
    approver = _second_owner(admin_client, "aprobador")
    assert _approve_database(approver, db_id, model_id, direction="up").status_code == 201

    rollback = _rollback(admin_client, db_id, confirm_version="0001")
    assert rollback.status_code == 409, rollback.text
    assert _code(rollback) == CODE_BLOCKED
    assert spy.rolled_back_on == []


def test_down_approved_with_capability_and_token_rolls_back(
    admin_client, server_payload, monkeypatch
):
    spy = _EngineSpy(monkeypatch, current="0001")
    model_id, db_id = _protected_database_with_down(admin_client, server_payload)
    approver = _second_owner(admin_client, "aprobador")
    assert _approve_down(approver, db_id, model_id).status_code == 201

    plan = _rollback(admin_client, db_id, dry_run="true")
    assert plan.status_code == 200, plan.text
    data = plan.json()["data"]
    assert data["dry_run"] is True
    assert data["rollback_path_versions"] == ["0001"]
    assert data["environment_slug"] == "production"
    assert data["destructive_plan"]["direction"] == "down"
    assert data["destructive_plan"]["all_approved"] is True
    assert spy.rolled_back_on == []  # el plan no toca el motor

    r = _rollback(
        admin_client, db_id, confirm_version="0001",
        confirm_token=data["destructive_plan"]["confirm_token"], confirm_name="downdb",
    )
    assert r.status_code == 200, r.text
    assert spy.rolled_back_on == ["downdb"]


def test_rollback_without_token_or_name_is_refused(admin_client, server_payload, monkeypatch):
    spy = _EngineSpy(monkeypatch, current="0001")
    model_id, db_id = _protected_database_with_down(admin_client, server_payload)
    approver = _second_owner(admin_client, "aprobador")
    assert _approve_down(approver, db_id, model_id).status_code == 201
    token = _rollback(admin_client, db_id, dry_run="true").json()["data"]["destructive_plan"][
        "confirm_token"
    ]

    without_token = _rollback(admin_client, db_id, confirm_version="0001")
    assert without_token.status_code == 409, without_token.text
    assert _code(without_token) == CODE_REQUIRED

    wrong_name = _rollback(
        admin_client, db_id, confirm_version="0001", confirm_token=token, confirm_name="otra"
    )
    assert wrong_name.status_code == 422, wrong_name.text
    assert _code(wrong_name) == CODE_PHRASE
    assert spy.rolled_back_on == []


def test_rollback_without_confirm_version_is_422_unless_dry_run(
    admin_client, server_payload, monkeypatch
):
    _EngineSpy(monkeypatch, current="0001")
    _model_id, db_id = _protected_database_with_down(admin_client, server_payload, env="development")
    r = _rollback(admin_client, db_id)
    assert r.status_code == 422, r.text
    assert _rollback(admin_client, db_id, dry_run="true").status_code == 200


def test_non_destructive_down_is_unchanged(admin_client, server_payload, monkeypatch):
    spy = _EngineSpy(monkeypatch, current="0001")
    _model_id, db_id = _protected_database_with_down(
        admin_client, server_payload, down_sql="SELECT 1"
    )
    plan = _rollback(admin_client, db_id, dry_run="true").json()["data"]
    assert plan["destructive_plan"] is None
    r = _rollback(admin_client, db_id, confirm_version="0001")
    assert r.status_code == 200, r.text
    assert spy.rolled_back_on == ["downdb"]


# --------------------------------------------------------------------------- #
# Llamadores internos: nunca consumen aprobaciones                              #
# --------------------------------------------------------------------------- #
def test_internal_callers_without_token_never_consume_an_approval(
    admin_client, server_payload, monkeypatch
):
    """
    Alta de BD, adopción e integración llaman a ``apply`` sin token: aunque la versión esté
    aprobada y quien llama sea una persona con la capacidad, reciben ``confirmation_required`` y
    no se ejecuta nada.
    """
    spy = _EngineSpy(monkeypatch)
    _model_id, db_id, _approver = _approved_database(admin_client, server_payload, monkeypatch)
    with pytest.raises(AppHttpException) as raised:
        ManagedMigrationController().apply(
            db_id,
            admin=Actor(
                kind="admin", id=1, username="admin",
                capabilities=frozenset(
                    {Capability.BLUEPRINTS_APPLY, Capability.BLUEPRINTS_APPLY_DESTRUCTIVE}
                ),
                step_up_until=OPEN_WINDOW,
            ),
        )
    assert raised.value.status_code == 409
    assert raised.value.public_context["code"] == CODE_REQUIRED
    assert spy.applied_on == []
