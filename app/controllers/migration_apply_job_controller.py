"""
Jobs de ``apply-all`` en background (``MigrationApplyJob``).

Es el mismo ``apply-all`` que ``ManagedMigrationController.apply_all``, partido en el tiempo:

1. **En el request** (``start_job``): ``_prepare_apply_all`` corre todas las validaciones y el
   preflight destructivo todo-o-nada. Si algo falla, el request devuelve ese error y no se crea
   ninguna fila. Si pasa, se crea el job ``pending`` y se encola.
2. **En el worker** (``run_job``): ``_apply_all_item`` por cada base, en serie, grabando el ítem
   de cada una apenas termina; al final ``_finish_apply_all`` agrega los omitidos y escribe la
   misma auditoría ``migration.apply_all`` que el camino síncrono.

Así el resultado por base es, por construcción, el mismo que devuelve el endpoint síncrono.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from app.controllers.managed_migration_controller import (
    ManagedMigrationController,
    _ApplyAllItemOutcome,
    _ApplyAllPlan,
)
from app.core.actor import Actor, identity_of
from app.core.context import current_http_identifier
from app.core.database import Database
from app.core.environments import DB_HOST, DB_NAME, DB_PASS, DB_PORT, DB_USER
from app.core.logger import get_logger
from app.models.database_model import DatabaseModel
from app.models.migration_apply_job import (
    MIGRATION_APPLY_JOB_ACTIVE,
    MIGRATION_APPLY_JOB_DONE,
    MIGRATION_APPLY_JOB_FAILED,
    MIGRATION_APPLY_JOB_INTERRUPTED,
    MIGRATION_APPLY_JOB_PARTIAL,
    MIGRATION_APPLY_JOB_PENDING,
    MIGRATION_APPLY_JOB_RUNNING,
    MigrationApplyJob,
)
from app.services import migration_apply_job_catalog as jcodes
from app.services import migration_apply_job_runner
from app.exceptions import AppHttpException

logger = get_logger(__name__)

# Guard in-process por BLUEPRINT: el "¿hay un job activo?" y el INSERT del nuevo tienen que ser
# atómicos, o dos requests simultáneos pasan los dos el chequeo. Con ``WORKERS=1`` (requisito
# de toda la familia de runners, ver ``clone_batch_runner``) esto alcanza; no es un lock
# cross-proceso.
_model_guards: dict[int, threading.Lock] = {}
_model_guards_lock = threading.Lock()

_INTERRUPTED_MESSAGE = "El proceso se reinició mientras el job estaba en ejecución."
_ERROR_MAX_LENGTH = 500


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _model_guard(model_id: int) -> threading.Lock:
    with _model_guards_lock:
        guard = _model_guards.get(model_id)
        if guard is None:
            guard = threading.Lock()
            _model_guards[model_id] = guard
        return guard


def _dump_items(items: list[dict]) -> str:
    # ``default=str``: el ítem es el dict del apply-all síncrono (tipos planos); el ``default``
    # solo evita que un tipo inesperado tire abajo el worker a mitad del recorrido.
    return json.dumps(items, default=str)


class MigrationApplyJobController:
    def __init__(self):
        self.db = Database(DB_NAME, DB_USER, DB_PASS, DB_HOST, DB_PORT)
        self.migrations = ManagedMigrationController()

    def _session(self):
        return self.db.get_declarative_base_session()

    # ------------------------------------------------------------------ #
    # Request                                                             #
    # ------------------------------------------------------------------ #
    def start_job(
        self,
        model_id: int,
        *,
        max_databases: int,
        database_ids: list[int] | None = None,
        environment_id: int | None = None,
        force: bool = False,
        on_failure: str = "auto",
        target_version: str | None = None,
        channel: str | None = None,
        admin: "dict | Actor | None" = None,
        confirm_token: str | None = None,
        confirm_phrase: str | None = None,
    ) -> dict:
        """
        Valida el lote como el ``apply-all`` síncrono, crea el job y lo encola.

        Sin ``dry_run``: la simulación ya es rápida por el endpoint síncrono y no tiene sentido
        encolarla.

        **Orden deliberado:** el chequeo de job activo va ANTES del preflight, para que un 409
        nunca llegue después de validar el ``confirm_token`` del lote.
        """
        with _model_guard(model_id):
            self._guard_no_active_job(model_id)
            plan = self.migrations._prepare_apply_all(
                model_id,
                max_databases=max_databases,
                database_ids=database_ids,
                environment_id=environment_id,
                force=force,
                dry_run=False,
                on_failure=on_failure,
                target_version=target_version,
                channel=channel,
                admin=admin,
                confirm_token=confirm_token,
                confirm_phrase=confirm_phrase,
            )
            job_id = self._create_job(plan, admin)

        try:
            migration_apply_job_runner.enqueue(job_id, plan, admin)
        except Exception as exc:
            # Un job ``pending`` que nadie va a correr bloquearía el blueprint con
            # ``already_active`` hasta el próximo reinicio: se cierra ``failed`` en el acto.
            logger.error("Job de apply-all %s: no se pudo encolar", job_id, exc_info=True)
            self._update_job(
                job_id,
                status=MIGRATION_APPLY_JOB_FAILED,
                error=f"No se pudo encolar: {type(exc).__name__}"[:_ERROR_MAX_LENGTH],
                finished_at=_utcnow(),
            )
            raise AppHttpException(
                message="No se pudo encolar la aplicación masiva; intente de nuevo.",
                status_code=503,
                context={"model_id": model_id, "job_id": job_id},
                public_context={"code": jcodes.CODE_JOB_ENQUEUE_FAILED, "job_id": job_id},
            ) from exc
        return self.get_job(model_id, job_id)

    def _guard_no_active_job(self, model_id: int) -> None:
        session = self._session()
        try:
            active_job_id = (
                session.query(MigrationApplyJob.id)
                .filter(
                    MigrationApplyJob.model_id == model_id,
                    MigrationApplyJob.status.in_(MIGRATION_APPLY_JOB_ACTIVE),
                )
                .order_by(MigrationApplyJob.id.asc())
                .limit(1)
                .scalar()
            )
        finally:
            session.close()
        if active_job_id is not None:
            raise AppHttpException(
                message=(
                    f"Ya hay un apply-all en curso para este blueprint (job {active_job_id})."
                ),
                status_code=409,
                context={"model_id": model_id},
                public_context={
                    "code": jcodes.CODE_JOB_ALREADY_ACTIVE,
                    "active_job_id": active_job_id,
                },
            )

    def _create_job(self, plan: _ApplyAllPlan, admin: "dict | Actor | None") -> int:
        admin_id, admin_username = identity_of(admin)
        channel_slug = plan.resolved_channel["slug"] if plan.resolved_channel else None
        session = self._session()
        try:
            job = MigrationApplyJob(
                model_id=plan.model_id,
                environment_id=plan.environment_id,
                target_version=plan.target_version,
                channel_slug=channel_slug,
                on_failure=plan.on_failure,
                force=plan.force,
                total_databases=plan.total,
                matched_databases=plan.matched,
                planned=len(plan.dbs),
                processed=0,
                failed=0,
                results=_dump_items([]),
                created_by_admin_id=admin_id,
                created_by_username=admin_username,
                origin_request_id=current_http_identifier.get(),
                status=MIGRATION_APPLY_JOB_PENDING,
            )
            session.add(job)
            session.commit()
            return job.id
        finally:
            session.close()

    def get_job(self, model_id: int, job_id: int) -> dict:
        """Estado y progreso del job. 404 si no existe o si es de OTRO blueprint."""
        session = self._session()
        try:
            job = session.get(MigrationApplyJob, job_id)
            if job is None or job.model_id != model_id:
                raise AppHttpException(
                    message="Job de apply-all no encontrado.",
                    status_code=404,
                    context={"model_id": model_id, "job_id": job_id},
                )
            return self._job_dict(job)
        finally:
            session.close()

    def list_jobs(self, model_id: int, *, limit: int) -> list[dict]:
        """Jobs del blueprint, del más nuevo al más viejo (sin ``results``: es el listado)."""
        session = self._session()
        try:
            if session.get(DatabaseModel, model_id) is None:
                raise AppHttpException(
                    message="Blueprint no encontrado.",
                    status_code=404,
                    context={"model_id": model_id},
                )
            jobs = (
                session.query(MigrationApplyJob)
                .filter(MigrationApplyJob.model_id == model_id)
                .order_by(MigrationApplyJob.id.desc())
                .limit(limit)
                .all()
            )
            return [self._job_dict(job, include_results=False) for job in jobs]
        finally:
            session.close()

    @staticmethod
    def _job_dict(job: MigrationApplyJob, *, include_results: bool = True) -> dict:
        job_dict = {
            "id": job.id,
            "model_id": job.model_id,
            "status": job.status,
            "environment_id": job.environment_id,
            "target_version": job.target_version,
            "channel_slug": job.channel_slug,
            "on_failure": job.on_failure,
            "force": job.force,
            "total_databases": job.total_databases,
            "matched_databases": job.matched_databases,
            "planned": job.planned,
            "processed": job.processed,
            "failed": job.failed,
            "error": job.error,
            "created_by_username": job.created_by_username,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }
        if include_results:
            job_dict["results"] = json.loads(job.results or "[]")
        return job_dict

    # ------------------------------------------------------------------ #
    # Worker                                                              #
    # ------------------------------------------------------------------ #
    def run_job(self, job_id: int, plan: _ApplyAllPlan, admin: "dict | Actor | None") -> None:
        """
        Recorre las bases del plan en serie y graba cada ítem apenas termina.

        Re-fija el Request ID del request que creó el job: ``ThreadPoolExecutor.submit`` no
        propaga ContextVars, y sin esto los logs y la auditoría del worker saldrían sin
        correlación con el request que autorizó el lote.
        """
        origin_request_id = self._origin_request_id(job_id)
        request_id_token = current_http_identifier.set(origin_request_id)
        try:
            self._run_job(job_id, plan, admin)
        finally:
            current_http_identifier.reset(request_id_token)

    def _origin_request_id(self, job_id: int) -> str | None:
        session = self._session()
        try:
            return (
                session.query(MigrationApplyJob.origin_request_id)
                .filter(MigrationApplyJob.id == job_id)
                .scalar()
            )
        finally:
            session.close()

    def _run_job(self, job_id: int, plan: _ApplyAllPlan, admin: "dict | Actor | None") -> None:
        outcomes: list[_ApplyAllItemOutcome] = []
        try:
            # Dentro del ``try``: si esta escritura falla, el job se cierra ``failed`` en vez de
            # quedar ``pending`` bloqueando el blueprint hasta el próximo reinicio.
            self._update_job(
                job_id, status=MIGRATION_APPLY_JOB_RUNNING, started_at=_utcnow()
            )
            for database in plan.dbs:
                outcome = self.migrations._apply_all_item(plan, database, admin=admin)
                outcomes.append(outcome)
                processed_items = [processed.item for processed in outcomes]
                self._update_job(
                    job_id,
                    processed=len(outcomes),
                    failed=sum(1 for item in processed_items if not item["ok"]),
                    results=_dump_items(processed_items),
                )
            response = self.migrations._finish_apply_all(plan, outcomes, admin=admin)
        except Exception as exc:  # noqa: BLE001 — el job tiene que quedar cerrado, no colgado
            logger.error("Job de apply-all %s: error inesperado", job_id, exc_info=True)
            self._update_job(
                job_id,
                status=MIGRATION_APPLY_JOB_FAILED,
                error=f"Error inesperado: {type(exc).__name__}"[:_ERROR_MAX_LENGTH],
                finished_at=_utcnow(),
            )
            return

        # ``failed`` y el estado final cuentan solo las bases RECORRIDAS. Los ítems omitidos por
        # la capa 2 (``error_code`` de scope) también van en ``results``, igual que en el
        # síncrono, pero no se intentaron: no convierten un recorrido limpio en ``partial``.
        failed_count = sum(1 for outcome in outcomes if not outcome.item["ok"])
        final_status = MIGRATION_APPLY_JOB_PARTIAL if failed_count else MIGRATION_APPLY_JOB_DONE
        self._update_job(
            job_id,
            status=final_status,
            processed=len(outcomes),
            failed=failed_count,
            results=_dump_items(response["results"]),
            finished_at=_utcnow(),
        )

    def _update_job(self, job_id: int, **values) -> None:
        session = self._session()
        try:
            session.query(MigrationApplyJob).filter(MigrationApplyJob.id == job_id).update(
                {getattr(MigrationApplyJob, column): value for column, value in values.items()},
                synchronize_session=False,
            )
            session.commit()
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    # Arranque                                                            #
    # ------------------------------------------------------------------ #
    def sweep_interrupted(self) -> int:
        """
        Cierra los jobs que un reinicio dejó ``pending`` o ``running``.

        Incluye ``pending``: el plan vivía en memoria del proceso que murió, así que un job que
        no llegó a arrancar ya no puede arrancar. **No se re-encola nada**: relanzarlo es una
        decisión del operador (las bases que ya terminaron reportan cero pendientes).
        """
        session = self._session()
        try:
            hung_job_ids = [
                job_id
                for (job_id,) in session.query(MigrationApplyJob.id)
                .filter(MigrationApplyJob.status.in_(MIGRATION_APPLY_JOB_ACTIVE))
                .all()
            ]
            if not hung_job_ids:
                return 0
            session.query(MigrationApplyJob).filter(
                MigrationApplyJob.id.in_(hung_job_ids)
            ).update(
                {
                    MigrationApplyJob.status: MIGRATION_APPLY_JOB_INTERRUPTED,
                    MigrationApplyJob.error: _INTERRUPTED_MESSAGE,
                    MigrationApplyJob.finished_at: _utcnow(),
                },
                synchronize_session=False,
            )
            session.commit()
            logger.warning("Jobs de apply-all marcados 'interrupted': %s", hung_job_ids)
            return len(hung_job_ids)
        finally:
            session.close()
