"""
Consumidores del resolver ESTRICTO de canales: aprovisionar, apply-all e integración.

Contrato que fijan estos tests:

- ``channel`` y ``target_version`` son destinos EXCLUYENTES en los tres consumidores
  (422 ``release_channel.target_conflict``), y se rechazan antes de resolver o tocar el motor.
- Un canal inexistente es 404; uno que no resuelve es 409 y NO cae a "hasta la última": no queda
  ni fila de inventario ni migración aplicada.
- Aprovisionar con canal exige ``apply_migrations`` (mismo código que ``target_version``).
- ``latest`` sirve la punta aunque no esté revisada, pero los guards de revisión de siempre la
  frenan al aplicar: el canal no abre una puerta nueva.
- apply-all con tope: una BD que ya lo superó reporta cero pendientes y NO se revierte.
- Integración: el canal se resuelve contra el blueprint ASIGNADO a la base, dentro del alcance
  del token, y el resultado pasa por la misma regla "solo hacia adelante" que una versión
  explícita.

El motor se reemplaza con los dobles de la suite (``get_current_version`` y ``apply`` del runner
o del controlador). Los tests NO se corren automáticamente en este repo; se verifican con
``.venv/bin/python scripts/run_tests_direct.py tests.test_channel_consumers``.
"""

import pytest

from app.core.database import Database
from app.models.managed_database import ManagedDatabase
from app.services import release_channel_catalog as channel_codes
from app.services.integration_scope_catalog import (
    CODE_INTEGRATION_BLUEPRINT_NOT_ASSIGNED,
    CODE_INTEGRATION_SERVER_NOT_ALLOWED,
)
from tests.scope_helpers import sembrar_bd
from tests.test_api_managed_databases import _owner, _server
from tests.test_api_migration_delete_renumber import _blueprint, _managed_db
from tests.test_channel_resolver import _create_channel, _set_pointer
from tests.test_integration_ops_api import (  # noqa: F401  (fixtures reutilizadas)
    _apply_path,
    _apply_token_headers,
    _create_blueprint,
    _create_database_with_blueprint,
    _create_server,
    _public_context,
    _token_headers,
    integration_api,
    owner_issuer,
    recorded_apply,
)

MODELS_URL = "/api/v1/database-models"
DATABASES_URL = "/api/v1/managed-databases"
CODE_APPLY_REQUIRES_PROVISION = "managed_database.apply_requires_provision"
CODE_UNREVIEWED_CAPTURE = "migration.capture_unreviewed"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _code(response) -> str | None:
    """Código estable de un error del gateway (``detail.public_context.code``)."""
    return response.json()["detail"]["public_context"].get("code")


def _manual_channel(admin_client, slug: str, *, pointer: str | None, model_id: int) -> None:
    _create_channel(admin_client, name=slug, slug=slug, resolution_mode="manual")
    if pointer is not None:
        _set_pointer(model_id, slug, pointer)


def _inventory_names() -> set[str]:
    session = Database().get_declarative_base_session()
    try:
        return {row[0] for row in session.query(ManagedDatabase.name).all()}
    finally:
        session.close()


@pytest.fixture()
def provisioning(monkeypatch):
    """
    Aprovisionar sin motor: el adaptador solo registra el CREATE y ``apply`` registra el tope con
    el que lo llamaron. Devuelve las dos listas para afirmar sobre ellas.
    """
    import app.controllers.managed_database_controller as mdc
    from app.controllers.managed_migration_controller import ManagedMigrationController

    created_names: list[str] = []
    apply_calls: list[dict] = []

    class FakeAdapter:
        def create_database(self, name, charset=None, collation=None, owner=None):
            created_names.append(name)

    def fake_apply(self, db_id, *, up_to_version=None, **kwargs):
        apply_calls.append({"db_id": db_id, "up_to_version": up_to_version})
        return {"managed_database_id": db_id, "to_version": up_to_version}

    monkeypatch.setattr(mdc, "get_adapter", lambda target: FakeAdapter())
    monkeypatch.setattr(ManagedMigrationController, "apply", fake_apply)
    return {"created": created_names, "apply_calls": apply_calls}


