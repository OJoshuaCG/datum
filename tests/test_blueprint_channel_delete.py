"""
Borrar una versión con canales de liberación apuntándole.

Contrato que fijan estos tests:

- Un puntero sobre la versión borrada pasa a la versión REVISADA más cercana por debajo; si no
  hay ninguna, queda en NULL. Nunca queda un puntero sobre una fila borrada.
- Un puntero sobre OTRA fila no se mueve y sobrevive al renumerado (apunta por id, no por
  número).
- Los canales nunca bloquean el borrado, pero si hay que moverlos el DELETE exige el
  ``confirm_token`` del preview, y los movimientos entran en su huella: un canal que cambia
  entre el preview y la ejecución invalida el token.
- Cada movimiento deja un evento ``version_deleted`` en la bitácora, atómico con el borrado.

El motor se mockea con los dobles de ``test_api_migration_delete_renumber``; la BD de metadatos
(SQLite) NO, porque ahí viven el UNIQUE ``(model_id, version)`` y los punteros.

Estos tests NO se corren automáticamente en este repo; se verifican con
``.venv/bin/python scripts/run_tests_direct.py tests.test_blueprint_channel_delete``.
"""

import pytest

from app.controllers.model_migration_controller import ModelMigrationController
from app.core.database import Database
from app.exceptions import AppHttpException
from app.models.database_model import DatabaseModel
from app.models.model_migration import ModelMigration
from app.models.release_channel import (
    BlueprintChannelPointer,
    BlueprintChannelPointerEvent,
    ReleaseChannel,
)
from tests.test_api_migration_delete_renumber import _blueprint, _engine, _managed_db

MODELS_URL = "/api/v1/database-models"
CHANNELS_URL = "/api/v1/release-channels"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _public_context(response) -> dict:
    return response.json()["detail"]["public_context"]


def _promote(client, model_id: int, channel_slug: str, version: str):
    response = client.post(
        f"{MODELS_URL}/{model_id}/channels/{channel_slug}/promote", json={"version": version}
    )
    assert response.status_code == 200, response.text


def _mark_unreviewed(client, model_id: int, version: str) -> None:
    response = client.patch(
        f"{MODELS_URL}/{model_id}/migrations/{version}", json={"reviewed": False}
    )
    assert response.status_code == 200, response.text


def _create_candidate_channel(client) -> None:
    response = client.post(
        CHANNELS_URL, json={"name": "Candidato", "slug": "candidate", "resolution_mode": "manual"}
    )
    assert response.status_code == 201, response.text


def _delete_plan(client, model_id: int, version: str) -> dict:
    response = client.get(f"{MODELS_URL}/{model_id}/migrations/{version}/delete-plan")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _delete(client, model_id: int, version: str, token: str | None = None):
    params = {"confirm_token": token} if token else {}
    return client.delete(f"{MODELS_URL}/{model_id}/migrations/{version}", params=params)


def _pointer_version(model_id: int, channel_slug: str) -> str | None:
    session = Database().get_declarative_base_session()
    try:
        channel = session.query(ReleaseChannel).filter(ReleaseChannel.slug == channel_slug).one()
        pointer = session.get(
            BlueprintChannelPointer,
            {"database_model_id": model_id, "release_channel_id": channel.id},
        )
        assert pointer is not None, "el puntero no debe borrarse: queda con NULL si no hay destino"
        if pointer.model_migration_id is None:
            return None
        return session.get(ModelMigration, pointer.model_migration_id).version
    finally:
        session.close()


def _pointer_migration_id(model_id: int, channel_slug: str) -> int | None:
    session = Database().get_declarative_base_session()
    try:
        channel = session.query(ReleaseChannel).filter(ReleaseChannel.slug == channel_slug).one()
        pointer = session.get(
            BlueprintChannelPointer,
            {"database_model_id": model_id, "release_channel_id": channel.id},
        )
        return pointer.model_migration_id
    finally:
        session.close()


def _migration_id(model_id: int, version: str) -> int:
    session = Database().get_declarative_base_session()
    try:
        return (
            session.query(ModelMigration.id)
            .filter(ModelMigration.model_id == model_id, ModelMigration.version == version)
            .one()[0]
        )
    finally:
        session.close()


def _pointers_on(migration_id: int) -> int:
    session = Database().get_declarative_base_session()
    try:
        return (
            session.query(BlueprintChannelPointer)
            .filter(BlueprintChannelPointer.model_migration_id == migration_id)
            .count()
        )
    finally:
        session.close()


def _events(model_id: int) -> list[BlueprintChannelPointerEvent]:
    session = Database().get_declarative_base_session()
    try:
        rows = (
            session.query(BlueprintChannelPointerEvent)
            .filter(BlueprintChannelPointerEvent.database_model_id == model_id)
            .order_by(BlueprintChannelPointerEvent.id.asc())
            .all()
        )
        session.expunge_all()
        return rows
    finally:
        session.close()


