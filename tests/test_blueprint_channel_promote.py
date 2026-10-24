"""
Promover el puntero de un canal de liberación y su historial.

Contrato que fijan estos tests:

- Solo se promueven canales ``manual`` y versiones REVISADAS del propio blueprint.
- Avanzar (o poner el primer valor) es un ``promote``; retroceder es un ``demote`` y exige
  repetir el slug del canal en ``confirm_slug`` (409 sin él, y el puntero no se mueve).
- Promover a la misma versión es un no-op: 200 ``changed=false`` y sin evento.
- ``blueprints.promote`` es solo de ``owner``: un ``operator`` recibe 403.
- El historial se lee del evento más reciente al más antiguo.

El motor no interviene: mover un puntero es una escritura solo del gateway. Los tests NO se
corren automáticamente en este repo; se verifican con
``.venv/bin/python scripts/run_tests_direct.py tests.test_blueprint_channel_promote``.
"""

from app.core.database import Database
from app.models.release_channel import (
    BlueprintChannelPointer,
    BlueprintChannelPointerEvent,
    ReleaseChannel,
)

MODELS_URL = "/api/v1/database-models"
CHANNELS_URL = "/api/v1/release-channels"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _public_context(response) -> dict:
    return response.json()["detail"]["public_context"]


def _blueprint(admin_client, slug: str, versions: list[str]) -> int:
    response = admin_client.post(MODELS_URL, json={"name": slug, "slug": slug})
    assert response.status_code == 201, response.text
    model_id = response.json()["data"]["id"]
    for version in versions:
        created = admin_client.post(
            f"{MODELS_URL}/{model_id}/migrations",
            json={
                "version": version,
                "name": f"m{version}",
                "up_sql": f"CREATE TABLE t{version} (id INT PRIMARY KEY)",
            },
        )
        assert created.status_code == 201, created.text
    return model_id


def _mark_unreviewed(admin_client, model_id: int, version: str) -> None:
    response = admin_client.patch(
        f"{MODELS_URL}/{model_id}/migrations/{version}", json={"reviewed": False}
    )
    assert response.status_code == 200, response.text


def _promote(client, model_id: int, channel_slug: str, body: dict, **query):
    return client.post(
        f"{MODELS_URL}/{model_id}/channels/{channel_slug}/promote", json=body, params=query
    )


def _history(client, model_id: int, channel_slug: str) -> list[dict]:
    response = client.get(f"{MODELS_URL}/{model_id}/channels/{channel_slug}/history?size=50")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _pointer_version(model_id: int, channel_slug: str) -> str | None:
    """La versión a la que apunta hoy el canal, leída de la BD y no de la API."""
    from app.models.model_migration import ModelMigration

    session = Database().get_declarative_base_session()
    try:
        channel = session.query(ReleaseChannel).filter(ReleaseChannel.slug == channel_slug).one()
        pointer = session.get(
            BlueprintChannelPointer,
            {"database_model_id": model_id, "release_channel_id": channel.id},
        )
        if pointer is None or pointer.model_migration_id is None:
            return None
        return session.get(ModelMigration, pointer.model_migration_id).version
    finally:
        session.close()


def _event_count(model_id: int) -> int:
    session = Database().get_declarative_base_session()
    try:
        return (
            session.query(BlueprintChannelPointerEvent)
            .filter(BlueprintChannelPointerEvent.database_model_id == model_id)
            .count()
        )
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Camino feliz                                                                 #
# --------------------------------------------------------------------------- #
def test_promote_from_unset_creates_pointer_and_one_promote_event(admin_client):
    model_id = _blueprint(admin_client, "promo-unset", ["0001", "0002", "0003"])

    response = _promote(admin_client, model_id, "stable", {"version": "0002", "note": "primera"})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["changed"] is True
    assert data["event_type"] == "promote"
    assert data["from_version"] is None
    assert data["to_version"] == "0002"

    assert _pointer_version(model_id, "stable") == "0002"
    events = _history(admin_client, model_id, "stable")
    assert len(events) == 1
    assert events[0]["event_type"] == "promote"
    assert events[0]["to_version"] == "0002"
    assert events[0]["from_version"] is None
    assert events[0]["note"] == "primera"
    assert events[0]["actor_username"] == "admin"


