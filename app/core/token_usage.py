"""
Registro de ``last_used_at`` de los tokens bearer (MCP e integración).

POR QUÉ UN UPDATE CONDICIONAL Y NO "LEER, ASIGNAR, COMMIT"
---------------------------------------------------------
La versión anterior asignaba ``fila.last_used_at = ahora`` y hacía ``commit`` en la MISMA
transacción en la que había leído el token. Eso tiene tres costos:

- Es el patrón leer-y-después-escribir que en MariaDB 11.8 (``innodb_snapshot_isolation``)
  dispara el error 1020 cuando dos requests del mismo token llegan juntas. Ese error tumbaba la
  autenticación con un 500.
- El ``commit`` expira la fila, así que cada atributo que se leía después era otro SELECT.
- La transacción de lectura quedaba abierta durante la carga del emisor y la auditoría.

Un único ``UPDATE ... WHERE last_used_at IS NULL OR last_used_at < umbral``, en una transacción
propia, no lee nada antes de escribir. Además es idempotente: si otro request ya lo refrescó, no
afecta ninguna fila. Es de mejor esfuerzo, porque perder un refresco de ``last_used_at`` es mucho
menos grave que rechazar a un agente legítimo.
"""

from datetime import datetime, timedelta

from sqlalchemy import or_, update
from sqlalchemy.exc import SQLAlchemyError

from app.core.logger import get_logger

logger = get_logger(__name__)


def record_last_used(
    session_factory, model, token_pk: int, now: datetime, resolution: timedelta
) -> None:
    """
    Escribe ``last_used_at = now`` en la fila ``token_pk`` si está vacía o tiene más de
    ``resolution`` de antigüedad. El throttle vive en el ``WHERE``: no hace falta haber leído la
    fila. Nunca lanza por un error de la BD (incluido el pool agotado): el token ya se validó y
    perder un refresco no puede convertir una autenticación correcta en un 500.
    """
    threshold = now - resolution
    statement = (
        update(model)
        .where(
            model.id == token_pk,
            or_(model.last_used_at.is_(None), model.last_used_at <= threshold),
        )
        .values(last_used_at=now)
    )
    session = session_factory()
    try:
        session.execute(statement, execution_options={"synchronize_session": False})
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.warning("No se pudo registrar last_used_at del token %s", token_pk, exc_info=True)
    finally:
        session.close()