def _versions(client, model_id: int) -> list[str]:
    response = client.get(f"{MODELS_URL}/{model_id}/migrations?size=50")
    assert response.status_code == 200, response.text
    return [row["version"] for row in response.json()["data"]]


def _delete_with_token(client, model_id: int, version: str) -> None:
    plan = _delete_plan(client, model_id, version)
    assert plan["confirm_token"], "se esperaba un token: el plan mueve canales"
    response = _delete(client, model_id, version, plan["confirm_token"])
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------- #
# Destino del puntero                                                          #
# --------------------------------------------------------------------------- #
def test_pointer_on_deleted_version_moves_to_previous_reviewed(admin_client):
    model_id = _blueprint(admin_client, "chdel-prev", ["0001", "0002", "0003", "0004"])
    _promote(admin_client, model_id, "stable", "0003")

    _delete_with_token(admin_client, model_id, "0003")

    assert _versions(admin_client, model_id) == ["0001", "0002", "0003"]
    assert _pointer_version(model_id, "stable") == "0002"


def test_pointer_skips_unreviewed_versions_when_moving_down(admin_client):
    model_id = _blueprint(admin_client, "chdel-skip", ["0001", "0002", "0003", "0004"])
    _mark_unreviewed(admin_client, model_id, "0002")
    _promote(admin_client, model_id, "stable", "0003")

    _delete_with_token(admin_client, model_id, "0003")

    # 0002 no está revisada: el puntero cae a 0001, no a la anterior a secas.
    assert _pointer_version(model_id, "stable") == "0001"


def test_pointer_becomes_null_when_no_reviewed_version_is_below(admin_client):
    model_id = _blueprint(admin_client, "chdel-null", ["0001", "0002", "0003"])
    _mark_unreviewed(admin_client, model_id, "0001")
    _promote(admin_client, model_id, "stable", "0002")

    _delete_with_token(admin_client, model_id, "0002")

    assert _pointer_version(model_id, "stable") is None


def test_pointer_on_first_version_becomes_null(admin_client):
    model_id = _blueprint(admin_client, "chdel-first", ["0001", "0002"])
    _promote(admin_client, model_id, "stable", "0001")

    _delete_with_token(admin_client, model_id, "0001")

    assert _versions(admin_client, model_id) == ["0001"]
    assert _pointer_version(model_id, "stable") is None


def test_no_pointer_is_left_on_the_deleted_row(admin_client):
    model_id = _blueprint(admin_client, "chdel-dangling", ["0001", "0002", "0003"])
    _create_candidate_channel(admin_client)
    _promote(admin_client, model_id, "stable", "0002")
    _promote(admin_client, model_id, "candidate", "0002")
    deleted_migration_id = _migration_id(model_id, "0002")

    _delete_with_token(admin_client, model_id, "0002")

    assert _pointers_on(deleted_migration_id) == 0
    assert _pointer_version(model_id, "stable") == "0001"
    assert _pointer_version(model_id, "candidate") == "0001"


def test_pointer_on_another_row_is_untouched_and_survives_renumber(admin_client):
    model_id = _blueprint(admin_client, "chdel-other", ["0001", "0002", "0003", "0004"])
    _promote(admin_client, model_id, "stable", "0004")
    pointed_migration_id = _migration_id(model_id, "0004")

    plan = _delete_plan(admin_client, model_id, "0002")
    assert plan["channel_moves"] == []
    assert plan["requires_confirmation"] is False

    # Sin canales que mover ni BDs adelante, el borrado sigue sin pedir token.
    response = _delete(admin_client, model_id, "0002")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["channel_moves"] == []

    # El puntero sigue sobre la MISMA fila, que ahora se llama 0003.
    assert _pointer_migration_id(model_id, "stable") == pointed_migration_id
    assert _pointer_version(model_id, "stable") == "0003"
    assert [event.event_type for event in _events(model_id)] == ["promote"]


# --------------------------------------------------------------------------- #
# Plan, token y confirmación                                                   #
# --------------------------------------------------------------------------- #
def test_delete_plan_lists_channel_moves(admin_client):
    model_id = _blueprint(admin_client, "chdel-plan", ["0001", "0002", "0003"])
    _promote(admin_client, model_id, "stable", "0003")

    plan = _delete_plan(admin_client, model_id, "0003")

    assert plan["deletable"] is True
    assert plan["requires_confirmation"] is True
    assert plan["confirm_token"]
    assert len(plan["channel_moves"]) == 1
    move = plan["channel_moves"][0]
    assert move["channel_slug"] == "stable"
    assert move["from_version"] == "0003"
    assert move["to_version"] == "0002"
    assert move["to_migration_id"] == _migration_id(model_id, "0002")
    assert any("stable" in warning for warning in plan["warnings"])


