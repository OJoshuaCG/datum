"""
Resolver de canales de liberación y sus endpoints de lectura.

Contrato que fijan estos tests:

- ``manual`` sirve el puntero; sin puntero es ``release_channel.no_released_version``.
- ``latest`` sirve la punta del blueprint, INCLUYENDO una versión sin revisar.
- ``derived`` es la versión MÍNIMA viva del entorno enlazado. Cuenta solo una base que existe y
  fue posicionada alguna vez; en base, en cuarentena o sin existir en el motor, queda afuera.
  Una base que cuenta y no se puede leer (o está en una versión desconocida) hace fallar el
  cálculo cerrado: ``release_channel.derived_unreadable``. Cero bases que cuenten:
  ``release_channel.derived_no_scope``.
- El resolver ESTRICTO lanza 409; el endpoint ``/resolve`` es TOLERANTE: siempre 200 con
  ``resolved=false`` y el código. El listado usa la caché del inventario.

El motor se reemplaza con el doble de ``test_api_migration_delete_renumber``. Los tests NO se
corren automáticamente en este repo; se verifican con
``.venv/bin/python scripts/run_tests_direct.py tests.test_channel_resolver``.
"""

import pytest

from app.core.database import Database
from app.exceptions import AppHttpException
from app.models.database_model import DatabaseModel
from app.models.enums import ProvisionStatus
from app.models.managed_database import ManagedDatabase
from app.models.release_channel import BlueprintChannelPointer, ReleaseChannel
from app.services import release_channel_catalog as channel_codes
from app.services.channel_resolution import (
    IGNORED_AT_BASE,
    IGNORED_DATABASE_MISSING,
    IGNORED_QUARANTINED,
    SOURCE_INVENTORY_CACHE,
    SOURCE_LIVE,
    SOURCE_POINTER,
    SOURCE_TIP,
    DatabaseVersionState,
    evaluate_derived,
)
from tests.test_api_migration_delete_renumber import (
    _UNREADABLE,
    _blueprint,
    _engine,
    _managed_db,
)

MODELS_URL = "/api/v1/database-models"
CHANNELS_URL = "/api/v1/release-channels"
ENVIRONMENTS_URL = "/api/v1/environments"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _state(
    database_id: int,
    version: str | None,
    *,
    exists: bool = True,
    readable: bool = True,
    quarantined: bool = False,
) -> DatabaseVersionState:
    return DatabaseVersionState(
        managed_database_id=database_id,
        database_name=f"db{database_id}",
        current_version=version,
        database_exists=exists,
        readable=readable,
        quarantined=quarantined,
    )


_IDS_BY_VERSION = {"0001": 1, "0002": 2, "0003": 3, "0004": 4, "0005": 5, "0010": 10}


def _environment_id(admin_client, slug: str) -> int:
    response = admin_client.get(f"{ENVIRONMENTS_URL}?size=50")
    assert response.status_code == 200, response.text
    return next(env["id"] for env in response.json()["data"] if env["slug"] == slug)