def _provision(admin_client, *, sid: int, oid: int, name: str, model_id: int, **extra):
    payload = {
        "server_id": sid,
        "owner_id": oid,
        "name": name,
        "model_id": model_id,
        "apply_migrations": True,
    }
    payload.update(extra)
    return admin_client.post(f"{DATABASES_URL}?provision=true", json=payload)


# --------------------------------------------------------------------------- #
# Aprovisionar (4.1)                                                           #
# --------------------------------------------------------------------------- #
def test_provision_with_channel_creates_at_the_pointer_version(admin_client, provisioning):
    model_id = _blueprint(admin_client, "bp-prov-ok", ["0001", "0002", "0003"])
    _manual_channel(admin_client, "stable-prov", pointer="0002", model_id=model_id)
    sid = _server(admin_client, 5601)
    oid = _owner(admin_client, sid, "own_prov_ok")

    response = _provision(
        admin_client, sid=sid, oid=oid, name="prov_ch_ok", model_id=model_id, channel="stable-prov"
    )

    assert response.status_code == 201, response.text
    assert provisioning["apply_calls"][0]["up_to_version"] == "0002"
    resolved = response.json()["data"]["resolved_channel"]
    assert resolved["slug"] == "stable-prov"
    assert resolved["version"] == "0002"
    assert resolved["mode"] == "manual"


def test_provision_with_channel_and_target_version_is_rejected_before_creating_anything(
    admin_client, provisioning
):
    model_id = _blueprint(admin_client, "bp-prov-both", ["0001", "0002"])
    _manual_channel(admin_client, "stable-both", pointer="0002", model_id=model_id)
    sid = _server(admin_client, 5602)
    oid = _owner(admin_client, sid, "own_prov_both")

    response = _provision(
        admin_client, sid=sid, oid=oid, name="prov_both", model_id=model_id,
        channel="stable-both", target_version="0001",
    )

    assert response.status_code == 422, response.text
    assert _code(response) == channel_codes.CODE_TARGET_CONFLICT
    assert provisioning["created"] == []
    assert "prov_both" not in _inventory_names()


def test_provision_with_unknown_channel_is_404_and_creates_nothing(admin_client, provisioning):
    model_id = _blueprint(admin_client, "bp-prov-404", ["0001"])
    sid = _server(admin_client, 5603)
    oid = _owner(admin_client, sid, "own_prov_404")

    response = _provision(
        admin_client, sid=sid, oid=oid, name="prov_404", model_id=model_id, channel="no-existe"
    )

    assert response.status_code == 404, response.text
    assert provisioning["created"] == []
    assert "prov_404" not in _inventory_names()


def test_provision_with_unset_channel_fails_closed_and_does_not_fall_back_to_the_tip(
    admin_client, provisioning
):
    model_id = _blueprint(admin_client, "bp-prov-unset", ["0001", "0002"])
    _manual_channel(admin_client, "stable-unset", pointer=None, model_id=model_id)
    sid = _server(admin_client, 5604)
    oid = _owner(admin_client, sid, "own_prov_unset")

    response = _provision(
        admin_client, sid=sid, oid=oid, name="prov_unset", model_id=model_id,
        channel="stable-unset",
    )

    assert response.status_code == 409, response.text
    assert _code(response) == channel_codes.CODE_NO_RELEASED_VERSION
    assert provisioning["created"] == []
    assert provisioning["apply_calls"] == []
    assert "prov_unset" not in _inventory_names()


def test_provision_with_channel_requires_apply_migrations(admin_client, provisioning):
    model_id = _blueprint(admin_client, "bp-prov-noapply", ["0001"])
    _manual_channel(admin_client, "stable-noapply", pointer="0001", model_id=model_id)
    sid = _server(admin_client, 5605)
    oid = _owner(admin_client, sid, "own_prov_noapply")

    response = admin_client.post(
        f"{DATABASES_URL}?provision=true",
        json={
            "server_id": sid, "owner_id": oid, "name": "prov_noapply", "model_id": model_id,
            "channel": "stable-noapply",
        },
    )

    assert response.status_code == 422, response.text
    assert _code(response) == CODE_APPLY_REQUIRES_PROVISION
    assert provisioning["created"] == []


