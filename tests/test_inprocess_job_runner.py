"""
Esqueleto compartido de los workers in-process (``app/services/inprocess_job_runner.py``).

Los cinco runners (clon, lote, collation, export, apply-all) se apoyan en él: si una de estas
garantías se rompe, se rompe en los cinco a la vez.
"""

import logging
import threading

import pytest

from app.services import (
    clone_batch_runner,
    clone_runner,
    collation_conversion_runner,
    export_runner,
    migration_apply_job_runner,
)
from app.services.inprocess_job_runner import (
    InProcessJobPool,
    KeyedGuards,
    run_logged,
    sweep_logged,
)

_TEST_LOGGER = logging.getLogger("tests.inprocess_job_runner")


class _RecordingHandler(logging.Handler):
    """Guarda los registros emitidos (``caplog`` no está disponible en ``run_tests_direct``)."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture()
def recorded_logs():
    handler = _RecordingHandler()
    previous_level = _TEST_LOGGER.level
    _TEST_LOGGER.addHandler(handler)
    _TEST_LOGGER.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        _TEST_LOGGER.removeHandler(handler)
        _TEST_LOGGER.setLevel(previous_level)


def test_keyed_guards_return_the_same_lock_per_key_and_distinct_locks_per_key():
    guards = KeyedGuards()

    assert guards.get("1:db") is guards.get("1:db")
    assert guards.get("1:db") is not guards.get("2:db")


def test_keyed_guards_hand_out_one_lock_per_key_under_concurrency():
    guards = KeyedGuards()
    thread_count = 8
    start = threading.Barrier(thread_count)
    seen: list[threading.Lock] = []
    seen_lock = threading.Lock()

    def grab() -> None:
        start.wait()
        lock = guards.get("same-key")
        with seen_lock:
            seen.append(lock)

    threads = [threading.Thread(target=grab) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(seen) == thread_count
    assert len({id(lock) for lock in seen}) == 1


def test_pool_is_created_lazily_once_and_runs_the_worker():
    pool = InProcessJobPool(max_workers=2, thread_name_prefix="test-pool")
    assert pool._executor is None

    first = pool.submit(lambda value: value * 2, 21)
    executor_after_first_submit = pool._executor
    second = pool.submit(lambda: threading.current_thread().name)

    assert first.result(timeout=5) == 42
    assert second.result(timeout=5).startswith("test-pool")
    assert pool._executor is executor_after_first_submit
    pool._executor.shutdown(wait=True)


def test_pool_keeps_at_least_one_worker_when_misconfigured_to_zero():
    pool = InProcessJobPool(max_workers=0, thread_name_prefix="test-zero")

    assert pool.submit(lambda: "ran").result(timeout=5) == "ran"
    assert pool._executor._max_workers == 1
    pool._executor.shutdown(wait=True)


def test_run_logged_logs_the_failure_with_the_job_id_and_does_not_raise(recorded_logs):
    def explode() -> None:
        raise RuntimeError("boom")

    result = run_logged(_TEST_LOGGER, "Job %s falló", 7, explode)

    assert result is None
    assert [record.getMessage() for record in recorded_logs] == ["Job 7 falló"]
    assert recorded_logs[0].levelno == logging.ERROR
    assert recorded_logs[0].exc_info is not None


def test_run_logged_runs_the_work_once_when_it_succeeds(recorded_logs):
    calls: list[int] = []

    run_logged(_TEST_LOGGER, "Job %s falló", 7, lambda: calls.append(1))

    assert calls == [1]
    assert recorded_logs == []


def test_sweep_logged_returns_the_count_or_zero_when_the_sweep_fails(recorded_logs):
    def broken_sweep() -> int:
        raise RuntimeError("db down")

    assert sweep_logged(_TEST_LOGGER, "Barrido falló", lambda: 3) == 3
    assert sweep_logged(_TEST_LOGGER, "Barrido falló", broken_sweep) == 0
    assert [record.getMessage() for record in recorded_logs] == ["Barrido falló"]
    assert recorded_logs[0].levelno == logging.WARNING


def test_each_runner_keeps_its_own_guards():
    """Los guards no se mezclan entre features: cada runner tiene su propio espacio de claves."""
    assert clone_runner.target_guard("1:db") is clone_runner.target_guard("1:db")
    assert export_runner.database_guard("1:db") is export_runner.database_guard("1:db")
    assert collation_conversion_runner.database_guard("1:db") is not (
        export_runner.database_guard("1:db")
    )
    assert clone_batch_runner.server_guard(1) is clone_batch_runner.server_guard(1)


def test_each_runner_has_its_own_pool():
    runner_modules = (
        clone_runner,
        clone_batch_runner,
        collation_conversion_runner,
        export_runner,
        migration_apply_job_runner,
    )
    distinct_pools = {id(module._pool) for module in runner_modules}

    assert len(distinct_pools) == len(runner_modules)


def test_export_enqueue_releases_the_admission_slot_when_submit_fails(monkeypatch):
    def broken_submit(worker, *args):
        raise RuntimeError("pool apagado")

    monkeypatch.setattr(export_runner._pool, "submit", broken_submit)
    admitted_before = export_runner.inflight_count()

    with pytest.raises(RuntimeError):
        export_runner.enqueue(987654)

    assert export_runner.inflight_count() == admitted_before
