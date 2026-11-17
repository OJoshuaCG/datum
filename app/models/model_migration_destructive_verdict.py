"""
Modelo ``ModelMigrationDestructiveVerdict``: veredicto de destructividad PRECALCULADO de una
versión de blueprint, por dirección.

Es un dato DERIVADO y recomputable, no una fuente de verdad: lo mandan el SQL y el manifiesto de la
versión. Solo existe para que el listado de versiones por aprobar no tenga que parsear con sqlglot
todo el blueprint en cada lectura. Una fila vale únicamente si su ``fingerprint`` coincide con el
que producen hoy los datos de la versión (ver ``app/services/destructive_verdicts.py``); si no,
se ignora y se recalcula. Perderla o borrarla entera es inocuo.

POR QUÉ NO ES UN FLAG EN ``model_migrations``
---------------------------------------------
El veredicto es por dirección (``up``/``down``) y por motor, y necesita una huella que lo invalide
solo cuando cambian los datos que lo producen. Un booleano en la versión no puede expresar eso, y
meterle columnas de análisis ensuciaría la tabla de la que el resto del gateway depende.
"""

from sqlalchemy import CHAR, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.services.destructive_approval_catalog import DESTRUCTIVE_APPROVAL_DIRECTIONS

DIRECTION_COLUMN_LENGTH = 4
FINGERPRINT_COLUMN_LENGTH = 64


def _quoted_values(values: tuple[str, ...]) -> str:
    """Arma la lista ``'a', 'b'`` de un ``IN (...)`` desde un vocabulario cerrado propio."""
    return ", ".join(f"'{value}'" for value in values)


class ModelMigrationDestructiveVerdict(Base, TimestampMixin):
    __tablename__ = "model_migration_destructive_verdicts"
    __table_args__ = (
        # Un veredicto por (versión, dirección). Por prefijo izquierdo también sirve la
        # búsqueda por versión que hace el listado.
        UniqueConstraint("model_migration_id", "direction", name="uq_mmdv_migration_direction"),
        CheckConstraint(
            f"direction IN ({_quoted_values(DESTRUCTIVE_APPROVAL_DIRECTIONS)})",
            name="direction",
        ),
        {
            "comment": (
                "Veredicto de destructividad precalculado por versión y dirección. Dato derivado: "
                "solo vale si fingerprint coincide con el actual; se puede borrar sin pérdida"
            )
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="ID único del veredicto"
    )

    model_migration_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("model_migrations.id", ondelete="CASCADE"),
        nullable=False,
        comment=(
            "Versión analizada, por id (borrar una versión renumera las siguientes). CASCADE: "
            "sin la versión no hay veredicto que conservar"
        ),
    )

    direction: Mapped[str] = mapped_column(
        String(DIRECTION_COLUMN_LENGTH),
        nullable=False,
        comment="Dirección analizada: up (aplicar) o down (rollback)",
    )

    fingerprint: Mapped[str] = mapped_column(
        CHAR(FINGERPRINT_COLUMN_LENGTH),
        nullable=False,
        comment=(
            "SHA-256 de todo lo que alimenta el veredicto (SQL, overrides por motor, manifiesto, "
            "versión del analizador y de sqlglot). Si no coincide con el actual, la fila se ignora"
        ),
    )

    reasons_by_engine: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "JSON {motor: [motivo, ...]}. Un motor ausente o con lista vacía significa que el SQL "
            "no es destructivo en ese motor; '{}' significa que no lo es en ninguno"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ModelMigrationDestructiveVerdict(id={self.id}, "
            f"model_migration_id={self.model_migration_id}, direction='{self.direction}')>"
        )
