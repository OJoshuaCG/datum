"""
``apply-all`` en background (``POST/GET .../migrations/apply-all-jobs``).

El motor remoto no existe en los tests: ``_run_apply`` (lo único del recorrido que toca el
motor) se reemplaza por un doble, y ``enqueue`` corre el worker en un hilo NUEVO y lo espera,
para que el test sea determinista y además pruebe lo que el pool real no propaga (ContextVars).
"""

import threading

import pytest

from app.controllers.managed_migration_controller import ManagedMigrationController
from app.controllers.migration_apply_job_controller import MigrationApplyJobController
from app.core.actor import Actor
from app.core.context import current_http_identifier
from app.models.migration_apply_job import (
    MIGRATION_APPLY_JOB_DONE,
    MIGRATION_APPLY_JOB_INTERRUPTED,
    MIGRATION_APPLY_JOB_PENDING,
    MIGRATION_APPLY_JOB_RUNNING,
    MigrationApplyJob,
)
from app.services import migration_apply_job_runner
from tests.test_api_model_migrations import (
    _create_migration,
    _managed_db_for_model,
    _new_model,
)

_APPLIED_RESULT = {
    "migration_id": 1,
    "version": "0001",
    "status": "applied",
    "error": None,
    "error_code": None,
    "execution_ms": 5,
    "resumed": False,
    "resumed_from_statement": None,
    "statement_total": 1,
    "failed_at_statement_index": None,
}


def _jobs_url(model_id: int) -> str:
    return f"/api/v1/database-models/{model_id}/migrations/apply-all-jobs"


@pytest.fixture()
def worker_in_fresh_thread(monkeypatch):
    """``enqueue`` corre el worker en un hilo nuevo (sin el contexto del request) y lo espera."""

    def run_in_fresh_thread(job_id, plan, admin):
        worker = threading.Thread(
            target=migration_apply_job_runner._run, args=(job_id, plan, admin)
        )
        worker.start()
        worker.join(timeout=30)
        assert not worker.is_alive(), "el worker del job no terminó"

    monkeypatch.setattr(migration_apply_job_runner, "enqueue", run_in_fresh_thread)


@pytest.fixture()
def run_apply_calls(monkeypatch):
    """Doble de ``_run_apply``: aplica sin motor y registra quién lo llamó y con qué Request ID."""
    calls: list[dict] = []

    def fake_run_apply(self, db_id, **kwargs):
        calls.append(
            {
                "db_id": db_id,
                "admin": kwargs.get("admin"),
                "request_id": current_http_identifier.get(),
            }
        )
        return {"failed": False, "results": [dict(_APPLIED_RESULT)]}

    monkeypatch.setattr(ManagedMigrationController, "_run_apply", fake_run_apply)
    return calls


def _blueprint_with_one_database(admin_client, slug: str, port: int) -> tuple[int, int]:
    model_id = _new_model(admin_client, slug=slug, name=slug.title())
    r = _create_migration(admin_client, model_id)
    assert r.status_code == 201, r.text
    db_id = _managed_db_for_model(admin_client, model_id, port=port)
    return model_id, db_id


def _insert_job(model_id: int, status: str) -> int:
    session = MigrationApplyJobController()._session()
    try:
        job = MigrationApplyJob(
            model_id=model_id,
            on_failure="auto",
            total_databases=0,
            matched_databases=0,
            planned=0,
            results="[]",
            status=status,
        )
        session.add(job)
        session.commit()
        return job.id
    finally:
        session.close()


def _job_status(job_id: int) -> str:
    session = MigrationApplyJobController()._session()
    try:
        return session.get(MigrationApplyJob, job_id).status
    finally:
        session.close()


def test_job_reports_the_same_items_as_the_sync_apply_all(
    admin_client, worker_in_fresh_thread, run_apply_calls
):
    """El ítem por base del job es, por construcción, el mismo que el del apply-all síncrono."""
    model_id, db_id = _blueprint_with_one_database(admin_client, "jobparity", 5601)
    params = {"force": True}

    sync_response = admin_client.post(
        f"/api/v1/database-models/{model_id}/migrations/apply-all", params=params
    )
    assert sync_response.status_code == 200, sync_response.text
    sync_data = sync_response.json()["data"]

    job_response = admin_client.post(_jobs_url(model_id), params=params)
    assert job_response.status_code == 202, job_response.text
    job_id = job_response.json()["data"]["id"]

    detail = admin_client.get(f"{_jobs_url(model_id)}/{job_id}")
    assert detail.status_code == 200, detail.text
    job = detail.json()["data"]
    assert job["status"] == MIGRATION_APPLY_JOB_DONE
    assert job["planned"] == 1
    assert job["processed"] == 1
    assert job["failed"] == 0
    assert job["total_databases"] == sync_data["total_databases"]
    assert job["matched_databases"] == sync_data["matched_databases"]
    assert job["results"] == sync_data["results"]
    assert job["results"][0]["managed_database_id"] == db_id
    assert job["results"][0]["ok"] is True


def test_worker_keeps_the_live_actor_and_the_origin_request_id(
    admin_client, worker_in_fresh_thread, run_apply_calls
):
    """
    El worker recibe el ``Actor`` vivo (un dict reconstruido no pasa ``can_at`` y las versiones
    destructivas se rechazarían) y re-fija el Request ID que el hilo nuevo no hereda.
    """
    model_id, _db_id = _blueprint_with_one_database(admin_client, "jobactor", 5602)

    job_response = admin_client.post(_jobs_url(model_id), params={"force": True})
    assert job_response.status_code == 202, job_response.text

    assert len(run_apply_calls) == 1
    worker_call = run_apply_calls[0]
    # ``scope.can_at`` niega todo lo que no sea una instancia de ``Actor``.
    assert isinstance(worker_call["admin"], Actor), type(worker_call["admin"])
    session = MigrationApplyJobController()._session()
    try:
        job = session.get(MigrationApplyJob, job_response.json()["data"]["id"])
        assert job.origin_request_id is not None
        assert worker_call["request_id"] == job.origin_request_id
    finally:
        session.close()