def test_delete_plan_reports_null_destination(admin_client):
    model_id = _blueprint(admin_client, "chdel-plan-null", ["0001", "0002"])
    _promote(admin_client, model_id, "stable", "0001")

    move = _delete_plan(admin_client, model_id, "0001")["channel_moves"][0]

    assert move["to_version"] is None
    assert move["to_migration_id"] is None


def test_plan_fingerprint_changes_with_channel_moves():
    states: list[dict] = []
    without_moves = ModelMigrationController._plan_fingerprint(1, "0003", states, [])
    move = {"release_channel_id": 1, "to_migration_id": 7}
    other_destination = {"release_channel_id": 1, "to_migration_id": 8}
    null_destination = {"release_channel_id": 1, "to_migration_id": None}

    with_move = ModelMigrationController._plan_fingerprint(1, "0003", states, [move])

    assert with_move != without_moves
    assert with_move != ModelMigrationController._plan_fingerprint(1, "0003", states, [other_destination])
    assert with_move != ModelMigrationController._plan_fingerprint(1, "0003", states, [null_destination])
    # Sin el argumento, la huella es la de siempre: los llamadores previos no cambian.
    assert ModelMigrationController._plan_fingerprint(1, "0003", states) == without_moves


def test_delete_without_token_when_channel_moves_is_409_with_moves(admin_client):
    model_id = _blueprint(admin_client, "chdel-notoken", ["0001", "0002"])
    _promote(admin_client, model_id, "stable", "0002")

    response = _delete(admin_client, model_id, "0002")  # la PUNTA, que antes no pedía token

    assert response.status_code == 409, response.text
    context = _public_context(response)
    assert context["code"] == "model_migration.renumber_confirmation_required"
    assert [move["channel_slug"] for move in context["channel_moves"]] == ["stable"]
    assert _versions(admin_client, model_id) == ["0001", "0002"]
    assert _pointer_version(model_id, "stable") == "0002"


def test_delete_tip_with_token_succeeds_and_moves_pointer(admin_client):
    model_id = _blueprint(admin_client, "chdel-tip", ["0001", "0002"])
    _promote(admin_client, model_id, "stable", "0002")

    plan = _delete_plan(admin_client, model_id, "0002")
    response = _delete(admin_client, model_id, "0002", plan["confirm_token"])

    assert response.status_code == 200, response.text
    moves = response.json()["data"]["channel_moves"]
    assert [(move["channel_slug"], move["to_version"]) for move in moves] == [("stable", "0001")]
    assert _versions(admin_client, model_id) == ["0001"]
    assert _pointer_version(model_id, "stable") == "0001"


def test_stale_token_when_a_channel_is_promoted_after_the_preview(admin_client):
    model_id = _blueprint(admin_client, "chdel-stale", ["0001", "0002", "0003"])
    _create_candidate_channel(admin_client)
    _promote(admin_client, model_id, "stable", "0003")
    plan = _delete_plan(admin_client, model_id, "0003")

    # Otro canal cae sobre la misma versión entre el preview y la ejecución.
    _promote(admin_client, model_id, "candidate", "0003")
    response = _delete(admin_client, model_id, "0003", plan["confirm_token"])

    assert response.status_code == 422, response.text  # el error de token estancado de siempre
    assert _versions(admin_client, model_id) == ["0001", "0002", "0003"]
    assert _pointer_version(model_id, "stable") == "0003"
    assert _pointer_version(model_id, "candidate") == "0003"


def test_channels_never_block_the_delete(admin_client):
    """Aunque TODOS los canales apunten a la versión, borrarla es posible (con token)."""
    model_id = _blueprint(admin_client, "chdel-noblock", ["0001", "0002"])
    _create_candidate_channel(admin_client)
    _promote(admin_client, model_id, "stable", "0002")
    _promote(admin_client, model_id, "candidate", "0002")

    plan = _delete_plan(admin_client, model_id, "0002")

    assert plan["deletable"] is True
    assert plan["blockers"] == []
    assert len(plan["channel_moves"]) == 2


def test_channel_moves_and_database_stamps_share_one_token(admin_client):
    """Una BD adelante y un canal sobre la versión borrada: un solo plan, un solo token."""
    model_id = _blueprint(admin_client, "chdel-both", ["0001", "0002", "0003", "0004"])
    _managed_db(admin_client, model_id, 5701, "db_adelante_canal", cached="0004")
    _promote(admin_client, model_id, "stable", "0002")

    with _engine({"db_adelante_canal": "0004"}) as log:
        plan = _delete_plan(admin_client, model_id, "0002")
        assert [row["database_name"] for row in plan["stamp_plan"]] == ["db_adelante_canal"]
        assert [move["channel_slug"] for move in plan["channel_moves"]] == ["stable"]
        response = _delete(admin_client, model_id, "0002", plan["confirm_token"])

    assert response.status_code == 200, response.text
    assert log.calls == [("db_adelante_canal", "0003")]
    assert _pointer_version(model_id, "stable") == "0001"


