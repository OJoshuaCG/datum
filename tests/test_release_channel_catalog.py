"""
Catálogo de canales de liberación: CRUD, borrado en cascada, guard de entornos y seed.

Los tests que NO pueden faltar:

- ``test_seed_rows_match_the_migration``: las filas del seed se declaran DOS veces (en la
  migración y en el servicio). Si divergen, un gateway provisionado por Alembic y uno
  provisionado por ``create_all`` sirven canales distintos.
- ``test_delete_channel_cascades_pointers_and_events``: SQLite no aplica claves foráneas, así
  que el borrado en cascada tiene que hacerlo el controller. Si falla, un canal borrado deja
  punteros huérfanos que un canal nuevo con el mismo id heredaría.
- ``test_delete_environment_linked_to_channel_is_409``: un canal ``derived`` no puede quedar
  apuntando a un entorno que ya no existe.

Estos tests NO se corren automáticamente en este repo (ver CLAUDE.md): se verifican con
``.venv/bin/python scripts/run_tests_direct.py tests.test_release_channel_catalog``.
"""

import importlib.util
import pathlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from typing import get_args

from app.core.database import Database
from app.models.database_model import DatabaseModel
from app.models.model_migration import ModelMigration
from app.models.release_channel import (
    BlueprintChannelPointer,
    BlueprintChannelPointerEvent,
    ReleaseChannel,
)
from app.schemas.release_channel import ResolutionMode
from app.services import release_channel_catalog as channel_catalog
from app.services.release_channel_catalog import (
    RELEASE_CHANNEL_MODES,
    release_channel_seed_rows,
    seed_release_channels,
)

CHANNELS_URL = "/api/v1/release-channels"
ENVIRONMENTS_URL = "/api/v1/environments"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _public_context(response) -> dict:
    return response.json()["detail"]["public_context"]


def _channels(admin_client) -> dict[str, dict]:
    response = admin_client.get(f"{CHANNELS_URL}?size=50")
    assert response.status_code == 200, response.text
    return {channel["slug"]: channel for channel in response.json()["data"]}


def _environment_id(admin_client, slug: str) -> int:
    response = admin_client.get(f"{ENVIRONMENTS_URL}?size=50")
    assert response.status_code == 200, response.text
    return next(env["id"] for env in response.json()["data"] if env["slug"] == slug)


