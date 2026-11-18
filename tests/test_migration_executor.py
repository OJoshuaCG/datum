"""
Tests del ejecutor de revisiones (``migration_executor``): paridad ``alembic`` vs ``native``.

``native`` existe para sacar las migraciones de la cola global ``_ALEMBIC_LOCK``. Lo que NO
puede cambiar es lo que queda escrito en la base del tercero: la tabla de versión (forma,
filas) y el esquema después de cada paso. El test de paridad corre la MISMA secuencia con
cada ejecutor sobre una SQLite nueva y compara una foto completa tras cada paso; cualquier
divergencia (otra fila, otro DDL de la tabla de versión, otro error) lo rompe.

Contra SQLite, igual que el ciclo de ``test_migration_runner.py``: los motores reales los
cubre CI.
"""

from __future__ import annotations

import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect

from app.core.environments import MIGRATION_EXECUTOR_ALEMBIC, MIGRATION_EXECUTOR_NATIVE
from app.models.enums import EngineType
from app.services.db_admin import migration_executor
from app.services.db_admin.migrations import MigrationRunner, MigrationSpec, version_table_name
from tests.test_migration_runner import _NO_MANAGED_DB, _no_checkpoint

_EXECUTORS = (MIGRATION_EXECUTOR_ALEMBIC, MIGRATION_EXECUTOR_NATIVE)
_SLUG = "parity"
# Segundos que un hilo espera al otro en la barrera: si las corridas estuvieran
# serializadas, la segunda nunca llegaría mientras la primera espera.
_BARRIER_TIMEOUT_SECONDS = 10
# Cuánto se espera para concluir que un hilo quedó bloqueado en el lock.
_BLOCKED_PROBE_SECONDS = 0.5


def _spec(version: str, up_sql: str, down_sql: str | None) -> MigrationSpec:
    return MigrationSpec(
        id=int(version), version=version, name=f"m{version}", up_sql=up_sql,
        up_sql_mysql=None, up_sql_postgresql=None, down_sql=down_sql, checksum="x",
    )


_PARITY_SPECS = [
    _spec("0001", "CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(50))",
          "DROP TABLE users"),
    _spec("0002", "ALTER TABLE users ADD COLUMN phone VARCHAR(20)",
          "ALTER TABLE users DROP COLUMN phone"),
    _spec("0003", "CREATE TABLE orders (id INTEGER PRIMARY KEY)", "DROP TABLE orders"),
    # Falla a mitad: la primera sentencia crea una tabla, la segunda apunta a una que no
    # existe. Lo que quede (versión y esquema) tiene que ser igual con los dos ejecutores.
    _spec("0004",
          "CREATE TABLE invoices (id INTEGER PRIMARY KEY);\n"
          "INSERT INTO table_that_does_not_exist (id) VALUES (1);",
          "DROP TABLE invoices"),
    # Dos versiones válidas (``VERSION_RE`` exige 4-10 dígitos) cuyo orden como texto es el
    # inverso del numérico ("10000" < "9999" como texto): la cadena se arma por orden numérico.
    _spec("9999", "CREATE TABLE credits (id INTEGER PRIMARY KEY)", "DROP TABLE credits"),
    _spec("10000", "CREATE TABLE refunds (id INTEGER PRIMARY KEY)", "DROP TABLE refunds"),
]


@contextmanager
def _revision_config(runner: MigrationRunner, connection, specs: list[MigrationSpec]):
    with tempfile.TemporaryDirectory() as tmp:
        versions_dir = Path(tmp) / "versions"
        versions_dir.mkdir()
        runner._write_revision_files(versions_dir, specs, EngineType.mysql, _NO_MANAGED_DB)
        yield runner._make_config(versions_dir, connection, version_table_name(_SLUG))


def _snapshot(connection) -> dict:
    """Foto de lo que un ejecutor deja escrito en la base."""
    version_table = version_table_name(_SLUG)
    inspector = inspect(connection)
    tables = sorted(inspector.get_table_names())
    version_table_ddl = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (version_table,)
    ).scalar()
    version_rows = (
        sorted(row[0] for row in connection.exec_driver_sql(
            f'SELECT version_num FROM "{version_table}"'
        ))
        if version_table in tables
        else None
    )
    columns = {table: [column["name"] for column in inspector.get_columns(table)]
               for table in tables}
    if connection.in_transaction():
        connection.commit()
    return {
        "version_rows": version_rows,
        "version_table_ddl": version_table_ddl,
        "columns": columns,
    }


