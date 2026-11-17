"""Índices compuestos para listados: audit_log, clone_jobs y tablas de ítems

Revision ID: c4e6a8b0d2f3
Revises: b9d3f5a7c2e1

Solo ÍNDICES, ninguna tabla ni columna nueva (veredicto del revisor de esquema):

- ``audit_log``: ``(status, id)``, ``(actor_type, id)`` y ``(admin_username, id)``. Son los filtros
  de igualdad de ``GET /audit-log``, que ordena por ``id DESC``. Sin índice, cada página filtrada
  recorre la tabla entera, y ``audit_log`` no tiene purga.
- ``clone_jobs``: ``(created_at, id)``, por el orden del historial.
- Ítems de clone, export y comparación: ``(fk, seq)``. Se leen por cabecera en orden de ``seq``.
  El índice simple de la FK NO se borra: MariaDB lo necesita para la FK (error 1553), y quitarlo
  sería una fase "contract" aparte. Tampoco son ``UNIQUE``, porque no hay garantía de que los
  datos existentes no tengan duplicados.

Nombres EXPLÍCITOS de ≤ 63 caracteres (la convención ``ix_%(column_0_label)s`` colisiona).
**Idempotente**: MySQL/MariaDB no tienen DDL transaccional, así que cada índice se crea solo si
falta, con un inspector NUEVO por paso. En PostgreSQL se crea ``CONCURRENTLY`` fuera de
transacción. **Downgrade** introspectivo: borra solo los índices que existan.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e6a8b0d2f3"
down_revision: Union[str, None] = "b9d3f5a7c2e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (tabla, nombre del índice, columnas)
_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("audit_log", "ix_audit_log_status_id", ["status", "id"]),
    ("audit_log", "ix_audit_log_actor_type_id", ["actor_type", "id"]),
    ("audit_log", "ix_audit_log_admin_username_id", ["admin_username", "id"]),
    ("clone_jobs", "ix_clone_jobs_created_at_id", ["created_at", "id"]),
    ("clone_job_items", "ix_clone_job_items_job_seq", ["job_id", "seq"]),
    ("export_job_items", "ix_export_job_items_job_seq", ["job_id", "seq"]),
    ("schema_comparison_items", "ix_schema_comparison_items_cmp_seq", ["comparison_id", "seq"]),
)


def _index_names(bind, table: str) -> set[str] | None:
    """Los índices de ``table``, o ``None`` si la tabla no existe. Inspector NUEVO por llamada."""
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return None
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for table, index_name, columns in _INDEXES:
        existing = _index_names(bind, table)
        if existing is None or index_name in existing:
            continue
        if bind.dialect.name == "postgresql":
            # CREATE INDEX CONCURRENTLY no corre dentro de una transacción.
            with op.get_context().autocommit_block():
                op.create_index(
                    index_name, table, columns, unique=False, postgresql_concurrently=True
                )
        else:
            op.create_index(index_name, table, columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table, index_name, _columns in reversed(_INDEXES):
        existing = _index_names(bind, table)
        if existing is not None and index_name in existing:
            op.drop_index(index_name, table_name=table)
