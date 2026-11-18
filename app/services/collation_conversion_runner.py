"""
Subsistema de ejecución asíncrona de jobs de conversión de charset/collation.

MISMO patrón que ``app/services/clone_runner.py`` (worker IN-PROCESS con
``ThreadPoolExecutor``, estado en la BD de metadatos, barrido de ``running`` huérfanos en el
``lifespan``), sobre el esqueleto compartido de ``app/services/inprocess_job_runner.py``
(``P-23``). Es un módulo SEPARADO porque el pool, los guards y el barrido son propios de la
conversión: no compite por hilos con el clon.

POR QUÉ ASÍNCRONO: ``ALTER TABLE ... CONVERT TO CHARACTER SET`` REESCRIBE la tabla completa
(la doc de MySQL lo confirma: cambia el default de la tabla *y* todas las columnas de
caracteres), así que en tablas grandes tarda minutos u horas y bloquea. No cabe en una
llamada HTTP síncrona.

NO es durable: si el proceso se reinicia, los jobs ``running`` quedan ``interrupted``
(barrido de arranque) y se reintentan a mano. Una cola durable es endurecimiento futuro —
misma limitación conocida que el clon.

Este módulo es deliberadamente delgado: administra el pool, un guard in-process por BD y el
barrido de arranque. El pipeline real vive en
``CollationConversionController.run_job`` para mantener juntas la lógica de sesión/negocio.
"""

from __future__ import annotations

import threading

from app.core.environments import COLLATION_CONVERSION_MAX_WORKERS
from app.core.logger import get_logger
from app.services.inprocess_job_runner import (
    InProcessJobPool,
    KeyedGuards,
    run_logged,
    sweep_logged,
)

logger = get_logger(__name__)

_pool = InProcessJobPool(
    max_workers=COLLATION_CONVERSION_MAX_WORKERS, thread_name_prefix="collation"
)

# Guards in-process por BD (clave física "server_id:db"). Serializan dos conversiones
# concurrentes de la MISMA BD dentro de este proceso. La serialización CROSS-PROCESO real la
# da el advisory lock del motor, que el pipeline sostiene UNA vez sobre una conexión dedicada
# durante TODAS las fases (BD → tablas → objetos) — ver ``MigrationRunner.advisory_lock``.
# Comparte espacio de claves con el clon y con schema-comparisons (misma ``lock_key``
# derivada del ``managed_database_id`` o de la clave sintética negativa), así que una
# conversión y un clon sobre la misma BD física NO pueden pisarse.
_db_guards = KeyedGuards()


def database_guard(db_ref: str) -> threading.Lock:
    """Lock in-process para una BD (misma instancia por ``db_ref``)."""
    return _db_guards.get(db_ref)


def enqueue(job_id: int) -> None:
    """Encola la ejecución del job en el pool de hilos."""
    _pool.submit(_run, job_id)


def _run(job_id: int) -> None:
    """Punto de entrada del worker: delega en el controller (import tardío: evita ciclos)."""
    from app.controllers.collation_conversion_controller import (
        CollationConversionController,
    )

    run_logged(
        logger,
        "Job de conversión de collation %s falló de forma inesperada",
        job_id,
        lambda: CollationConversionController().run_job(job_id),
    )


def sweep_interrupted() -> int:
    """
    Marca ``running → interrupted`` los jobs colgados por un reinicio del proceso. Se llama
    en el ``lifespan`` de arranque. Devuelve cuántos se marcaron.
    """
    from app.controllers.collation_conversion_controller import (
        CollationConversionController,
    )

    return sweep_logged(
        logger,
        "No se pudo barrer jobs de conversión de collation interrumpidos",
        lambda: CollationConversionController().sweep_interrupted(),
    )