# --------------------------------------------------------------------------- #
# Bitácora y atomicidad                                                        #
# --------------------------------------------------------------------------- #
def test_delete_records_version_deleted_event(admin_client):
    model_id = _blueprint(admin_client, "chdel-event", ["0001", "0002", "0003"])
    _promote(admin_client, model_id, "stable", "0003")
    deleted_migration_id = _migration_id(model_id, "0003")
    destination_migration_id = _migration_id(model_id, "0002")

    _delete_with_token(admin_client, model_id, "0003")

    events = _events(model_id)
    assert [event.event_type for event in events] == ["promote", "version_deleted"]
    automatic_event = events[-1]
    assert automatic_event.from_migration_id == deleted_migration_id
    assert automatic_event.to_migration_id == destination_migration_id
    assert automatic_event.from_version == "0003"
    assert automatic_event.to_version == "0002"
    assert automatic_event.actor_username == "admin"
    assert automatic_event.actor_type == "admin"


def test_event_with_null_destination_keeps_the_deleted_version_label(admin_client):
    model_id = _blueprint(admin_client, "chdel-event-null", ["0001", "0002"])
    _promote(admin_client, model_id, "stable", "0001")

    _delete_with_token(admin_client, model_id, "0001")

    automatic_event = _events(model_id)[-1]
    assert automatic_event.event_type == "version_deleted"
    assert automatic_event.from_version == "0001"
    assert automatic_event.to_migration_id is None
    assert automatic_event.to_version is None


def _plan_for(model_id: int, version: str) -> dict:
    session = Database().get_declarative_base_session()
    try:
        controller = ModelMigrationController()
        model = session.get(DatabaseModel, model_id)
        migration = (
            session.query(ModelMigration)
            .filter(ModelMigration.model_id == model_id, ModelMigration.version == version)
            .one()
        )
        return controller._build_delete_plan(session, model, migration)
    finally:
        session.close()


def test_apply_renumber_with_stale_channel_moves_is_409_and_changes_nothing(admin_client):
    """
    ``_apply_renumber`` relee los punteros bajo el lock y los compara con el plan: si un canal
    cayó sobre la versión después de armarlo, aborta SIN borrar ni mover nada (y el llamador
    compensa los stamps).
    """
    model_id = _blueprint(admin_client, "chdel-applystale", ["0001", "0002", "0003"])
    _create_candidate_channel(admin_client)
    _promote(admin_client, model_id, "stable", "0003")
    plan = _plan_for(model_id, "0003")
    _promote(admin_client, model_id, "candidate", "0003")  # el plan ya no describe la realidad

    with pytest.raises(AppHttpException) as error_info:
        ModelMigrationController()._apply_renumber(model_id, "0003", plan)

    assert error_info.value.status_code == 409
    assert error_info.value.public_context["code"] == "model_migration.renumber_plan_stale"
    assert _versions(admin_client, model_id) == ["0001", "0002", "0003"]
    assert _pointer_version(model_id, "stable") == "0003"
    assert _pointer_version(model_id, "candidate") == "0003"
    assert [event.event_type for event in _events(model_id)] == ["promote", "promote"]


def test_apply_renumber_with_stale_destination_is_409(admin_client):
    """La versión destino perdió su revisión después del plan: mover ahí rompería el invariante."""
    model_id = _blueprint(admin_client, "chdel-destreview", ["0001", "0002", "0003"])
    _promote(admin_client, model_id, "stable", "0003")
    plan = _plan_for(model_id, "0003")
    assert plan["channel_moves"][0]["to_version"] == "0002"
    _mark_unreviewed(admin_client, model_id, "0002")

    with pytest.raises(AppHttpException) as error_info:
        ModelMigrationController()._apply_renumber(model_id, "0003", plan)

    assert error_info.value.public_context["code"] == "model_migration.renumber_plan_stale"
    assert _versions(admin_client, model_id) == ["0001", "0002", "0003"]
    assert _pointer_version(model_id, "stable") == "0003"


def test_apply_renumber_moves_pointers_in_the_same_transaction(admin_client):
    model_id = _blueprint(admin_client, "chdel-atomic", ["0001", "0002", "0003"])
    _promote(admin_client, model_id, "stable", "0002")
    plan = _plan_for(model_id, "0002")

    ModelMigrationController()._apply_renumber(model_id, "0002", plan)

    assert _versions(admin_client, model_id) == ["0001", "0002"]
    assert _pointer_version(model_id, "stable") == "0001"
    assert [event.event_type for event in _events(model_id)] == ["promote", "version_deleted"]
