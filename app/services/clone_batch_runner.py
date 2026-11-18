"""
Subsistema de ejecución asíncrona de LOTES de clonación.

Por qué un executor propio y no el de ``clone_runner``
------------------------------------------------------
Un lote recorre sus filas EN SERIE, llamando a ``CloneController.run_job`` de forma
sincrónica dentro de su propio hilo. Si en vez de eso encolara sus N hijos en el pool de
``clone_runner`` (``CLONE_MAX_WORKERS``, default 2), pasarían dos cosas malas: correrían de
a dos —el lote dejaría de ser serie— y un lote de 12 bases monopolizaría el pool durante
horas, dejando sin turno a cualquier clon suelto que un operador lance mientras tanto.

``CLONE_BATCH_MAX_WORKERS`` (default 1) gobierna cuántos LOTES corren a la vez, no cuántas
filas dentro de un lote. Las filas son serie siempre y eso no es configurable: el
paralelismo entre bases multiplicaría el daño de un error sin un beneficio medido.

Durabilidad: la misma que el resto de la familia. NO es una cola durable — si el proceso se
reinicia, el lote queda ``interrupted`` (barrido en el ``lifespan``) y se relanza a mano con
"reintentar las que faltaron". Con un lote de horas la ventana de un reinicio es mucho más
grande que con un clon suelto, así que ``WORKERS=1`` en el arranque de uvicorn deja de ser un
default cómodo y pasa a ser un requisito: el barrido marca ``interrupted`` todo lo que esté
``running``, sin distinguir de qué proceso es.

El esqueleto (pool perezoso, guards por clave, log del worker y del barrido) es el compartido
de ``app/services/inprocess_job_runner.py`` (``P-23``); acá queda lo propio del lote.
"""

from __future__ import annotations

import threading

from app.core.environments import CLONE_BATCH_MAX_WORKERS
from app.core.logger import get_logger
from app.services.inprocess_job_runner import (
    InProcessJobPool,
    KeyedGuards,
    run_logged,
    sweep_logged,
)

logger = get_logger(__name__)

_pool = InProcessJobPool(
    max_workers=CLONE_BATCH_MAX_WORKERS, thread_name_prefix="clone-batch"
)

# Guard in-process por SERVIDOR destino (no por BD, como en ``clone_runner``): un lote
# escribe sobre N bases del mismo servidor, así que la clave natural es el servidor. Evita
# que dos lotes hacia el mismo destino se pisen dentro de este proceso. La serialización
# cross-proceso real la sigue dando el advisory lock del motor que toma cada job.
_server_guards = KeyedGuards()


def server_guard(server_id: int) -> threading.Lock:
    """Lock in-process por servidor destino (misma instancia por ``server_id``)."""
    return _server_guards.get(server_id)


def enqueue(batch_id: int, item_ids: tuple[int, ...] | None = None) -> None:
    """
    Encola el recorrido del lote en el pool de hilos.

    ``item_ids`` acota el recorrido a las filas que la capa 2 autorizó al confirmar; ``None`` las
    recorre todas. El worker no re-evalúa permisos: ejecuta exactamente lo que se le encola.
    """
    _pool.submit(_run, batch_id, item_ids)


def _run(batch_id: int, item_ids: tuple[int, ...] | None = None) -> None:
    """Punto de entrada del worker: delega en el controller (importado tarde por los ciclos)."""
    from app.controllers.clone_batch_controller import CloneBatchController

    run_logged(
        logger,
        "Lote de clonación %s falló de forma inesperada",
        batch_id,
        lambda: CloneBatchController().run_batch(batch_id, item_ids),
    )


def sweep_interrupted() -> int:
    """
    Cierra los lotes que un reinicio dejó ``running``. Se llama en el ``lifespan`` de arranque.

    **Tiene que correr DESPUÉS de ``clone_runner.sweep_interrupted``**: ese barrido es el que
    pasa a ``interrupted`` los ``CloneJob`` colgados, y el estado de una fila del lote se lee
    del job cuando existe. Al revés, el lote se cerraría mirando filas que todavía figuran
    ``running`` y contaría mal el desenlace.
    """
    from app.controllers.clone_batch_controller import CloneBatchController

    return sweep_logged(
        logger,
        "No se pudo barrer lotes de clonación interrumpidos",
        lambda: CloneBatchController().sweep_interrupted(),
    )
