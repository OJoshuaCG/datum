"""gateway_sessions: índice por last_seen_at para la purga por retención

Revision ID: b9d3f5a7c2e1
Revises: a8c2e4f6b1d9

Solo un ÍNDICE, ninguna tabla ni columna nueva (decisión del revisor de esquema). La purga por
retención (``SESSION_RETENTION_DAYS``) borra por ``last_seen_at < umbral``; sin índice cada pasada
recorre toda la tabla. El nombre es EXPLÍCITO y mide ≤ 63 caracteres.

**Idempotente**: MySQL/MariaDB no tienen DDL transaccional, así que el índice se crea solo si
falta, con un inspector NUEVO en cada consulta. En PostgreSQL se crea ``CONCURRENTLY`` fuera de
transacción para no bloquear los logins.

**Downgrade** introspectivo: borra el índice solo si existe.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9d3f5a7c2e1"
down_revision: Union[str, None] = "a8c2e4f6b1d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "gateway_sessions"
_INDEX = "ix_gs_last_seen_at"
_COLUMNS = ["last_seen_at"]


def _index_names(bind) -> set[str]:
    # Inspector NUEVO en cada llamada: cachea, y el esquema cambia entre pasos.
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _INDEX in _index_names(bind):
        return
    if bind.dialect.name == "postgresql":
        # CREATE INDEX CONCURRENTLY no corre dentro de una transacción.
        with op.get_context().autocommit_block():
            op.create_index(_INDEX, _TABLE, _COLUMNS, unique=False, postgresql_concurrently=True)
    else:
        op.create_index(_INDEX, _TABLE, _COLUMNS, unique=False)


def downgrade() -> None:
    if _INDEX in _index_names(op.get_bind()):
        op.drop_index(_INDEX, table_name=_TABLE)
