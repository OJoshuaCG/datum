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

El esqueleto (pool perezoso, log del worker y del barrido) es el compartido de
``app/services/inprocess_job_runner.py`` (``P-23``).
"""

from __future__ import annotations

from typing import Any

from app.core.environments import MIGRATION_APPLY_MAX_WORKERS
from app.core.logger import get_logger
from app.services.inprocess_job_runner import InProcessJobPool, run_logged, sweep_logged

logger = get_logger(__name__)

_pool = InProcessJobPool(
    max_workers=MIGRATION_APPLY_MAX_WORKERS, thread_name_prefix="migration-apply"
)


def enqueue(job_id: int, plan: Any, admin: Any) -> None:
    """Encola el recorrido del job. ``plan`` y ``admin`` viajan en memoria (ver el docstring)."""
    _pool.submit(_run, job_id, plan, admin)


def _run(job_id: int, plan: Any, admin: Any) -> None:
    """Punto de entrada del worker: delega en el controller (importado tarde por los ciclos)."""
    from app.controllers.migration_apply_job_controller import MigrationApplyJobController

    run_logged(
        logger,
        "Job de apply-all %s falló de forma inesperada",
        job_id,
        lambda: MigrationApplyJobController().run_job(job_id, plan, admin),
    )


def sweep_interrupted() -> int:
    """Cierra los jobs que un reinicio dejó ``pending``/``running``. Se llama en el ``lifespan``."""
    from app.controllers.migration_apply_job_controller import MigrationApplyJobController

    return sweep_logged(
        logger,
        "No se pudo barrer jobs de apply-all interrumpidos",
        lambda: MigrationApplyJobController().sweep_interrupted(),
    )
