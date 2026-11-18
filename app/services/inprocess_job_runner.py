"""
Esqueleto compartido de los workers in-process (``P-23``).

Clon, lote de clon, conversión de collation, exportación y ``apply-all`` corren en
background con el mismo esqueleto: un ``ThreadPoolExecutor`` creado una sola vez, locks
in-process por clave (BD, servidor) y dos envoltorios que nunca dejan escapar una excepción:
el del worker (un hilo que muere sin log es un job colgado invisible) y el del barrido de
arranque (un barrido que falla no puede tumbar el ``lifespan``). Las cinco copias habían
empezado a divergir en detalles (el doble chequeo del pool solo estaba comentado en dos), así
que viven acá una sola vez.

Lo que NO vive acá, a propósito: qué corre cada worker, qué clave serializa cada guard, el
techo de admisión de la exportación y el orden de los barridos. Eso es de cada feature y
sigue documentado en su runner (``app/services/*_runner.py``), que conserva su API pública
para que controllers, ``main.py`` y tests no cambien.

Los vocabularios de ítem (``applied/failed/skipped`` del clon vs ``ok/error/skipped`` de
collation y export) siguen divergentes: son contrato del frontend y unificarlos es un cambio
de API, no de este esqueleto.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Hashable
from concurrent.futures import Future, ThreadPoolExecutor
from logging import Logger
from typing import Any


class InProcessJobPool:
    """Pool de hilos perezoso de UNA familia de jobs. No es una cola durable."""

    def __init__(self, *, max_workers: int, thread_name_prefix: str) -> None:
        # ``max(1, ...)``: un ``*_MAX_WORKERS=0`` mal configurado no puede dejar el pool sin
        # hilos (ThreadPoolExecutor lo rechaza) ni encolar trabajo que nadie va a correr.
        self._max_workers = max(1, max_workers)
        self._thread_name_prefix = thread_name_prefix
        self._executor: ThreadPoolExecutor | None = None
        self._executor_lock = threading.Lock()

    def _get_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            with self._executor_lock:
                # Doble chequeo: entre el ``is None`` de afuera y el lock, otro request pudo
                # haberlo creado ya. Sin esto se instancian dos pools y uno queda huérfano
                # con sus hilos levantados.
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(
                        max_workers=self._max_workers,
                        thread_name_prefix=self._thread_name_prefix,
                    )
        return self._executor

    def submit(self, worker: Callable[..., None], *args: Any) -> Future:
        """Encola ``worker(*args)``. Propaga el error de ``submit`` (pool apagado) al caller."""
        return self._get_executor().submit(worker, *args)


class KeyedGuards:
    """Un ``threading.Lock`` por clave, siempre la misma instancia para la misma clave."""

    def __init__(self) -> None:
        self._locks: dict[Hashable, threading.Lock] = {}
        self._locks_lock = threading.Lock()

    def get(self, key: Hashable) -> threading.Lock:
        with self._locks_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock


def run_logged(logger: Logger, failure_message: str, job_id: int, work: Callable[[], None]) -> None:
    """
    Corre el cuerpo de un worker y registra cualquier excepción en vez de propagarla.

    ``failure_message`` lleva un ``%s`` para el id. El controller ya cierra el job ante los
    errores que espera; esto atrapa lo que se le escapó, para que quede en el log con el
    traceback y el hilo del pool siga disponible.
    """
    try:
        work()
    except Exception:  # noqa: BLE001 — el worker nunca debe morir silenciosamente sin log
        logger.error(failure_message, job_id, exc_info=True)


def sweep_logged(logger: Logger, failure_message: str, sweep: Callable[[], int]) -> int:
    """Corre un barrido de arranque; si falla lo registra y devuelve 0 (el arranque sigue)."""
    try:
        return sweep()
    except Exception:  # noqa: BLE001 — el arranque nunca debe romperse por esto
        logger.warning(failure_message, exc_info=True)
        return 0