def _create_channel(admin_client, **overrides) -> dict:
    payload = {"name": "Candidato", "slug": "candidate", "resolution_mode": "manual"}
    payload.update(overrides)
    response = admin_client.post(CHANNELS_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _load_migration(glob: str):
    """Carga una migración por RUTA: su nombre de archivo no es un identificador importable."""
    path = next(pathlib.Path("alembic/versions").glob(glob))
    spec = importlib.util.spec_from_file_location(f"_mig_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_pointer_with_event(channel_id: int) -> tuple[int, int]:
    """Crea un blueprint con una versión, un puntero al canal y un evento. Devuelve (modelo, versión)."""
    session = Database().get_declarative_base_session()
    try:
        blueprint = DatabaseModel(name="Blueprint canal", slug="blueprint-canal")
        session.add(blueprint)
        session.flush()
        version = ModelMigration(
            model_id=blueprint.id,
            version="1.0.0",
            name="inicial",
            up_sql="SELECT 1",
            checksum="0" * 64,
        )
        session.add(version)
        session.flush()
        session.add(
            BlueprintChannelPointer(
                database_model_id=blueprint.id,
                release_channel_id=channel_id,
                model_migration_id=version.id,
            )
        )
        session.add(
            BlueprintChannelPointerEvent(
                database_model_id=blueprint.id,
                release_channel_id=channel_id,
                event_type=channel_catalog.EVENT_PROMOTE,
                to_migration_id=version.id,
                to_version="1.0.0",
                actor_username="admin",
                actor_type="admin",
            )
        )
        session.commit()
        return blueprint.id, version.id
    finally:
        session.close()


def _count(model) -> int:
    session = Database().get_declarative_base_session()
    try:
        return session.query(model).count()
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Autorización                                                                 #
# --------------------------------------------------------------------------- #
def test_requires_auth(client):
    assert client.get(CHANNELS_URL).status_code == 401


def test_write_without_capability_is_403(owner_client):
    """``owner`` sin ``security_officer``: lee los canales pero no los escribe."""
    assert owner_client.get(CHANNELS_URL).status_code == 200
    payload = {"name": "Nuevo", "slug": "nuevo", "resolution_mode": "manual"}
    assert owner_client.post(CHANNELS_URL, json=payload).status_code == 403


# --------------------------------------------------------------------------- #
# CRUD                                                                         #
# --------------------------------------------------------------------------- #
def test_create_and_get(admin_client):
    created = _create_channel(admin_client)
    assert created["slug"] == "candidate"
    assert created["resolution_mode"] == "manual"
    assert created["environment_id"] is None
    assert created["pointer_count"] == 0

    response = admin_client.get(f"{CHANNELS_URL}/{created['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["slug"] == "candidate"


def test_slug_is_normalized_to_lowercase(admin_client):
    created = _create_channel(admin_client, name="Mayus", slug="  MiCanal ")
    assert created["slug"] == "micanal"


def test_duplicate_slug_is_409_slug_taken(admin_client):
    response = admin_client.post(
        CHANNELS_URL, json={"name": "Otro nombre", "slug": "stable", "resolution_mode": "manual"}
    )
    assert response.status_code == 409, response.text
    assert _public_context(response)["code"] == "release_channel.slug_taken"


def test_duplicate_name_is_409_name_taken(admin_client):
    response = admin_client.post(
        CHANNELS_URL, json={"name": "Estable", "slug": "otro-slug", "resolution_mode": "manual"}
    )
    assert response.status_code == 409, response.text
    assert _public_context(response)["code"] == "release_channel.name_taken"


def test_get_unknown_channel_is_404_not_found(admin_client):
    response = admin_client.get(f"{CHANNELS_URL}/999999")
    assert response.status_code == 404, response.text
    assert _public_context(response)["code"] == "release_channel.not_found"


def test_invalid_mode_is_422_from_pydantic(admin_client):
    """El modo inválido no tiene código propio: lo rechaza la validación estándar."""
    response = admin_client.post(
        CHANNELS_URL, json={"name": "Raro", "slug": "raro", "resolution_mode": "yolo"}
    )
    assert response.status_code == 422, response.text


def test_mode_literal_matches_the_closed_vocabulary():
    """El ``Literal`` del schema y el vocabulario del CHECK tienen que ser la misma lista."""
    assert set(get_args(ResolutionMode)) == set(RELEASE_CHANNEL_MODES)


def test_derived_without_environment_is_422_environment_required(admin_client):
    response = admin_client.post(
        CHANNELS_URL, json={"name": "Derivado", "slug": "derivado", "resolution_mode": "derived"}
    )
    assert response.status_code == 422, response.text
    assert _public_context(response)["code"] == "release_channel.environment_required"


def test_derived_with_environment_is_created(admin_client):
    staging_id = _environment_id(admin_client, "staging")
    created = _create_channel(
        admin_client,
        name="Derivado",
        slug="derivado",
        resolution_mode="derived",
        environment_id=staging_id,
    )
    assert created["environment_id"] == staging_id


def test_unknown_environment_is_422_environment_not_found(admin_client):
    response = admin_client.post(
        CHANNELS_URL,
        json={
            "name": "Huérfano",
            "slug": "huerfano",
            "resolution_mode": "derived",
            "environment_id": 999999,
        },
    )
    assert response.status_code == 422, response.text
    assert _public_context(response)["code"] == "environment.not_found"


def test_patch_to_derived_without_environment_is_rejected_on_resulting_state(admin_client):
    created = _create_channel(admin_client)
    response = admin_client.patch(
        f"{CHANNELS_URL}/{created['id']}", json={"resolution_mode": "derived"}
    )
    assert response.status_code == 422, response.text
    assert _public_context(response)["code"] == "release_channel.environment_required"


def test_patch_unlinking_environment_of_derived_channel_is_rejected(admin_client):
    staging_id = _environment_id(admin_client, "staging")
    created = _create_channel(
        admin_client,
        name="Derivado",
        slug="derivado",
        resolution_mode="derived",
        environment_id=staging_id,
    )
    response = admin_client.patch(f"{CHANNELS_URL}/{created['id']}", json={"environment_id": None})
    assert response.status_code == 422, response.text
    assert _public_context(response)["code"] == "release_channel.environment_required"


def test_patch_updates_name_and_mode(admin_client):
    created = _create_channel(admin_client)
    response = admin_client.patch(
        f"{CHANNELS_URL}/{created['id']}",
        json={"name": "Candidato 2", "resolution_mode": "latest"},
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert (body["name"], body["resolution_mode"]) == ("Candidato 2", "latest")


def test_patch_cannot_change_the_slug(admin_client):
    """``slug`` es inmutable: el campo enviado se ignora, no renombra el canal."""
    created = _create_channel(admin_client)
    response = admin_client.patch(f"{CHANNELS_URL}/{created['id']}", json={"slug": "otro"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["slug"] == "candidate"


def test_patch_to_a_taken_name_is_409(admin_client):
    created = _create_channel(admin_client)
    response = admin_client.patch(f"{CHANNELS_URL}/{created['id']}", json={"name": "Estable"})
    assert response.status_code == 409, response.text
    assert _public_context(response)["code"] == "release_channel.name_taken"


# --------------------------------------------------------------------------- #
# Borrado                                                                      #
# --------------------------------------------------------------------------- #
def test_delete_channel_without_pointers_needs_no_confirmation(admin_client):
    created = _create_channel(admin_client)
    response = admin_client.delete(f"{CHANNELS_URL}/{created['id']}")
    assert response.status_code == 200, response.text
    assert admin_client.get(f"{CHANNELS_URL}/{created['id']}").status_code == 404


def test_delete_channel_with_pointers_requires_confirm_slug(admin_client):
    created = _create_channel(admin_client)
    _seed_pointer_with_event(created["id"])

    listed = _channels(admin_client)["candidate"]
    assert listed["pointer_count"] == 1

    without_confirmation = admin_client.delete(f"{CHANNELS_URL}/{created['id']}")
    assert without_confirmation.status_code == 422, without_confirmation.text
    context = _public_context(without_confirmation)
    assert context["code"] == "release_channel.confirmation_required"
    assert context["expected_slug"] == "candidate"
    assert "candidate" in _channels(admin_client)

    wrong_confirmation = admin_client.delete(
        f"{CHANNELS_URL}/{created['id']}", params={"confirm_slug": "otro"}
    )
    assert wrong_confirmation.status_code == 422, wrong_confirmation.text


def test_delete_channel_cascades_pointers_and_events(admin_client):
    created = _create_channel(admin_client)
    _seed_pointer_with_event(created["id"])
    assert _count(BlueprintChannelPointer) == 1
    assert _count(BlueprintChannelPointerEvent) == 1

    response = admin_client.delete(
        f"{CHANNELS_URL}/{created['id']}", params={"confirm_slug": "Candidate"}
    )
    assert response.status_code == 200, response.text
    assert _count(BlueprintChannelPointer) == 0
    assert _count(BlueprintChannelPointerEvent) == 0
    assert "candidate" not in _channels(admin_client)


# --------------------------------------------------------------------------- #
# Guard del borrado de entornos                                                #
# --------------------------------------------------------------------------- #
def test_delete_environment_linked_to_channel_is_409(admin_client):
    """``stable`` se siembra enlazado a ``production``: ese entorno no se puede borrar."""
    production_id = _environment_id(admin_client, "production")
    response = admin_client.delete(f"{ENVIRONMENTS_URL}/{production_id}")
    assert response.status_code == 409, response.text
    context = _public_context(response)
    assert context["code"] == "environment.has_release_channels"
    assert context["channel_count"] == 1
    assert context["channel_slugs"] == ["stable"]
    assert _environment_id(admin_client, "production") == production_id


def test_delete_environment_without_channels_keeps_working(admin_client):
    staging_id = _environment_id(admin_client, "staging")
    response = admin_client.delete(f"{ENVIRONMENTS_URL}/{staging_id}")
    assert response.status_code == 200, response.text


def test_environment_becomes_deletable_after_unlinking_its_channel(admin_client):
    qa_response = admin_client.post(ENVIRONMENTS_URL, json={"name": "QA", "slug": "qa"})
    assert qa_response.status_code == 201, qa_response.text
    qa_id = qa_response.json()["data"]["id"]
    channel = _create_channel(
        admin_client, name="QA canal", slug="qa-canal", resolution_mode="derived", environment_id=qa_id
    )

    blocked = admin_client.delete(f"{ENVIRONMENTS_URL}/{qa_id}")
    assert blocked.status_code == 409, blocked.text

    admin_client.patch(f"{CHANNELS_URL}/{channel['id']}", json={"resolution_mode": "manual"})
    unlinked = admin_client.patch(f"{CHANNELS_URL}/{channel['id']}", json={"environment_id": None})
    assert unlinked.status_code == 200, unlinked.text
    assert admin_client.delete(f"{ENVIRONMENTS_URL}/{qa_id}").status_code == 200


# --------------------------------------------------------------------------- #
# Seed                                                                         #
# --------------------------------------------------------------------------- #
def test_seed_creates_stable_and_development(admin_client):
    channels = _channels(admin_client)
    assert set(channels) == {"stable", "development"}
    assert channels["stable"]["resolution_mode"] == "manual"
    assert channels["stable"]["environment_id"] == _environment_id(admin_client, "production")
    assert channels["development"]["resolution_mode"] == "latest"
    assert channels["development"]["environment_id"] is None


def test_seed_rows_match_the_migration(admin_client):
    """``release_channel_seed_rows()`` == el literal ``_SEED_ROWS`` de la migración."""
    migration = _load_migration("*_release_channels.py")
    from_migration = {row["slug"]: row for row in migration._SEED_ROWS}
    from_service = {row["slug"]: row for row in release_channel_seed_rows()}
    assert from_migration.keys() == from_service.keys()
    assert from_migration == from_service


def test_seed_does_not_resurrect_a_deleted_channel(admin_client):
    channels = _channels(admin_client)
    response = admin_client.delete(f"{CHANNELS_URL}/{channels['development']['id']}")
    assert response.status_code == 200, response.text
    seed_release_channels()  # segundo arranque: la tabla no está vacía
    assert set(_channels(admin_client)) == {"stable"}


def test_seed_twice_leaves_one_row_each(admin_client):
    seed_release_channels()
    seed_release_channels()
    assert _count(ReleaseChannel) == 2


def test_stable_has_no_environment_when_production_is_absent(admin_client):
    """Con la tabla de canales vacía y sin ``production``, ``stable`` nace sin entorno."""
    production_id = _environment_id(admin_client, "production")
    session = Database().get_declarative_base_session()
    try:
        session.query(ReleaseChannel).delete()
        session.execute(text("DELETE FROM environments WHERE id = :id"), {"id": production_id})
        session.commit()
    finally:
        session.close()

    seed_release_channels()

    channels = _channels(admin_client)
    assert channels["stable"]["environment_id"] is None


@pytest.mark.parametrize("mode", RELEASE_CHANNEL_MODES)
def test_every_mode_in_the_vocabulary_is_accepted_by_the_api(admin_client, mode):
    staging_id = _environment_id(admin_client, "staging")
    created = _create_channel(
        admin_client,
        name=f"Canal {mode}",
        slug=f"canal-{mode}",
        resolution_mode=mode,
        environment_id=staging_id if mode == "derived" else None,
    )
    assert created["resolution_mode"] == mode


def test_migration_upgrade_twice_does_not_collide_and_seeds_once():
    """
    MySQL/MariaDB no tienen DDL transaccional: una migración que muere a mitad se reintenta sobre
    lo que ya aplicó. Correrla dos veces no debe chocar ni duplicar el seed, y el slug del entorno
    se compara en Python (no en SQL), así que ``Production`` no enlaza a ``stable``.
    """
    migration = _load_migration("*_release_channels.py")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE environments (id INTEGER PRIMARY KEY, slug VARCHAR(64))"))
        connection.execute(text("INSERT INTO environments VALUES (7, 'production'), (8, 'Production')"))
        for _attempt in range(2):
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

        table_names = set(inspect(connection).get_table_names())
        assert {
            "release_channels",
            "blueprint_channel_pointers",
            "blueprint_channel_pointer_events",
        } <= table_names
        seeded_rows = connection.execute(
            text("SELECT slug, environment_id FROM release_channels ORDER BY slug")
        ).all()
        assert [tuple(row) for row in seeded_rows] == [("development", None), ("stable", 7)]
