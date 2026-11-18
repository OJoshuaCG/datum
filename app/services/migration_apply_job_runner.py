"""
Subsistema de ejecución asíncrona de los jobs de ``apply-all`` (``MigrationApplyJob``).

``MIGRATION_APPLY_MAX_WORKERS`` (default 1) gobierna cuántos JOBS corren a la vez (siempre de
blueprints distintos: el controller rechaza un segundo job activo del mismo blueprint). Dentro
de un job las bases van en serie, igual que el ``apply-all`` síncrono. Que dos jobs corran de
verdad en paralelo requiere ``MIGRATION_EXECUTOR=native``; con ``alembic`` esperan en
``_ALEMBIC_LOCK`` (``app/services/db_admin/migration_executor.py``).

Qué se le pasa al worker y por qué en memoria: el plan ya validado (``_ApplyAllPlan``) y el
``Actor`` VIVO del request. El ``Actor`` no se puede reconstruir desde la fila como hacen los
lotes de clon (un dict ``{id, username}``): el guard destructivo de cada base llama ``can_at``,
que un dict no tiene, y las versiones destructivas se rechazarían. El precio es el de toda la
familia: NO es una cola durable; un reinicio deja el job ``interrupted`` (barrido en el
``lifespan``) y se relanza a mano.

NOTA (``P-23``): QUINTA copia del esqueleto de runner (clon, lote de clon, collation, export y
éste). No se unificó acá por la misma razón que en ``clone_batch_runner``: no atar una feature
nueva a un refactor de cuatro módulos en producción.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.core.environments import MIGRATION_APPLY_MAX_WORKERS
from app.core.logger import get_logger

logger = get_logger(__name__)

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        # Doble chequeo bajo lock: dos requests concurrentes en el arranque podrían crear dos
        # pools, y el segundo se perdería con sus hilos ya levantados.
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=max(1, MIGRATION_APPLY_MAX_WORKERS),
                    thread_name_prefix="migration-apply",
                )
    return _executor


def enqueue(job_id: int, plan: Any, admin: Any) -> None:
    """Encola el recorrido del job. ``plan`` y ``admin`` viajan en memoria (ver el docstring)."""
    _get_executor().submit(_run, job_id, plan, admin)


def _run(job_id: int, plan: Any, admin: Any) -> None:
    """Punto de entrada del worker: delega en el controller (importado tarde por los ciclos)."""
    from app.controllers.migration_apply_job_controller import MigrationApplyJobController

    try:
        MigrationApplyJobController().run_job(job_id, plan, admin)
    except Exception:  # noqa: BLE001 — el worker nunca debe morir silenciosamente sin log
        logger.error("Job de apply-all %s falló de forma inesperada", job_id, exc_info=True)


def sweep_interrupted() -> int:
    """Cierra los jobs que un reinicio dejó ``pending``/``running``. Se llama en el ``lifespan``."""
    from app.controllers.migration_apply_job_controller import MigrationApplyJobController

    try:
        return MigrationApplyJobController().sweep_interrupted()
    except Exception:  # noqa: BLE001 — el arranque nunca debe romperse por esto
        logger.warning("No se pudo barrer jobs de apply-all interrumpidos", exc_info=True)
        return 0