def _create_channel(admin_client, **overrides) -> dict:
    payload = {"name": "Candidato", "slug": "candidate", "resolution_mode": "derived"}
    payload.update(overrides)
    response = admin_client.post(CHANNELS_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _place_database(db_id: int, *, environment_id: int, status: ProvisionStatus | None = None):
    session = Database().get_declarative_base_session()
    try:
        row = session.get(ManagedDatabase, db_id)
        row.environment_id = environment_id
        if status is not None:
            row.status = status
        session.commit()
    finally:
        session.close()


def _set_pointer(model_id: int, channel_slug: str, version: str | None) -> None:
    from app.models.model_migration import ModelMigration

    session = Database().get_declarative_base_session()
    try:
        channel = session.query(ReleaseChannel).filter(ReleaseChannel.slug == channel_slug).one()
        migration_id = None
        if version is not None:
            migration_id = (
                session.query(ModelMigration.id)
                .filter(ModelMigration.model_id == model_id, ModelMigration.version == version)
                .scalar()
            )
        session.add(
            BlueprintChannelPointer(
                database_model_id=model_id,
                release_channel_id=channel.id,
                model_migration_id=migration_id,
            )
        )
        session.commit()
    finally:
        session.close()


def _strict(model_id: int, channel_slug: str):
    from app.controllers.blueprint_channel_controller import resolve_channel_version

    session = Database().get_declarative_base_session()
    try:
        model = session.get(DatabaseModel, model_id)
        return resolve_channel_version(session, model, channel_slug)
    finally:
        session.close()


def _resolve_api(client, model_id: int, channel_slug: str) -> dict:
    response = client.get(f"{MODELS_URL}/{model_id}/channels/{channel_slug}/resolve")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _derived_setup(admin_client, slug: str, cached: dict[str, str | None], port_base: int):
    """Blueprint de cinco versiones con una BD por entrada de ``cached`` en ``staging``."""
    model_id = _blueprint(admin_client, slug, ["0001", "0002", "0003", "0004", "0005"])
    staging_id = _environment_id(admin_client, "staging")
    db_ids = {}
    for offset, (db_name, version) in enumerate(cached.items()):
        db_id = _managed_db(admin_client, model_id, port_base + offset, db_name, cached=version)
        _place_database(db_id, environment_id=staging_id)
        db_ids[db_name] = db_id
    channel = _create_channel(admin_client, environment_id=staging_id)
    return model_id, db_ids, channel


# --------------------------------------------------------------------------- #
# Regla derived (pura)                                                         #
# --------------------------------------------------------------------------- #
def test_derived_takes_numeric_minimum_not_lexicographic():
    outcome = evaluate_derived(
        [_state(1, "0010"), _state(2, "0003"), _state(3, "0005")], _IDS_BY_VERSION
    )

    assert outcome.error_code is None
    assert outcome.version == "0003"
    assert outcome.model_migration_id == 3


def test_derived_excludes_base_quarantined_and_missing_databases():
    outcome = evaluate_derived(
        [
            _state(1, "0004"),
            _state(2, None),  # en base: nunca posicionada
            _state(3, "0001", quarantined=True),
            _state(4, "0001", exists=False),
        ],
        _IDS_BY_VERSION,
    )

    assert outcome.version == "0004"
    reasons = {item.managed_database_id: item.ignored_reason for item in outcome.contributions}
    assert reasons == {
        1: None,
        2: IGNORED_AT_BASE,
        3: IGNORED_QUARANTINED,
        4: IGNORED_DATABASE_MISSING,
    }


def test_derived_unreadable_counted_database_fails_closed_without_partial_minimum():
    outcome = evaluate_derived(
        [_state(1, "0004"), _state(2, None, readable=False)], _IDS_BY_VERSION
    )

    assert outcome.version is None
    assert outcome.error_code == channel_codes.CODE_DERIVED_UNREADABLE
    assert outcome.unreadable_database_ids == [2]


def test_derived_unknown_version_counts_as_unreadable():
    outcome = evaluate_derived([_state(1, "0004"), _state(2, "0099")], _IDS_BY_VERSION)

    assert outcome.error_code == channel_codes.CODE_DERIVED_UNREADABLE
    assert outcome.unreadable_database_ids == [2]


def test_derived_unreadable_quarantined_database_is_ignored():
    outcome = evaluate_derived(
        [_state(1, "0004"), _state(2, None, readable=False, quarantined=True)], _IDS_BY_VERSION
    )

    assert outcome.error_code is None
    assert outcome.version == "0004"


def test_derived_with_nothing_counted_is_no_scope():
    outcome = evaluate_derived([_state(1, None), _state(2, "0001", quarantined=True)], _IDS_BY_VERSION)

    assert outcome.error_code == channel_codes.CODE_DERIVED_NO_SCOPE
    assert evaluate_derived([], _IDS_BY_VERSION).error_code == channel_codes.CODE_DERIVED_NO_SCOPE


# --------------------------------------------------------------------------- #
# Resolver estricto: manual y latest                                           #
# --------------------------------------------------------------------------- #
def test_strict_manual_without_pointer_is_no_released_version(admin_client):
    model_id = _blueprint(admin_client, "res-manual-unset", ["0001", "0002"])

    with pytest.raises(AppHttpException) as raised:
        _strict(model_id, "stable")

    assert raised.value.status_code == 409
    assert raised.value.public_context["code"] == channel_codes.CODE_NO_RELEASED_VERSION


def test_strict_manual_with_null_pointer_is_no_released_version(admin_client):
    model_id = _blueprint(admin_client, "res-manual-null", ["0001"])
    _set_pointer(model_id, "stable", None)

    with pytest.raises(AppHttpException) as raised:
        _strict(model_id, "stable")

    assert raised.value.public_context["code"] == channel_codes.CODE_NO_RELEASED_VERSION


def test_strict_manual_returns_pointed_version(admin_client):
    model_id = _blueprint(admin_client, "res-manual-ok", ["0001", "0002", "0003"])
    _set_pointer(model_id, "stable", "0002")

    resolution = _strict(model_id, "stable")

    assert resolution.version == "0002"
    assert resolution.source == SOURCE_POINTER
    assert resolution.derived_reference is None


def test_strict_manual_pointer_to_foreign_blueprint_is_pointer_inconsistent(admin_client):
    model_id = _blueprint(admin_client, "res-incons-a", ["0001"])
    other_id = _blueprint(admin_client, "res-incons-b", ["0001", "0002"])
    from app.models.model_migration import ModelMigration

    session = Database().get_declarative_base_session()
    try:
        channel = session.query(ReleaseChannel).filter(ReleaseChannel.slug == "stable").one()
        foreign_id = (
            session.query(ModelMigration.id)
            .filter(ModelMigration.model_id == other_id, ModelMigration.version == "0002")
            .scalar()
        )
        session.add(
            BlueprintChannelPointer(
                database_model_id=model_id,
                release_channel_id=channel.id,
                model_migration_id=foreign_id,
            )
        )
        session.commit()
    finally:
        session.close()

    with pytest.raises(AppHttpException) as raised:
        _strict(model_id, "stable")

    assert raised.value.status_code == 409
    assert raised.value.public_context["code"] == channel_codes.CODE_POINTER_INCONSISTENT


def test_strict_latest_is_tip_including_unreviewed(admin_client):
    model_id = _blueprint(admin_client, "res-latest", ["0001", "0002", "0003"])
    response = admin_client.patch(
        f"{MODELS_URL}/{model_id}/migrations/0003", json={"reviewed": False}
    )
    assert response.status_code == 200, response.text

    resolution = _strict(model_id, "development")

    assert resolution.version == "0003"
    assert resolution.source == SOURCE_TIP


def test_strict_latest_without_versions_is_no_released_version(admin_client):
    model_id = _blueprint(admin_client, "res-latest-empty", [])

    with pytest.raises(AppHttpException) as raised:
        _strict(model_id, "development")

    assert raised.value.public_context["code"] == channel_codes.CODE_NO_RELEASED_VERSION


def test_strict_unknown_channel_is_404(admin_client):
    model_id = _blueprint(admin_client, "res-no-channel", ["0001"])

    with pytest.raises(AppHttpException) as raised:
        _strict(model_id, "does-not-exist")

    assert raised.value.status_code == 404
    assert raised.value.public_context["code"] == channel_codes.CODE_NOT_FOUND


# --------------------------------------------------------------------------- #
# Resolver estricto: derived contra el motor                                   #
# --------------------------------------------------------------------------- #
def test_strict_derived_returns_minimum_live_version(admin_client):
    model_id, _db_ids, _channel = _derived_setup(
        admin_client,
        "res-derived-min",
        {"dbmin1": "0003", "dbmin2": "0004", "dbmin3": "0005"},
        port_base=41000,
    )

    # El motor manda sobre la caché: la caché dice 0003 pero la base 'dbmin1' ya está en 0005.
    with _engine({"dbmin1": "0005", "dbmin2": "0004", "dbmin3": "0005"}):
        resolution = _strict(model_id, "candidate")

    assert resolution.version == "0004"
    assert resolution.source == SOURCE_LIVE
    assert resolution.resolved is True


def test_strict_derived_ignores_never_positioned_database(admin_client):
    model_id, _db_ids, _channel = _derived_setup(
        admin_client,
        "res-derived-base",
        {"dbbase1": "0004", "dbbase2": None},
        port_base=41100,
    )

    with _engine({"dbbase1": "0004", "dbbase2": None}):
        resolution = _strict(model_id, "candidate")

    assert resolution.version == "0004"
    ignored = {item.database_name: item.ignored_reason for item in resolution.databases}
    assert ignored["dbbase2"] == IGNORED_AT_BASE


def test_strict_derived_ignores_quarantined_database(admin_client):
    model_id, db_ids, _channel = _derived_setup(
        admin_client,
        "res-derived-quar",
        {"dbq1": "0004", "dbq2": "0002"},
        port_base=41200,
    )
    staging_id = _environment_id(admin_client, "staging")
    _place_database(db_ids["dbq2"], environment_id=staging_id, status=ProvisionStatus.error)

    with _engine({"dbq1": "0004", "dbq2": "0002"}):
        resolution = _strict(model_id, "candidate")

    assert resolution.version == "0004"


def test_strict_derived_ignores_databases_of_other_environments(admin_client):
    model_id, db_ids, _channel = _derived_setup(
        admin_client,
        "res-derived-env",
        {"dbenv1": "0004", "dbenv2": "0001"},
        port_base=41300,
    )
    _place_database(db_ids["dbenv2"], environment_id=_environment_id(admin_client, "production"))

    with _engine({"dbenv1": "0004", "dbenv2": "0001"}):
        resolution = _strict(model_id, "candidate")

    assert resolution.version == "0004"


def test_strict_derived_unreadable_counted_database_is_409_with_ids(admin_client):
    model_id, db_ids, _channel = _derived_setup(
        admin_client,
        "res-derived-unread",
        {"dbu1": "0004", "dbu2": "0003"},
        port_base=41400,
    )

    with _engine({"dbu1": "0004", "dbu2": _UNREADABLE}):
        with pytest.raises(AppHttpException) as raised:
            _strict(model_id, "candidate")

    assert raised.value.status_code == 409
    assert raised.value.public_context["code"] == channel_codes.CODE_DERIVED_UNREADABLE
    assert raised.value.public_context["managed_database_ids"] == [db_ids["dbu2"]]
    # El texto del motor (host, usuario, contraseña) no viaja en la respuesta.
    assert "rootpw" not in str(raised.value.public_context)
    assert "rootpw" not in raised.value.message


def test_strict_derived_unknown_engine_version_is_unreadable(admin_client):
    model_id, _db_ids, _channel = _derived_setup(
        admin_client, "res-derived-unk", {"dbk1": "0004"}, port_base=41500
    )

    with _engine({"dbk1": "0099"}):
        with pytest.raises(AppHttpException) as raised:
            _strict(model_id, "candidate")

    assert raised.value.public_context["code"] == channel_codes.CODE_DERIVED_UNREADABLE


def test_strict_derived_zero_counted_is_no_scope(admin_client):
    model_id, _db_ids, _channel = _derived_setup(
        admin_client, "res-derived-none", {"dbn1": None}, port_base=41600
    )

    with _engine({"dbn1": None}):
        with pytest.raises(AppHttpException) as raised:
            _strict(model_id, "candidate")

    assert raised.value.status_code == 409
    assert raised.value.public_context["code"] == channel_codes.CODE_DERIVED_NO_SCOPE


# --------------------------------------------------------------------------- #
# Endpoint /resolve: tolerante y en vivo                                       #
# --------------------------------------------------------------------------- #
def test_resolve_endpoint_derived_unreadable_is_200_with_per_database_flags(admin_client):
    model_id, db_ids, _channel = _derived_setup(
        admin_client,
        "res-api-unread",
        {"dbr1": "0004", "dbr2": "0003"},
        port_base=41700,
    )

    with _engine({"dbr1": "0004", "dbr2": _UNREADABLE}):
        data = _resolve_api(admin_client, model_id, "candidate")

    assert data["resolved"] is False
    assert data["code"] == channel_codes.CODE_DERIVED_UNREADABLE
    assert data["version"] is None
    readable_by_name = {item["database_name"]: item["readable"] for item in data["databases"]}
    assert readable_by_name == {"dbr1": True, "dbr2": False}


def test_resolve_endpoint_derived_ok_reports_live_source(admin_client):
    model_id, _db_ids, _channel = _derived_setup(
        admin_client,
        "res-api-ok",
        {"dbo1": "0003", "dbo2": "0005"},
        port_base=41800,
    )

    with _engine({"dbo1": "0003", "dbo2": "0005"}):
        data = _resolve_api(admin_client, model_id, "candidate")

    assert data["resolved"] is True
    assert data["version"] == "0003"
    assert data["source"] == SOURCE_LIVE
    assert data["code"] is None


def test_resolve_endpoint_manual_unset_is_200_unresolved(admin_client):
    model_id = _blueprint(admin_client, "res-api-manual", ["0001", "0002"])

    data = _resolve_api(admin_client, model_id, "stable")

    assert data["resolved"] is False
    assert data["code"] == channel_codes.CODE_NO_RELEASED_VERSION


def test_resolve_endpoint_manual_shows_derived_reference_next_to_pointer(admin_client):
    model_id, _db_ids, _channel = _derived_setup(
        admin_client,
        "res-api-ref",
        {"dbf1": "0003", "dbf2": "0004"},
        port_base=41900,
    )
    staging_id = _environment_id(admin_client, "staging")
    response = admin_client.patch(f"{CHANNELS_URL}/{_stable_id()}", json={"environment_id": staging_id})
    assert response.status_code == 200, response.text
    _set_pointer(model_id, "stable", "0002")

    with _engine({"dbf1": "0003", "dbf2": "0004"}):
        data = _resolve_api(admin_client, model_id, "stable")

    assert data["version"] == "0002"
    assert data["source"] == SOURCE_POINTER
    assert data["derived_reference"]["version"] == "0003"
    assert data["derived_reference"]["source"] == SOURCE_LIVE


def test_resolve_endpoint_unknown_channel_is_404(admin_client):
    model_id = _blueprint(admin_client, "res-api-404", ["0001"])

    response = admin_client.get(f"{MODELS_URL}/{model_id}/channels/nope/resolve")

    assert response.status_code == 404
    assert response.json()["detail"]["public_context"]["code"] == channel_codes.CODE_NOT_FOUND


def test_resolve_endpoint_unknown_blueprint_is_404(admin_client):
    response = admin_client.get(f"{MODELS_URL}/999999/channels/stable/resolve")

    assert response.status_code == 404


def _stable_id() -> int:
    session = Database().get_declarative_base_session()
    try:
        return session.query(ReleaseChannel.id).filter(ReleaseChannel.slug == "stable").scalar()
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Listado: caché del inventario, sin tocar el motor                            #
# --------------------------------------------------------------------------- #
def test_list_channels_uses_inventory_cache_and_never_opens_the_engine(admin_client):
    model_id, _db_ids, _channel = _derived_setup(
        admin_client,
        "res-list",
        {"dbl1": "0003", "dbl2": "0005"},
        port_base=42000,
    )
    _set_pointer(model_id, "stable", "0004")

    # Sin ``_engine``: si el listado abriera un motor, el runner real fallaría y la base
    # figuraría como ilegible. Con la caché, el resultado no depende del motor.
    response = admin_client.get(f"{MODELS_URL}/{model_id}/channels")
    assert response.status_code == 200, response.text
    by_slug = {item["channel_slug"]: item for item in response.json()["data"]}

    assert by_slug["stable"]["version"] == "0004"
    assert by_slug["stable"]["source"] == SOURCE_POINTER
    assert by_slug["development"]["version"] == "0005"
    assert by_slug["development"]["source"] == SOURCE_TIP
    assert by_slug["candidate"]["resolved"] is True
    assert by_slug["candidate"]["version"] == "0003"
    assert by_slug["candidate"]["source"] == SOURCE_INVENTORY_CACHE


def test_list_channels_unresolved_channel_does_not_fail_the_list(admin_client):
    model_id = _blueprint(admin_client, "res-list-unset", ["0001"])

    response = admin_client.get(f"{MODELS_URL}/{model_id}/channels")
    assert response.status_code == 200, response.text
    by_slug = {item["channel_slug"]: item for item in response.json()["data"]}

    assert by_slug["stable"]["resolved"] is False
    assert by_slug["stable"]["code"] == channel_codes.CODE_NO_RELEASED_VERSION
    assert by_slug["development"]["resolved"] is True


def test_list_channels_unknown_blueprint_is_404(admin_client):
    response = admin_client.get(f"{MODELS_URL}/999999/channels")

    assert response.status_code == 404
