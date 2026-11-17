"""
``GET /authz/scope-readiness`` y la limitación F-17.

La resolución a nivel servidor mira solo las BDs inventariadas; el reporte lo declara con UN campo
booleano de primer nivel, siempre true, y sigue sin abrir una sola conexión al motor.
"""

import app.core.remote_engine as remote_engine
import app.services.db_admin.factory as factory
from tests.scope_helpers import env_id, sembrar_bd

_URL = "/api/v1/authz/scope-readiness"


def test_readiness_flags_inventory_only_resolution(admin_client):
    r = admin_client.get(_URL)
    assert r.status_code == 200, r.text
    datos = r.json()["data"]
    assert datos["server_resolution_inventory_only"] is True


def test_the_flag_is_always_true_regardless_of_the_inventory(admin_client):
    sembrar_bd(environment_id=env_id("development"))
    antes = admin_client.get(_URL).json()["data"]
    sembrar_bd(environment_id=None, name="sin_entorno")
    despues = admin_client.get(_URL).json()["data"]
    assert antes["ready"] is True
    assert despues["ready"] is False
    assert antes["server_resolution_inventory_only"] is True
    assert despues["server_resolution_inventory_only"] is True


def test_readiness_opens_zero_engine_connections(admin_client, monkeypatch):
    """Listar el motor al autorizar está descartado (decisión R2-1): el reporte no conecta."""

    def _prohibido(*args, **kwargs):
        raise AssertionError("scope-readiness no debe abrir conexiones al motor")

    monkeypatch.setattr(remote_engine, "get_engine", _prohibido)
    monkeypatch.setattr(factory, "get_adapter", _prohibido)
    sembrar_bd(environment_id=env_id("production"))
    r = admin_client.get(_URL)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["server_resolution_inventory_only"] is True


def test_the_flag_is_the_only_new_top_level_field(admin_client):
    datos = admin_client.get(_URL).json()["data"]
    assert set(datos) == {
        "total_databases",
        "unclassified_databases",
        "ready",
        "fallback_environment_slug",
        "servers",
        "server_resolution_inventory_only",
    }


# --------------------------------------------------------------------------- #
# Per-server resolution (``AuthzController.scope_readiness`` in               #
# app/controllers/authz_controller.py loads environments once, not per server) #
# --------------------------------------------------------------------------- #
def _create_server(admin_client, server_payload) -> int:
    response = admin_client.post("/api/v1/servers", json=server_payload())
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _server_row(datos: dict, server_id: int) -> dict:
    return next(row for row in datos["servers"] if row["server_id"] == server_id)


def test_a_fully_classified_server_resolves_to_its_most_protected_database(
    admin_client, server_payload
):
    server_id = _create_server(admin_client, server_payload)
    sembrar_bd(server_id=server_id, environment_id=env_id("development"), name="bd_dev")
    sembrar_bd(server_id=server_id, environment_id=env_id("production"), name="bd_prod")

    row = _server_row(admin_client.get(_URL).json()["data"], server_id)

    assert row["databases"] == 2
    assert row["unclassified"] == 0
    assert row["derived_from_gap"] is False
    assert row["derived_environment_slug"] == "production"


def test_a_server_with_an_unclassified_database_falls_back_to_the_most_protected(
    admin_client, server_payload
):
    server_id = _create_server(admin_client, server_payload)
    sembrar_bd(server_id=server_id, environment_id=env_id("development"), name="bd_dev")
    sembrar_bd(server_id=server_id, environment_id=None, name="bd_sin_entorno")

    datos = admin_client.get(_URL).json()["data"]
    row = _server_row(datos, server_id)

    assert row["databases"] == 2
    assert row["unclassified"] == 1
    assert row["derived_from_gap"] is True
    assert row["derived_environment_slug"] == datos["fallback_environment_slug"]
    assert row["derived_environment_slug"] is not None
