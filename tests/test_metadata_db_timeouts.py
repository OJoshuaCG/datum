"""
Timeouts de la BD de METADATOS del gateway.

Sin tope, una consulta lenta o un pool agotado cuelgan el request indefinidamente. El límite real
lo pone el motor (``max_statement_time`` en MariaDB, ``max_execution_time`` en MySQL,
``statement_timeout`` en PostgreSQL); los timeouts de socket del cliente son la red de seguridad.
"""

import app.core.database as database_mod


class _FakeCursor:
    def __init__(self, executed: list[str]) -> None:
        self._executed = executed

    def execute(self, statement: str) -> None:
        self._executed.append(statement)

    def close(self) -> None:
        pass


class _FakeDbapiConnection:
    """Lo único que mira el listener: la versión del handshake y un cursor."""

    def __init__(self, server_info: str) -> None:
        self._server_info = server_info
        self.executed: list[str] = []

    def get_server_info(self) -> str:
        return self._server_info

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.executed)


def test_mariadb_session_gets_max_statement_time_in_seconds(monkeypatch):
    monkeypatch.setattr(database_mod, "DB_STATEMENT_TIMEOUT_S", 30)
    connection = _FakeDbapiConnection("11.8.2-MariaDB-ubu2404")
    database_mod._apply_server_statement_timeout(connection, None)
    assert connection.executed == ["SET SESSION max_statement_time = 30"]


def test_mysql_session_gets_max_execution_time_in_milliseconds(monkeypatch):
    """MySQL no tiene ``max_statement_time``: un SET de esa variable rompería cada conexión."""
    monkeypatch.setattr(database_mod, "DB_STATEMENT_TIMEOUT_S", 30)
    connection = _FakeDbapiConnection("8.0.36")
    database_mod._apply_server_statement_timeout(connection, None)
    assert connection.executed == ["SET SESSION max_execution_time = 30000"]


def test_zero_statement_timeout_sets_nothing(monkeypatch):
    monkeypatch.setattr(database_mod, "DB_STATEMENT_TIMEOUT_S", 0)
    connection = _FakeDbapiConnection("11.8.2-MariaDB")
    database_mod._apply_server_statement_timeout(connection, None)
    assert connection.executed == []


def test_mysql_engine_has_socket_timeouts_and_pool_timeout(monkeypatch):
    monkeypatch.setattr(database_mod, "DB_CONNECT_TIMEOUT_S", 10)
    monkeypatch.setattr(database_mod, "DB_READ_TIMEOUT_S", 60)
    monkeypatch.setattr(database_mod, "DB_WRITE_TIMEOUT_S", 60)
    monkeypatch.setattr(database_mod, "DB_POOL_TIMEOUT_S", 10)
    kwargs = database_mod._get_engine_kwargs("mysql")
    assert kwargs["pool_timeout"] == 10
    connect_args = kwargs["connect_args"]
    assert connect_args["connect_timeout"] == 10
    assert connect_args["read_timeout"] == 60
    assert connect_args["write_timeout"] == 60
    # Lo que ya estaba se conserva: la zona UTC de la sesión.
    assert "time_zone = '+00:00'" in connect_args["init_command"]


def test_postgresql_engine_sets_statement_timeout_in_milliseconds(monkeypatch):
    monkeypatch.setattr(database_mod, "DB_STATEMENT_TIMEOUT_S", 30)
    connect_args = database_mod._get_engine_kwargs("postgresql")["connect_args"]
    assert connect_args["options"] == "-c timezone=UTC -c statement_timeout=30000"


def test_postgresql_engine_without_statement_timeout_keeps_only_the_timezone(monkeypatch):
    monkeypatch.setattr(database_mod, "DB_STATEMENT_TIMEOUT_S", 0)
    connect_args = database_mod._get_engine_kwargs("postgresql")["connect_args"]
    assert connect_args["options"] == "-c timezone=UTC"