def _run_step(connection, step) -> dict:
    """Corre un paso y devuelve la foto, más el tipo de error si el paso falló."""
    error_type = None
    try:
        step()
    except Exception as exc:  # noqa: BLE001 — el tipo de error es parte de la paridad
        error_type = type(exc).__name__
        if connection.in_transaction():
            connection.rollback()
    return {"error": error_type, **_snapshot(connection)}


def _parity_trace(executor: str) -> list[tuple[str, dict]]:
    runner = MigrationRunner()
    database_file = tempfile.mktemp(suffix=".db")
    engine = create_engine(f"sqlite:///{database_file}")
    version_table = version_table_name(_SLUG)
    trace: list[tuple[str, dict]] = []

    with engine.connect() as connection, _revision_config(
        runner, connection, _PARITY_SPECS
    ) as cfg:
        steps = [
            ("upgrade 0001 (INSERT)",
             lambda: migration_executor.upgrade(cfg, "0001", executor=executor)),
            ("upgrade 0003 (dos pasos, UPDATE)",
             lambda: migration_executor.upgrade(cfg, "0003", executor=executor)),
            ("upgrade 0004 (falla a mitad)",
             lambda: migration_executor.upgrade(cfg, "0004", executor=executor)),
            ("downgrade -1 (relativo)",
             lambda: migration_executor.downgrade(cfg, "-1", executor=executor)),
            ("downgrade base (DELETE)",
             lambda: migration_executor.downgrade(cfg, "base", executor=executor)),
            ("stamp 0002 desde base",
             lambda: migration_executor.stamp(cfg, "0002", executor=executor)),
            ("stamp 0001 hacia atrás",
             lambda: migration_executor.stamp(cfg, "0001", executor=executor)),
            ("puntero huérfano",
             lambda: connection.exec_driver_sql(
                 f"UPDATE \"{version_table}\" SET version_num = '0099'")),
            ("stamp sin purge con puntero huérfano (falla)",
             lambda: migration_executor.stamp(cfg, "0002", executor=executor)),
            ("stamp con purge con puntero huérfano",
             lambda: migration_executor.stamp(cfg, "0003", purge=True, executor=executor)),
            ("downgrade inexistente (falla)",
             lambda: migration_executor.downgrade(cfg, "0042", executor=executor)),
        ]
        for label, step in steps:
            trace.append((label, _run_step(connection, step)))
    engine.dispose()
    return trace


@_no_checkpoint()
def test_native_leaves_the_same_version_table_and_schema_as_alembic():
    alembic_trace = _parity_trace(MIGRATION_EXECUTOR_ALEMBIC)
    native_trace = _parity_trace(MIGRATION_EXECUTOR_NATIVE)

    for (label, alembic_state), (_label, native_state) in zip(
        alembic_trace, native_trace, strict=True
    ):
        assert native_state == alembic_state, label


@_no_checkpoint()
def test_parity_trace_exercises_every_version_table_write():
    """Guarda contra una paridad vacía: la secuencia sí pasa por INSERT, UPDATE, DELETE,
    purge y los dos errores. Si un cambio la vaciara, los dos ejecutores "coincidirían" sin
    probar nada."""
    trace = dict(_parity_trace(MIGRATION_EXECUTOR_ALEMBIC))

    assert trace["upgrade 0001 (INSERT)"]["version_rows"] == ["0001"]
    assert trace["upgrade 0003 (dos pasos, UPDATE)"]["version_rows"] == ["0003"]
    assert trace["upgrade 0004 (falla a mitad)"]["error"] is not None
    assert trace["upgrade 0004 (falla a mitad)"]["version_rows"] == ["0003"]
    assert trace["downgrade -1 (relativo)"]["version_rows"] == ["0002"]
    assert trace["downgrade base (DELETE)"]["version_rows"] == []
    assert "users" not in trace["downgrade base (DELETE)"]["columns"]
    assert trace["stamp 0002 desde base"]["version_rows"] == ["0002"]
    assert "users" not in trace["stamp 0002 desde base"]["columns"]  # stamp no corre DDL
    assert trace["stamp sin purge con puntero huérfano (falla)"]["error"] == "CommandError"
    assert trace["stamp sin purge con puntero huérfano (falla)"]["version_rows"] == ["0099"]
    assert trace["stamp con purge con puntero huérfano"]["version_rows"] == ["0003"]
    assert trace["downgrade inexistente (falla)"]["error"] is not None
    assert "_pkc" in trace["upgrade 0001 (INSERT)"]["version_table_ddl"]