def test_provision_with_latest_channel_serves_the_newest_reviewed_version_not_the_unreviewed_tip(
    admin_client, monkeypatch
):
    """
    R-7: el puntero de ``latest`` solo cae sobre versiones REVISADAS. Con la punta sin revisar,
    el canal sirve la última revisada, así que aprovisionar nunca llega a aplicar la captura
    sin aprobar; cuando la aprueban, el puntero se mueve solo.
    """
    import app.controllers.managed_database_controller as mdc
    from app.services.db_admin.migrations import MigrationRunner

    class FakeAdapter:
        def create_database(self, *args, **kwargs):
            return None

    monkeypatch.setattr(mdc, "get_adapter", lambda target: FakeAdapter())
    monkeypatch.setattr(MigrationRunner, "get_current_version", lambda self, *a, **k: None)
    model_id = _blueprint(admin_client, "bp-prov-latest", ["0001"])
    capture = admin_client.post(
        f"{MODELS_URL}/{model_id}/migrations",
        json={
            "version": "0002",
            "name": "captura",
            "up_sql": "SELECT id FROM t0001",
            "capture_selects": True,
        },
    )
    assert capture.status_code == 201, capture.text
    assert capture.json()["data"]["reviewed"] is False  # premisa: la punta nace sin revisar
    _create_channel(admin_client, name="tip-prov", slug="tip-prov", resolution_mode="latest")
    sid = _server(admin_client, 5606)
    oid = _owner(admin_client, sid, "own_prov_latest")

    response = _provision(
        admin_client, sid=sid, oid=oid, name="prov_latest", model_id=model_id, channel="tip-prov"
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["resolved_channel"]["version"] == "0001"
    assert data["migration"].get("error_code") != CODE_UNREVIEWED_CAPTURE


# --------------------------------------------------------------------------- #
# apply-all (4.2)                                                              #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def batch_versions(monkeypatch):
    """Versión actual por nombre de BD, leída por el runner del lote."""
    from app.services.db_admin.migrations import MigrationRunner

    versions: dict[str, str | None] = {}
    monkeypatch.setattr(
        MigrationRunner,
        "get_current_version",
        lambda self, target, db_name, slug: versions.get(db_name),
    )
    return versions


def _apply_all_dry_run(admin_client, model_id: int, **query) -> dict:
    params = "&".join(f"{key}={value}" for key, value in {"dry_run": "true", **query}.items())
    return admin_client.post(f"{MODELS_URL}/{model_id}/migrations/apply-all?{params}")


def _pending_by_database(response) -> dict[str, list[str]]:
    assert response.status_code == 200, response.text
    return {
        item["database_name"]: item["pending_versions"]
        for item in response.json()["data"]["results"]
    }


def _two_databases(admin_client, slug: str, port: int, batch_versions):
    """
    Dos BDs del blueprint ``slug``: ``<prefijo>_a`` en 0001 y ``<prefijo>_b`` en 0004. El prefijo es
    el slug con ``_`` en lugar de ``-``: el slug admite guiones y el nombre de BD no.
    """
    model_id = _blueprint(admin_client, slug, ["0001", "0002", "0003", "0004", "0005"])
    database_prefix = slug.replace("-", "_")
    _managed_db(admin_client, model_id, port, f"{database_prefix}_a", cached="0001")
    _managed_db(admin_client, model_id, port + 1, f"{database_prefix}_b", cached="0004")
    batch_versions.update({f"{database_prefix}_a": "0001", f"{database_prefix}_b": "0004"})
    return model_id


def test_apply_all_target_version_bounds_every_database_and_never_rolls_back(
    admin_client, batch_versions
):
    model_id = _two_databases(admin_client, "bp-all-target", 5611, batch_versions)

    response = _apply_all_dry_run(admin_client, model_id, target_version="0003")

    pending = _pending_by_database(response)
    assert pending["bp_all_target_a"] == ["0002", "0003"]
    # R-9: la BD que ya está en 0004 supera el tope; no se revierte, simplemente no tiene pendientes.
    assert pending["bp_all_target_b"] == []
    assert response.json()["data"]["target_version"] == "0003"


def test_apply_all_channel_resolves_the_bound_and_reports_it(admin_client, batch_versions):
    model_id = _two_databases(admin_client, "bp-all-channel", 5621, batch_versions)
    _manual_channel(admin_client, "stable-all", pointer="0003", model_id=model_id)

    response = _apply_all_dry_run(admin_client, model_id, channel="stable-all")

    pending = _pending_by_database(response)
    assert pending["bp_all_channel_a"] == ["0002", "0003"]
    assert pending["bp_all_channel_b"] == []
    data = response.json()["data"]
    assert data["target_version"] == "0003"
    assert data["resolved_channel"]["slug"] == "stable-all"


def test_apply_all_without_target_keeps_applying_up_to_the_tip(admin_client, batch_versions):
    model_id = _two_databases(admin_client, "bp-all-plain", 5631, batch_versions)

    response = _apply_all_dry_run(admin_client, model_id)

    pending = _pending_by_database(response)
    assert pending["bp_all_plain_a"] == ["0002", "0003", "0004", "0005"]
    assert pending["bp_all_plain_b"] == ["0005"]
    assert response.json()["data"]["target_version"] is None
    assert response.json()["data"]["resolved_channel"] is None


def test_apply_all_channel_and_target_version_together_is_422(admin_client, batch_versions):
    model_id = _two_databases(admin_client, "bp-all-both", 5641, batch_versions)
    _manual_channel(admin_client, "stable-all-both", pointer="0003", model_id=model_id)

    response = _apply_all_dry_run(
        admin_client, model_id, channel="stable-all-both", target_version="0002"
    )

    assert response.status_code == 422, response.text
    assert _code(response) == channel_codes.CODE_TARGET_CONFLICT


def test_apply_all_unknown_target_version_is_422(admin_client, batch_versions):
    model_id = _two_databases(admin_client, "bp-all-unknown", 5651, batch_versions)

    response = _apply_all_dry_run(admin_client, model_id, target_version="0099")

    assert response.status_code == 422, response.text


def test_apply_all_unset_channel_fails_closed_without_planning_anything(
    admin_client, batch_versions
):
    model_id = _two_databases(admin_client, "bp-all-unset", 5661, batch_versions)
    _manual_channel(admin_client, "stable-all-unset", pointer=None, model_id=model_id)

    response = _apply_all_dry_run(admin_client, model_id, channel="stable-all-unset")

    assert response.status_code == 409, response.text
    assert _code(response) == channel_codes.CODE_NO_RELEASED_VERSION


# --------------------------------------------------------------------------- #
# Integración: apply-forward con canal (4.3)                                   #
# --------------------------------------------------------------------------- #
def _integration_database(admin_client, server_port: int, name: str, pointer: str | None):
    """Base con blueprint real de cinco versiones y un canal manual apuntando a ``pointer``."""
    server_id = _create_server(admin_client, server_port)
    database_id = _create_database_with_blueprint(
        admin_client,
        server_id,
        name=name,
        migrations=[
            {"version": f"000{n}", "name": f"m{n}", "up_sql": f"CREATE TABLE {name}_{n} (id INT)"}
            for n in range(1, 6)
        ],
        environment_slug="development",
    )
    session = Database().get_declarative_base_session()
    try:
        model_id = session.get(ManagedDatabase, database_id).model_id
    finally:
        session.close()
    # El slug de canal admite solo minúsculas, dígitos y guiones; el nombre de BD usa ``_``.
    channel_slug = "ch-" + name.replace("_", "-")
    _manual_channel(admin_client, channel_slug, pointer=pointer, model_id=model_id)
    return server_id, database_id, channel_slug


def test_integration_apply_with_channel_resolves_to_the_pointer_version(
    integration_api, admin_client, owner_issuer, recorded_apply  # noqa: F811
):
    server_id, database_id, channel_slug = _integration_database(
        admin_client, 5671, "int_chan_ok", "0004"
    )

    response = integration_api.post(
        _apply_path(database_id),
        json={"channel": channel_slug},
        headers=_apply_token_headers(owner_issuer, server_id),
    )

    assert response.status_code == 200, response.text
    assert recorded_apply[0]["up_to_version"] == "0004"
    assert response.json()["data"]["resolved_channel"]["slug"] == channel_slug


def test_integration_apply_channel_and_version_are_exclusive(
    integration_api, admin_client, owner_issuer, recorded_apply  # noqa: F811
):
    server_id, database_id, channel_slug = _integration_database(
        admin_client, 5672, "int_chan_both", "0004"
    )

    response = integration_api.post(
        _apply_path(database_id),
        json={"channel": channel_slug, "version": "0003"},
        headers=_apply_token_headers(owner_issuer, server_id),
    )

    assert response.status_code == 422, response.text
    assert _public_context(response)["code"] == channel_codes.CODE_TARGET_CONFLICT
    assert recorded_apply == []


def test_integration_apply_channel_older_than_current_reuses_the_not_forward_check(
    integration_api, admin_client, owner_issuer, recorded_apply  # noqa: F811
):
    # ``recorded_apply`` fija la versión actual en 0002: un canal en 0001 es hacia atrás.
    server_id, database_id, channel_slug = _integration_database(
        admin_client, 5673, "int_chan_back", "0001"
    )

    response = integration_api.post(
        _apply_path(database_id),
        json={"channel": channel_slug},
        headers=_apply_token_headers(owner_issuer, server_id),
    )

    assert response.status_code == 422, response.text
    assert _public_context(response)["code"] == "integration.migration_target_not_forward"
    assert recorded_apply == []


def test_integration_apply_with_unset_channel_is_409_and_applies_nothing(
    integration_api, admin_client, owner_issuer, recorded_apply  # noqa: F811
):
    server_id, database_id, channel_slug = _integration_database(
        admin_client, 5674, "int_chan_unset", None
    )

    response = integration_api.post(
        _apply_path(database_id),
        json={"channel": channel_slug},
        headers=_apply_token_headers(owner_issuer, server_id),
    )

    assert response.status_code == 409, response.text
    assert _public_context(response)["code"] == channel_codes.CODE_NO_RELEASED_VERSION
    assert recorded_apply == []


def test_integration_apply_with_channel_keeps_the_token_server_scope(
    integration_api, admin_client, owner_issuer, recorded_apply  # noqa: F811
):
    _server_id, database_id, channel_slug = _integration_database(
        admin_client, 5675, "int_chan_scope", "0004"
    )
    other_server_id = _create_server(admin_client, 5676)

    response = integration_api.post(
        _apply_path(database_id),
        json={"channel": channel_slug},
        headers=_apply_token_headers(owner_issuer, other_server_id),
    )

    assert response.status_code == 403, response.text
    assert _public_context(response)["code"] == CODE_INTEGRATION_SERVER_NOT_ALLOWED
    assert recorded_apply == []


def test_integration_apply_with_channel_on_a_database_without_blueprint_is_404(
    integration_api, admin_client, owner_issuer, recorded_apply  # noqa: F811
):
    server_id = _create_server(admin_client, 5677)
    database_id = sembrar_bd(server_id=server_id, name="int_chan_nobp")
    _create_channel(admin_client, name="ch-nobp", slug="ch-nobp", resolution_mode="latest")

    response = integration_api.post(
        _apply_path(database_id),
        json={"channel": "ch-nobp"},
        headers=_apply_token_headers(owner_issuer, server_id),
    )

    assert response.status_code == 404, response.text
    assert _public_context(response)["code"] == CODE_INTEGRATION_BLUEPRINT_NOT_ASSIGNED
    assert recorded_apply == []


# --------------------------------------------------------------------------- #
# Integración: lectura del canal de la base (scope blueprint.read_channel)     #
# --------------------------------------------------------------------------- #
def _channel_read_path(database_id: int, channel_slug: str) -> str:
    return f"/api/v1/integration/databases/{database_id}/channels/{channel_slug}"


def _channel_read_headers(issuer_id: int, server_id: int, **allowlists) -> dict:
    from app.services.integration_scope_catalog import IntegrationScope

    return _token_headers(
        issuer_id,
        [IntegrationScope.BLUEPRINT_READ_CHANNEL],
        server_ids=(server_id,),
        **allowlists,
    )


def test_integration_channel_read_returns_the_pointer_version(
    integration_api, admin_client, owner_issuer  # noqa: F811
):
    server_id, database_id, channel_slug = _integration_database(
        admin_client, 5690, "int_chr_ok", "0004"
    )

    response = integration_api.get(
        _channel_read_path(database_id, channel_slug),
        headers=_channel_read_headers(owner_issuer, server_id),
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["slug"] == channel_slug
    assert data["mode"] == "manual"
    assert data["version"] == "0004"
    assert set(data) == {"slug", "mode", "version", "source"}


def test_integration_channel_read_with_unset_pointer_is_409_and_not_an_empty_value(
    integration_api, admin_client, owner_issuer  # noqa: F811
):
    server_id, database_id, channel_slug = _integration_database(
        admin_client, 5691, "int_chr_unset", None
    )

    response = integration_api.get(
        _channel_read_path(database_id, channel_slug),
        headers=_channel_read_headers(owner_issuer, server_id),
    )

    assert response.status_code == 409, response.text
    assert _public_context(response)["code"] == channel_codes.CODE_NO_RELEASED_VERSION


def test_integration_channel_read_of_an_unknown_channel_is_404(
    integration_api, admin_client, owner_issuer  # noqa: F811
):
    server_id, database_id, _channel_slug = _integration_database(
        admin_client, 5692, "int_chr_unknown", "0004"
    )

    response = integration_api.get(
        _channel_read_path(database_id, "no-such-channel"),
        headers=_channel_read_headers(owner_issuer, server_id),
    )

    assert response.status_code == 404, response.text


def test_integration_channel_read_on_a_database_without_blueprint_is_404(
    integration_api, admin_client, owner_issuer  # noqa: F811
):
    server_id = _create_server(admin_client, 5693)
    database_id = sembrar_bd(server_id=server_id, name="int_chr_nobp")
    _create_channel(admin_client, name="ch-chr-nobp", slug="ch-chr-nobp", resolution_mode="latest")

    response = integration_api.get(
        _channel_read_path(database_id, "ch-chr-nobp"),
        headers=_channel_read_headers(owner_issuer, server_id),
    )

    assert response.status_code == 404, response.text
    assert _public_context(response)["code"] == CODE_INTEGRATION_BLUEPRINT_NOT_ASSIGNED


def test_integration_channel_read_keeps_the_token_server_scope(
    integration_api, admin_client, owner_issuer  # noqa: F811
):
    _server_id, database_id, channel_slug = _integration_database(
        admin_client, 5694, "int_chr_scope", "0004"
    )
    other_server_id = _create_server(admin_client, 5695)

    response = integration_api.get(
        _channel_read_path(database_id, channel_slug),
        headers=_channel_read_headers(owner_issuer, other_server_id),
    )

    assert response.status_code == 403, response.text
    assert _public_context(response)["code"] == CODE_INTEGRATION_SERVER_NOT_ALLOWED


def test_integration_channel_read_keeps_the_token_blueprint_allowlist(
    integration_api, admin_client, owner_issuer  # noqa: F811
):
    from app.services.integration_scope_catalog import CODE_INTEGRATION_BLUEPRINT_NOT_ALLOWED

    server_id, database_id, channel_slug = _integration_database(
        admin_client, 5696, "int_chr_bp_allow", "0004"
    )
    unrelated_blueprint_id = _create_blueprint("Unrelated Chr Blueprint")

    response = integration_api.get(
        _channel_read_path(database_id, channel_slug),
        headers=_channel_read_headers(
            owner_issuer, server_id, blueprint_ids=(unrelated_blueprint_id,)
        ),
    )

    assert response.status_code == 403, response.text
    assert _public_context(response)["code"] == CODE_INTEGRATION_BLUEPRINT_NOT_ALLOWED


def test_integration_channel_read_needs_its_own_scope(
    integration_api, admin_client, owner_issuer  # noqa: F811
):
    from app.services.integration_scope_catalog import (
        CODE_INTEGRATION_SCOPE_MISSING,
        IntegrationScope,
    )

    server_id, database_id, channel_slug = _integration_database(
        admin_client, 5697, "int_chr_noscope", "0004"
    )
    headers = _token_headers(
        owner_issuer, [IntegrationScope.BLUEPRINT_READ_ASSIGNED], server_ids=(server_id,)
    )

    response = integration_api.get(_channel_read_path(database_id, channel_slug), headers=headers)

    assert response.status_code == 403, response.text
    assert _public_context(response)["code"] == CODE_INTEGRATION_SCOPE_MISSING


# --------------------------------------------------------------------------- #
# Integración: lectura del canal por blueprint (sin base de datos)             #
# --------------------------------------------------------------------------- #
def _blueprint_channel_read_path(model_id: int, channel_slug: str) -> str:
    return f"/api/v1/integration/blueprints/{model_id}/channels/{channel_slug}"


def _blueprint_id_of(database_id: int) -> int:
    session = Database().get_declarative_base_session()
    try:
        return session.get(ManagedDatabase, database_id).model_id
    finally:
        session.close()


def test_integration_blueprint_channel_read_with_blueprint_allowlist_returns_the_pointer(
    integration_api, admin_client, owner_issuer  # noqa: F811
):
    server_id, database_id, channel_slug = _integration_database(
        admin_client, 5710, "int_bch_ok", "0004"
    )
    model_id = _blueprint_id_of(database_id)

    response = integration_api.get(
        _blueprint_channel_read_path(model_id, channel_slug),
        headers=_channel_read_headers(owner_issuer, server_id, blueprint_ids=(model_id,)),
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["slug"] == channel_slug
    assert data["version"] == "0004"
    assert set(data) == {"slug", "mode", "version", "source"}


def test_integration_blueprint_channel_read_outside_the_blueprint_allowlist_is_403(
    integration_api, admin_client, owner_issuer  # noqa: F811
):
    from app.services.integration_scope_catalog import CODE_INTEGRATION_BLUEPRINT_NOT_ALLOWED

    server_id, database_id, channel_slug = _integration_database(
        admin_client, 5711, "int_bch_deny", "0004"
    )
    model_id = _blueprint_id_of(database_id)
    unrelated_blueprint_id = _create_blueprint("Unrelated Bch Blueprint")

    response = integration_api.get(
        _blueprint_channel_read_path(model_id, channel_slug),
        headers=_channel_read_headers(
            owner_issuer, server_id, blueprint_ids=(unrelated_blueprint_id,)
        ),
    )

    assert response.status_code == 403, response.text
    assert _public_context(response)["code"] == CODE_INTEGRATION_BLUEPRINT_NOT_ALLOWED


def test_integration_blueprint_channel_read_without_blueprint_allowlist_keeps_the_server_rule(
    integration_api, admin_client, owner_issuer  # noqa: F811
):
    server_id, database_id, channel_slug = _integration_database(
        admin_client, 5712, "int_bch_srv", "0004"
    )
    model_id = _blueprint_id_of(database_id)
    other_server_id = _create_server(admin_client, 5713)

    allowed = integration_api.get(
        _blueprint_channel_read_path(model_id, channel_slug),
        headers=_channel_read_headers(owner_issuer, server_id),
    )
    denied = integration_api.get(
        _blueprint_channel_read_path(model_id, channel_slug),
        headers=_channel_read_headers(owner_issuer, other_server_id),
    )

    assert allowed.status_code == 200, allowed.text
    assert denied.status_code == 403, denied.text
    assert _public_context(denied)["code"] == CODE_INTEGRATION_SERVER_NOT_ALLOWED


def test_integration_blueprint_channel_read_with_unset_pointer_is_409(
    integration_api, admin_client, owner_issuer  # noqa: F811
):
    server_id, database_id, channel_slug = _integration_database(
        admin_client, 5714, "int_bch_unset", None
    )
    model_id = _blueprint_id_of(database_id)

    response = integration_api.get(
        _blueprint_channel_read_path(model_id, channel_slug),
        headers=_channel_read_headers(owner_issuer, server_id, blueprint_ids=(model_id,)),
    )

    assert response.status_code == 409, response.text
    assert _public_context(response)["code"] == channel_codes.CODE_NO_RELEASED_VERSION


def test_integration_blueprint_channel_read_needs_its_own_scope(
    integration_api, admin_client, owner_issuer  # noqa: F811
):
    from app.services.integration_scope_catalog import (
        CODE_INTEGRATION_SCOPE_MISSING,
        IntegrationScope,
    )

    server_id, database_id, channel_slug = _integration_database(
        admin_client, 5715, "int_bch_noscope", "0004"
    )
    model_id = _blueprint_id_of(database_id)
    headers = _token_headers(
        owner_issuer,
        [IntegrationScope.BLUEPRINT_READ_ASSIGNED],
        server_ids=(server_id,),
        blueprint_ids=(model_id,),
    )

    response = integration_api.get(
        _blueprint_channel_read_path(model_id, channel_slug), headers=headers
    )

    assert response.status_code == 403, response.text
    assert _public_context(response)["code"] == CODE_INTEGRATION_SCOPE_MISSING
