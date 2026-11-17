"""query_executions / migration_statement_progress: índices de las rutas de lectura

Revision ID: a8c2e4f6b1d9
Revises: f7a9c1e3b5d6

Solo ÍNDICES, ninguna tabla ni columna nueva (decisión del revisor de esquema: el costo estaba en
recalcular, no en falta de estructura).

- ``ix_qe_server_db_id`` sobre ``query_executions (server_id, database_name, id)``: el historial de
  la consola filtra por servidor y base y pagina por ``id`` descendente. Con solo el índice de
  ``server_id`` el motor filtraba el resto fila por fila y ordenaba aparte.
- ``ix_msp_migration_direction`` sobre ``migration_statement_progress (model_migration_id,
  direction)``: la UNIQUE existente empieza por ``managed_database_id``, así que buscar por versión
  (y el ``ON DELETE CASCADE`` desde ``model_migrations``) no la podía usar.

Los nombres son EXPLÍCITOS: la convención ``ix_%(column_0_label)s`` solo usa la primera columna y
chocaría con los índices de una sola columna que ya existen. Ambos miden ≤ 63 caracteres.

**Idempotente**: MySQL/MariaDB no tienen DDL transaccional. Cada índice se crea solo si falta,
con un inspector NUEVO en cada consulta. En PostgreSQL se crea ``CONCURRENTLY`` fuera de
transacción para no bloquear las escrituras de ``query_executions``, que es la tabla que crece.

**Downgrade** introspectivo: borra cada índice solo si existe.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8c2e4f6b1d9"
down_revision: Union[str, None] = "f7a9c1e3b5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_QUERY_EXECUTIONS_TABLE = "query_executions"
_QUERY_EXECUTIONS_INDEX = "ix_qe_server_db_id"
_QUERY_EXECUTIONS_COLUMNS = ["server_id", "database_name", "id"]

_PROGRESS_TABLE = "migration_statement_progress"
_PROGRESS_INDEX = "ix_msp_migration_direction"
_PROGRESS_COLUMNS = ["model_migration_id", "direction"]


def _index_names(bind, table_name: str) -> set[str]:
    # Inspector NUEVO en cada llamada: cachea, y entre pasos de esta migración el esquema cambia.
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    bind = op.get_bind()
    if index_name in _index_names(bind, table_name):
        return
    if bind.dialect.name == "postgresql":
        # CREATE INDEX CONCURRENTLY no corre dentro de una transacción.
        with op.get_context().autocommit_block():
            op.create_index(
                index_name, table_name, columns, unique=False, postgresql_concurrently=True
            )
    else:
        op.create_index(index_name, table_name, columns, unique=False)


def _drop_index_if_present(table_name: str, index_name: str) -> None:
    if index_name in _index_names(op.get_bind(), table_name):
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    _create_index_if_missing(
        _QUERY_EXECUTIONS_TABLE, _QUERY_EXECUTIONS_INDEX, _QUERY_EXECUTIONS_COLUMNS
    )
    _create_index_if_missing(_PROGRESS_TABLE, _PROGRESS_INDEX, _PROGRESS_COLUMNS)


def downgrade() -> None:
    _drop_index_if_present(_PROGRESS_TABLE, _PROGRESS_INDEX)
    _drop_index_if_present(_QUERY_EXECUTIONS_TABLE, _QUERY_EXECUTIONS_INDEX)
