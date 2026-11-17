"""model_migration_destructive_verdicts: veredicto de destructividad precalculado

Revision ID: f7a9c1e3b5d6
Revises: e4f6a8c0b2d3

Tabla de DATOS DERIVADOS: una fila por (versión, dirección) con los motivos por los que el SQL es
destructivo en cada motor y una huella (``fingerprint``) de todo lo que alimentó ese veredicto. El
listado de versiones por aprobar la consulta en vez de parsear con sqlglot todo el blueprint en
cada lectura. Una fila solo se usa si su huella coincide con la actual, así que **no hace falta
backfill para que la migración sea correcta**: sin filas, el listado recalcula como siempre. El
script ``scripts/backfill_destructive_verdicts.py`` solo las precarga para que la primera lectura
tampoco sea lenta.

FK ``CASCADE`` a la versión: sin la versión no hay veredicto que conservar.

**Idempotente paso a paso** (MySQL/MariaDB no tienen DDL transaccional: una migración que muere a
mitad deja aplicado lo que ya corrió con ``alembic_version`` atrasado). La tabla se crea solo si
falta, con un inspector NUEVO en cada consulta. La FK y la PK se dejan SIN nombre: los nombres de la
convención superan el límite del motor (64 en MySQL, 63 en PostgreSQL) y se guardan truncados con
un hash, así que nombrarlos acá divergiría del modelo.

**Downgrade** introspectivo: borra la tabla solo si existe y NUNCA nombra una constraint a mano.
Bajar esto no pierde información: son datos derivados que se recalculan al leer.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7a9c1e3b5d6"
down_revision: Union[str, None] = "e4f6a8c0b2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VERDICTS_TABLE = "model_migration_destructive_verdicts"

_DIRECTION_LENGTH = 4
_FINGERPRINT_LENGTH = 64


def _tables(bind) -> set[str]:
    # Inspector NUEVO en cada llamada: cachea, y entre pasos de esta migración el esquema cambia.
    return set(sa.inspect(bind).get_table_names())


def _create_verdicts_table() -> None:
    op.create_table(
        _VERDICTS_TABLE,
        sa.Column(
            "id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
            comment="ID único del veredicto",
        ),
        sa.Column(
            "model_migration_id",
            sa.Integer(),
            nullable=False,
            comment=(
                "Versión analizada, por id (borrar una versión renumera las siguientes). "
                "CASCADE: sin la versión no hay veredicto que conservar"
            ),
        ),
        sa.Column(
            "direction",
            sa.String(length=_DIRECTION_LENGTH),
            nullable=False,
            comment="Dirección analizada: up (aplicar) o down (rollback)",
        ),
        sa.Column(
            "fingerprint",
            sa.CHAR(length=_FINGERPRINT_LENGTH),
            nullable=False,
            comment=(
                "SHA-256 de todo lo que alimenta el veredicto (SQL, overrides por motor, "
                "manifiesto, versión del analizador y de sqlglot). Si no coincide con el actual, "
                "la fila se ignora"
            ),
        ),
        sa.Column(
            "reasons_by_engine",
            sa.Text(),
            nullable=False,
            comment=(
                "JSON {motor: [motivo, ...]}. Un motor ausente o con lista vacía significa que el "
                "SQL no es destructivo en ese motor; '{}' significa que no lo es en ninguno"
            ),
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
        sa.ForeignKeyConstraint(["model_migration_id"], ["model_migrations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_migration_id", "direction", name="uq_mmdv_migration_direction"),
        sa.CheckConstraint(
            "direction IN ('up', 'down')",
            # Sufijo corto: la convención de nombres (``env.py`` con ``target_metadata``) antepone
            # ``ck_<tabla>_``; pasar el nombre completo lo duplicaría y divergería del modelo.
            name="direction",
        ),
        comment=(
            "Veredicto de destructividad precalculado por versión y dirección. Dato derivado: "
            "solo vale si fingerprint coincide con el actual; se puede borrar sin pérdida"
        ),
    )


def upgrade() -> None:
    bind = op.get_bind()

    if _VERDICTS_TABLE not in _tables(bind):
        _create_verdicts_table()


def downgrade() -> None:
    bind = op.get_bind()

    if _VERDICTS_TABLE in _tables(bind):
        op.drop_table(_VERDICTS_TABLE)