def test_promote_forward_records_from_and_to(admin_client):
    model_id = _blueprint(admin_client, "promo-fwd", ["0001", "0002", "0003"])
    assert _promote(admin_client, model_id, "stable", {"version": "0001"}).status_code == 200

    response = _promote(admin_client, model_id, "stable", {"version": "0003"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["event_type"] == "promote"
    assert response.json()["data"]["from_version"] == "0001"
    assert _pointer_version(model_id, "stable") == "0003"
    assert _event_count(model_id) == 2


def test_promote_by_model_migration_id(admin_client):
    model_id = _blueprint(admin_client, "promo-by-id", ["0001", "0002"])
    listing = admin_client.get(f"{MODELS_URL}/{model_id}/migrations?size=50").json()["data"]
    target_id = next(row["id"] for row in listing if row["version"] == "0002")

    response = _promote(admin_client, model_id, "stable", {"model_migration_id": target_id})
    assert response.status_code == 200, response.text
    assert _pointer_version(model_id, "stable") == "0002"


# --------------------------------------------------------------------------- #
# Retroceso                                                                    #
# --------------------------------------------------------------------------- #
def test_backward_without_confirm_is_409_and_does_not_move(admin_client):
    model_id = _blueprint(admin_client, "promo-back", ["0001", "0002", "0003"])
    assert _promote(admin_client, model_id, "stable", {"version": "0003"}).status_code == 200

    response = _promote(admin_client, model_id, "stable", {"version": "0001"})
    assert response.status_code == 409, response.text
    context = _public_context(response)
    assert context["code"] == "release_channel.promote_backward_requires_confirm"
    assert context["expected_slug"] == "stable"
    assert _pointer_version(model_id, "stable") == "0003"
    assert _event_count(model_id) == 1


def test_backward_with_wrong_confirm_slug_is_409(admin_client):
    model_id = _blueprint(admin_client, "promo-back-bad", ["0001", "0002"])
    assert _promote(admin_client, model_id, "stable", {"version": "0002"}).status_code == 200

    response = _promote(
        admin_client, model_id, "stable", {"version": "0001"}, confirm_slug="development"
    )
    assert response.status_code == 409, response.text
    assert _pointer_version(model_id, "stable") == "0002"


def test_backward_with_confirm_slug_moves_and_records_demote(admin_client):
    model_id = _blueprint(admin_client, "promo-demote", ["0001", "0002", "0003"])
    assert _promote(admin_client, model_id, "stable", {"version": "0003"}).status_code == 200

    response = _promote(admin_client, model_id, "stable", {"version": "0001"}, confirm_slug="stable")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["event_type"] == "demote"
    assert _pointer_version(model_id, "stable") == "0001"

    latest_event = _history(admin_client, model_id, "stable")[0]
    assert latest_event["event_type"] == "demote"
    assert latest_event["from_version"] == "0003"
    assert latest_event["to_version"] == "0001"


# --------------------------------------------------------------------------- #
# Rechazos                                                                     #
# --------------------------------------------------------------------------- #
def test_unreviewed_version_is_409(admin_client):
    model_id = _blueprint(admin_client, "promo-unrev", ["0001", "0002"])
    _mark_unreviewed(admin_client, model_id, "0002")

    response = _promote(admin_client, model_id, "stable", {"version": "0002"})
    assert response.status_code == 409, response.text
    assert _public_context(response)["code"] == "release_channel.version_unreviewed"
    assert _pointer_version(model_id, "stable") is None
    assert _event_count(model_id) == 0


def test_derived_and_latest_channels_are_not_manual(admin_client):
    model_id = _blueprint(admin_client, "promo-notmanual", ["0001"])

    latest_response = _promote(admin_client, model_id, "development", {"version": "0001"})
    assert latest_response.status_code == 409, latest_response.text
    assert _public_context(latest_response)["code"] == "release_channel.not_manual"

    production = next(
        env
        for env in admin_client.get("/api/v1/environments?size=50").json()["data"]
        if env["slug"] == "production"
    )
    created = admin_client.post(
        CHANNELS_URL,
        json={
            "name": "Derivado",
            "slug": "derivado",
            "resolution_mode": "derived",
            "environment_id": production["id"],
        },
    )
    assert created.status_code == 201, created.text
    derived_response = _promote(admin_client, model_id, "derivado", {"version": "0001"})
    assert derived_response.status_code == 409, derived_response.text
    assert _public_context(derived_response)["code"] == "release_channel.not_manual"


def test_version_of_another_blueprint_is_422_not_in_blueprint(admin_client):
    model_id = _blueprint(admin_client, "promo-own", ["0001"])
    other_model_id = _blueprint(admin_client, "promo-other", ["0001"])
    other_listing = admin_client.get(f"{MODELS_URL}/{other_model_id}/migrations?size=50").json()[
        "data"
    ]

    response = _promote(
        admin_client, model_id, "stable", {"model_migration_id": other_listing[0]["id"]}
    )
    assert response.status_code == 422, response.text
    assert _public_context(response)["code"] == "release_channel.version_not_in_blueprint"
    assert _pointer_version(model_id, "stable") is None


def test_unknown_version_is_422_version_not_found(admin_client):
    model_id = _blueprint(admin_client, "promo-unknown", ["0001"])

    by_version = _promote(admin_client, model_id, "stable", {"version": "0099"})
    assert by_version.status_code == 422, by_version.text
    assert _public_context(by_version)["code"] == "release_channel.version_not_found"

    by_id = _promote(admin_client, model_id, "stable", {"model_migration_id": 999999})
    assert by_id.status_code == 422, by_id.text
    assert _public_context(by_id)["code"] == "release_channel.version_not_found"


def test_unknown_channel_is_404_and_unknown_blueprint_is_404(admin_client):
    model_id = _blueprint(admin_client, "promo-404", ["0001"])

    unknown_channel = _promote(admin_client, model_id, "no-existe", {"version": "0001"})
    assert unknown_channel.status_code == 404, unknown_channel.text
    assert _public_context(unknown_channel)["code"] == "release_channel.not_found"

    unknown_blueprint = _promote(admin_client, 999999, "stable", {"version": "0001"})
    assert unknown_blueprint.status_code == 404, unknown_blueprint.text


def test_body_needs_exactly_one_target(admin_client):
    model_id = _blueprint(admin_client, "promo-body", ["0001"])

    neither = _promote(admin_client, model_id, "stable", {})
    assert neither.status_code == 422, neither.text
    both = _promote(admin_client, model_id, "stable", {"version": "0001", "model_migration_id": 1})
    assert both.status_code == 422, both.text


def test_pointer_to_foreign_blueprint_row_is_409_inconsistent(admin_client):
    """Un puntero que cruza de blueprint no lo produce ningún camino legítimo: se corta."""
    model_id = _blueprint(admin_client, "promo-incons", ["0001", "0002"])
    other_model_id = _blueprint(admin_client, "promo-incons-other", ["0001"])
    other_listing = admin_client.get(f"{MODELS_URL}/{other_model_id}/migrations?size=50").json()[
        "data"
    ]

    session = Database().get_declarative_base_session()
    try:
        channel = session.query(ReleaseChannel).filter(ReleaseChannel.slug == "stable").one()
        session.add(
            BlueprintChannelPointer(
                database_model_id=model_id,
                release_channel_id=channel.id,
                model_migration_id=other_listing[0]["id"],
            )
        )
        session.commit()
    finally:
        session.close()

    response = _promote(admin_client, model_id, "stable", {"version": "0002"})
    assert response.status_code == 409, response.text
    assert _public_context(response)["code"] == "release_channel.pointer_inconsistent"


# --------------------------------------------------------------------------- #
# No-op                                                                        #
# --------------------------------------------------------------------------- #
def test_same_version_is_200_unchanged_and_writes_no_event(admin_client):
    model_id = _blueprint(admin_client, "promo-noop", ["0001", "0002"])
    assert _promote(admin_client, model_id, "stable", {"version": "0002"}).status_code == 200

    response = _promote(admin_client, model_id, "stable", {"version": "0002"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["changed"] is False
    assert response.json()["data"]["event_type"] is None
    assert _event_count(model_id) == 1


# --------------------------------------------------------------------------- #
# Autorización                                                                 #
# --------------------------------------------------------------------------- #
def test_requires_auth(client):
    response = client.post(f"{MODELS_URL}/1/channels/stable/promote", json={"version": "0001"})
    assert response.status_code == 401


def test_operator_without_promote_capability_is_403(admin_client):
    """``blueprints.promote`` es solo de owner: operar (escribir versiones) no alcanza."""
    from tests.access_request_helpers import client_as, create_user

    model_id = _blueprint(admin_client, "promo-403", ["0001"])
    account = create_user(admin_client, "operador-promote", gateway_role="operator")
    operator_client = client_as(account, "operador-promote")

    response = _promote(operator_client, model_id, "stable", {"version": "0001"})
    assert response.status_code == 403, response.text
    assert _pointer_version(model_id, "stable") is None


def test_owner_can_promote(owner_client, admin_client):
    model_id = _blueprint(admin_client, "promo-owner", ["0001"])

    response = _promote(owner_client, model_id, "stable", {"version": "0001"})
    assert response.status_code == 200, response.text
    assert _pointer_version(model_id, "stable") == "0001"


def test_history_is_readable_without_promote_capability(admin_client):
    """Leer la bitácora es ``blueprints.read``: un operator la ve aunque no pueda promover."""
    from tests.access_request_helpers import client_as, create_user

    model_id = _blueprint(admin_client, "promo-hist-read", ["0001"])
    assert _promote(admin_client, model_id, "stable", {"version": "0001"}).status_code == 200
    account = create_user(admin_client, "operador-historia", gateway_role="operator")
    operator_client = client_as(account, "operador-historia")

    assert len(_history(operator_client, model_id, "stable")) == 1


# --------------------------------------------------------------------------- #
# Historial                                                                    #
# --------------------------------------------------------------------------- #
def test_history_is_newest_first_and_per_channel(admin_client):
    model_id = _blueprint(admin_client, "promo-hist", ["0001", "0002", "0003"])
    created = admin_client.post(
        CHANNELS_URL, json={"name": "Candidato", "slug": "candidate", "resolution_mode": "manual"}
    )
    assert created.status_code == 201, created.text

    assert _promote(admin_client, model_id, "stable", {"version": "0001"}).status_code == 200
    assert _promote(admin_client, model_id, "stable", {"version": "0003"}).status_code == 200
    assert _promote(admin_client, model_id, "candidate", {"version": "0002"}).status_code == 200

    stable_events = _history(admin_client, model_id, "stable")
    assert [event["to_version"] for event in stable_events] == ["0003", "0001"]
    candidate_events = _history(admin_client, model_id, "candidate")
    assert [event["to_version"] for event in candidate_events] == ["0002"]
    event_ids = [event["id"] for event in stable_events]
    assert event_ids == sorted(event_ids, reverse=True)


def test_history_includes_automatic_version_deleted_moves(admin_client):
    model_id = _blueprint(admin_client, "promo-hist-auto", ["0001", "0002", "0003"])
    assert _promote(admin_client, model_id, "stable", {"version": "0003"}).status_code == 200

    plan = admin_client.get(f"{MODELS_URL}/{model_id}/migrations/0003/delete-plan").json()["data"]
    deleted = admin_client.delete(
        f"{MODELS_URL}/{model_id}/migrations/0003",
        params={"confirm_token": plan["confirm_token"]},
    )
    assert deleted.status_code == 200, deleted.text

    events = _history(admin_client, model_id, "stable")
    assert [event["event_type"] for event in events] == ["version_deleted", "promote"]
    assert events[0]["from_version"] == "0003"
    assert events[0]["to_version"] == "0002"


def test_history_of_unknown_channel_is_404(admin_client):
    model_id = _blueprint(admin_client, "promo-hist-404", ["0001"])
    response = admin_client.get(f"{MODELS_URL}/{model_id}/channels/no-existe/history")
    assert response.status_code == 404, response.text
    assert _public_context(response)["code"] == "release_channel.not_found"