@_no_checkpoint()
def test_native_upgrade_orders_versions_numerically():
    runner = MigrationRunner()
    engine = create_engine(f"sqlite:///{tempfile.mktemp(suffix='.db')}")
    specs = [spec for spec in _PARITY_SPECS if spec.version != "0004"]
    with engine.connect() as connection, _revision_config(runner, connection, specs) as cfg:
        migration_executor.upgrade(cfg, "0003", executor=MIGRATION_EXECUTOR_NATIVE)
        migration_executor.upgrade(cfg, "10000", executor=MIGRATION_EXECUTOR_NATIVE)

        assert runner._read_current(connection, version_table_name(_SLUG)) == "10000"
        # Con una cadena por orden de texto, 10000 quedaría antes que 9999 y ``credits`` no
        # se crearía al subir hasta 10000.
        table_names = inspect(connection).get_table_names()
        assert "credits" in table_names
        assert "refunds" in table_names
    engine.dispose()


@_no_checkpoint()
def test_native_does_not_wait_for_the_global_alembic_lock():
    runner = MigrationRunner()
    engine = create_engine(f"sqlite:///{tempfile.mktemp(suffix='.db')}")
    outcome: dict[str, object] = {}

    def apply_with_native() -> None:
        with engine.connect() as connection, _revision_config(
            runner, connection, _PARITY_SPECS[:1]
        ) as cfg:
            migration_executor.upgrade(cfg, "0001", executor=MIGRATION_EXECUTOR_NATIVE)
            outcome["version"] = runner._read_current(connection, version_table_name(_SLUG))

    with migration_executor._ALEMBIC_LOCK:
        worker = threading.Thread(target=apply_with_native)
        worker.start()
        worker.join(timeout=_BARRIER_TIMEOUT_SECONDS)
        finished_while_lock_was_held = not worker.is_alive()

    worker.join()
    engine.dispose()
    assert finished_while_lock_was_held
    assert outcome["version"] == "0001"


@_no_checkpoint()
def test_alembic_executor_still_waits_for_the_global_lock():
    runner = MigrationRunner()
    engine = create_engine(f"sqlite:///{tempfile.mktemp(suffix='.db')}")

    def apply_with_alembic() -> None:
        with engine.connect() as connection, _revision_config(
            runner, connection, _PARITY_SPECS[:1]
        ) as cfg:
            migration_executor.upgrade(cfg, "0001", executor=MIGRATION_EXECUTOR_ALEMBIC)

    with migration_executor._ALEMBIC_LOCK:
        worker = threading.Thread(target=apply_with_alembic)
        worker.start()
        worker.join(timeout=_BLOCKED_PROBE_SECONDS)
        blocked_while_lock_was_held = worker.is_alive()

    worker.join()
    engine.dispose()
    assert blocked_while_lock_was_held


@_no_checkpoint()
def test_two_native_runs_on_different_databases_overlap():
    """Las dos corridas se esperan mutuamente DENTRO del DDL (una función SQLite que espera
    en una barrera). Si el ejecutor las serializara, la barrera vencería y el DDL fallaría."""
    runner = MigrationRunner()
    barrier = threading.Barrier(2, timeout=_BARRIER_TIMEOUT_SECONDS)
    specs = [_spec("0001", "CREATE TABLE t AS SELECT gw_wait_for_peer() AS x", "DROP TABLE t")]
    versions: dict[int, str | None] = {}
    errors: list[BaseException] = []

    def wait_for_peer() -> int:
        barrier.wait()
        return 1

    def apply_on_own_database(worker_index: int) -> None:
        engine = create_engine(f"sqlite:///{tempfile.mktemp(suffix='.db')}")

        @event.listens_for(engine, "connect")
        def register_barrier_function(dbapi_connection, _record):
            dbapi_connection.create_function("gw_wait_for_peer", 0, wait_for_peer)

        try:
            with engine.connect() as connection, _revision_config(
                runner, connection, specs
            ) as cfg:
                migration_executor.upgrade(cfg, "0001", executor=MIGRATION_EXECUTOR_NATIVE)
                versions[worker_index] = runner._read_current(
                    connection, version_table_name(_SLUG)
                )
        except BaseException as exc:  # noqa: BLE001 — se reporta en el hilo principal
            errors.append(exc)
        finally:
            engine.dispose()

    workers = [threading.Thread(target=apply_on_own_database, args=(index,)) for index in (0, 1)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert errors == []
    assert versions == {0: "0001", 1: "0001"}


def test_unknown_executor_is_rejected():
    with pytest.raises(ValueError):
        migration_executor.upgrade(None, "0001", executor="threads")  # type: ignore[arg-type]