def test_a_failing_database_leaves_the_job_partial(admin_client, worker_in_fresh_thread, monkeypatch):
    """Un error inesperado en una base queda en su ítem y el job cierra ``partial``, no colgado."""
    model_id, _db_id = _blueprint_with_one_database(admin_client, "jobfail", 5603)

    def exploding_run_apply(self, db_id, **kwargs):
        raise RuntimeError("motor caído")

    monkeypatch.setattr(ManagedMigrationController, "_run_apply", exploding_run_apply)

    job_response = admin_client.post(_jobs_url(model_id), params={"force": True})
    assert job_response.status_code == 202, job_response.text
    job = admin_client.get(f"{_jobs_url(model_id)}/{job_response.json()['data']['id']}").json()[
        "data"
    ]
    assert job["status"] == "partial"
    assert job["processed"] == 1
    assert job["failed"] == 1
    assert job["results"][0]["ok"] is False
    assert job["results"][0]["error"] == "error inesperado: RuntimeError"


def test_second_job_of_the_same_blueprint_is_rejected_while_one_is_active(
    admin_client, worker_in_fresh_thread, run_apply_calls
):
    model_id, _db_id = _blueprint_with_one_database(admin_client, "jobbusy", 5604)
    active_job_id = _insert_job(model_id, MIGRATION_APPLY_JOB_RUNNING)

    r = admin_client.post(_jobs_url(model_id), params={"force": True})

    assert r.status_code == 409, r.text
    public_context = r.json()["detail"]["public_context"]
    assert public_context["code"] == "migration_apply_job.already_active"
    assert public_context["active_job_id"] == active_job_id
    assert run_apply_calls == []


def test_a_job_that_cannot_be_enqueued_is_closed_and_does_not_block_the_blueprint(
    admin_client, run_apply_calls, monkeypatch
):
    """Si ``enqueue`` falla, el job queda ``failed`` en el acto y no bloquea un nuevo pedido."""
    model_id, _db_id = _blueprint_with_one_database(admin_client, "jobenqueue", 5606)

    def broken_enqueue(job_id, plan, admin):
        raise RuntimeError("pool apagado")

    monkeypatch.setattr(migration_apply_job_runner, "enqueue", broken_enqueue)

    r = admin_client.post(_jobs_url(model_id), params={"force": True})

    assert r.status_code == 503, r.text
    public_context = r.json()["detail"]["public_context"]
    assert public_context["code"] == "migration_apply_job.enqueue_failed"
    failed_job_id = public_context["job_id"]
    assert _job_status(failed_job_id) == "failed"
    assert run_apply_calls == []

    retry = admin_client.post(_jobs_url(model_id), params={"force": True})
    assert retry.status_code == 503, retry.text
    assert retry.json()["detail"]["public_context"]["code"] == "migration_apply_job.enqueue_failed"


def test_an_invalid_batch_creates_no_job(admin_client, worker_in_fresh_thread, run_apply_calls):
    """Las validaciones corren en el request: si el lote es inválido no queda ninguna fila."""
    model_id, _db_id = _blueprint_with_one_database(admin_client, "jobinvalid", 5605)

    r = admin_client.post(_jobs_url(model_id), params={"on_failure": "no-existe"})

    assert r.status_code == 422, r.text
    listing = admin_client.get(_jobs_url(model_id))
    assert listing.status_code == 200, listing.text
    assert listing.json()["data"] == []
    assert run_apply_calls == []


def test_job_of_another_blueprint_is_not_found(admin_client):
    model_id = _new_model(admin_client, slug="jobowner", name="JobOwner")
    other_model_id = _new_model(admin_client, slug="jobother", name="JobOther")
    job_id = _insert_job(model_id, MIGRATION_APPLY_JOB_DONE)

    r = admin_client.get(f"{_jobs_url(other_model_id)}/{job_id}")

    assert r.status_code == 404, r.text


def test_listing_returns_newest_first_without_results(admin_client):
    model_id = _new_model(admin_client, slug="joblist", name="JobList")
    older_job_id = _insert_job(model_id, MIGRATION_APPLY_JOB_DONE)
    newer_job_id = _insert_job(model_id, MIGRATION_APPLY_JOB_DONE)

    r = admin_client.get(_jobs_url(model_id))

    assert r.status_code == 200, r.text
    jobs = r.json()["data"]
    assert [job["id"] for job in jobs] == [newer_job_id, older_job_id]
    assert all(job["results"] is None for job in jobs)


def test_sweep_marks_pending_and_running_jobs_interrupted(admin_client):
    """El plan vivía en memoria: tras un reinicio ni un ``pending`` puede arrancar ya."""
    model_id = _new_model(admin_client, slug="jobsweep", name="JobSweep")
    pending_job_id = _insert_job(model_id, MIGRATION_APPLY_JOB_PENDING)
    running_job_id = _insert_job(model_id, MIGRATION_APPLY_JOB_RUNNING)
    done_job_id = _insert_job(model_id, MIGRATION_APPLY_JOB_DONE)

    swept = migration_apply_job_runner.sweep_interrupted()

    assert swept == 2
    assert _job_status(pending_job_id) == MIGRATION_APPLY_JOB_INTERRUPTED
    assert _job_status(running_job_id) == MIGRATION_APPLY_JOB_INTERRUPTED
    assert _job_status(done_job_id) == MIGRATION_APPLY_JOB_DONE
