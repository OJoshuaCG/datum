"""migration_apply_jobs: apply-all de un blueprint corrido en background

Revision ID: d5f7b9c1e3a5
Revises: c4e6a8b0d2f3

Una fila por job de ``apply-all`` en background (``app/models/migration_apply_job.py``). Tabla
NUEVA, sin backfill: los ``apply-all`` síncronos de antes no dejaban job.

**Idempotente paso a paso** (MySQL/MariaDB no tienen DDL transaccional: una migración que muere a
mitad deja aplicado lo que ya corrió con ``alembic_version`` atrasado). La tabla se crea solo si
falta y cada índice solo si falta, con un inspector NUEVO en cada consulta. La FK y la PK se dejan
SIN nombre: los nombres de la convención superan el límite del motor y se guardan truncados con un
hash, así que nombrarlos acá divergiría del modelo.

**Downgrade** introspectivo: borra la tabla solo si existe y NUNCA nombra una constraint a mano.
Bajar esto pierde el historial de jobs, no datos de las bases administradas.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5f7b9c1e3a5"
down_revision: Union[str, None] = "c4e6a8b0d2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JOBS_TABLE = "migration_apply_jobs"
# Nombres que produce la convención ``ix_%(column_0_label)s`` del modelo (``app/models/base.py``).
_MODEL_ID_INDEX = "ix_migration_apply_jobs_model_id"
_STATUS_INDEX = "ix_migration_apply_jobs_status"


def _tables(bind) -> set[str]:
    # Inspector NUEVO en cada llamada: cachea, y entre pasos de esta migración el esquema cambia.
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(_JOBS_TABLE)}


def _create_jobs_table() -> None:
    op.create_table(
        _JOBS_TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="ID único del job"),
        sa.Column(
            "model_id",
            sa.Integer(),
            nullable=False,
            comment="Blueprint cuyas migraciones se aplican. CASCADE: sin blueprint no hay job",
        ),
        sa.Column(
            "environment_id",
            sa.Integer(),
            nullable=True,
            comment="Filtro de entorno pedido (sin FK: historial desacoplado)",
        ),
        sa.Column(
            "target_version",
            sa.String(length=50),
            nullable=True,
            comment="Versión tope del lote, ya resuelta (si se pidió por canal, la del canal)",
        ),
        sa.Column(
            "channel_slug",
            sa.String(length=100),
            nullable=True,
            comment="Canal pedido, si el tope se resolvió por canal",
        ),
        sa.Column(
            "on_failure",
            sa.String(length=20),
            nullable=False,
            comment="Modo de reconciliación por base: auto | reconcile | leave",
        ),
        sa.Column(
            "force",
            sa.Boolean(),
            server_default="0",
            nullable=False,
            comment="Override de cuarentena pedido (NO saltea el guard de entornos)",
        ),
        sa.Column(
            "total_databases",
            sa.Integer(),
            nullable=False,
            comment="Bases del blueprint, sin filtros (mismo número que el apply-all síncrono)",
        ),
        sa.Column(
            "matched_databases",
            sa.Integer(),
            nullable=False,
            comment="Bases que cumplían los filtros, antes del tope",
        ),
        sa.Column(
            "planned",
            sa.Integer(),
            nullable=False,
            comment="Bases que el job va a recorrer (tope aplicado)",
        ),
        sa.Column(
            "processed",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="Bases ya terminadas, con éxito o no. El polling lee 'processed de planned'",
        ),
        sa.Column(
            "failed",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="Bases terminadas con error (ok=false)",
        ),
        sa.Column(
            "results",
            sa.Text(),
            nullable=False,
            comment=(
                "JSON: lista de ítems con el MISMO formato que results[] del apply-all síncrono, "
                "uno por base terminada, en orden de recorrido"
            ),
        ),
        sa.Column(
            "created_by_admin_id",
            sa.Integer(),
            nullable=True,
            comment="Admin que creó el job (sin FK: historial desacoplado)",
        ),
        sa.Column(
            "created_by_username",
            sa.String(length=128),
            nullable=True,
            comment="Username del admin (mismo ancho que audit_log)",
        ),
        sa.Column(
            "origin_request_id",
            sa.String(length=32),
            nullable=True,
            comment="Request ID que creó el job; el worker lo reusa en sus logs y su auditoría",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
            comment="pending | running | done | partial | failed | interrupted",
        ),
        sa.Column(
            "error",
            sa.String(length=500),
            nullable=True,
            comment="Motivo acotado del fallo del JOB (nunca el mensaje crudo del motor)",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(),
            nullable=True,
            comment="Cuándo arrancó la primera base (UTC)",
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(),
            nullable=True,
            comment="Cuándo terminó el recorrido (UTC)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
            comment="Fecha y hora de creación del registro",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
            comment="Fecha y hora de última actualización del registro",
        ),
        sa.ForeignKeyConstraint(["model_id"], ["database_models.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="apply-all de un blueprint corrido en background, con progreso por base",
    )


def upgrade() -> None:
    bind = op.get_bind()

    if _JOBS_TABLE not in _tables(bind):
        _create_jobs_table()

    existing_indexes = _indexes(bind)
    if _MODEL_ID_INDEX not in existing_indexes:
        op.create_index(_MODEL_ID_INDEX, _JOBS_TABLE, ["model_id"], unique=False)
    if _STATUS_INDEX not in existing_indexes:
        op.create_index(_STATUS_INDEX, _JOBS_TABLE, ["status"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()

    if _JOBS_TABLE in _tables(bind):
        op.drop_table(_JOBS_TABLE)
