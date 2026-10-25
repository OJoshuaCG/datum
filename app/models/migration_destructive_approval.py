"""
Modelo ``MigrationDestructiveApproval``: la aprobación de una SEGUNDA persona para ejecutar una
migración destructiva en un entorno que bloquea lo destructivo.

POR QUÉ UNA TABLA PROPIA Y NO UN FLAG EN ``model_migrations``
-------------------------------------------------------------
Una aprobación tiene ciclo de vida (vigente, reemplazada, revocada), alcance (una base o todo el
blueprint), dirección (``up`` o ``down``) y rastro de quién la dio, quién la usó y quién la
revocó. Un booleano en la versión no puede decir "aprobada para la base A y no para la B", ni
sobrevivir a una edición del SQL como evidencia de qué se aprobó.

POR QUÉ ``live_key`` Y ``scope_key`` (mismo molde que ``capability_grant.py``)
------------------------------------------------------------------------------
Debe haber UNA sola aprobación vigente por (versión, dirección, alcance) y cualquier cantidad de
filas históricas. Un índice único parcial no es portable (MySQL no lo tiene), así que ``live_key``
vale ``1`` mientras la fila está vigente y ``NULL`` cuando fue revocada o reemplazada: en un
``UNIQUE`` los ``NULL`` son distintos en los cuatro motores que usa el repo. Un ``CHECK`` ata
``live_key`` a ``revoked_at`` para que no puedan divergir. ``scope_key`` (``'blueprint'`` o
``'database:{id}'``) existe porque ``managed_database_id`` es NULL en el alcance blueprint y un
NULL dentro del ``UNIQUE`` lo volvería inefectivo.

POR QUÉ NO HAY ``CHECK`` SOBRE ``managed_database_id``
------------------------------------------------------
MySQL prohíbe un ``CHECK`` sobre una columna que participa de una FK con ``CASCADE``. Que
``scope='database'`` implique ``managed_database_id`` y viceversa lo valida el controller.

POR QUÉ LA AUTORÍA Y LOS ACTORES VAN COMO SNAPSHOT SIN FK
---------------------------------------------------------
Borrar un usuario no puede mutilar el rastro de quién aprobó, ejecutó o revocó SQL destructivo
sobre producción. Mismo criterio que ``ModelMigration.created_by_*``.
"""

from datetime import datetime

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.services.destructive_approval_catalog import (
    DESTRUCTIVE_APPROVAL_DIRECTIONS,
    DESTRUCTIVE_APPROVAL_SCOPES,
    HASH_BASIS_RAW_SOURCE_V1,
    LIVE_KEY_ACTIVE,
)

DIRECTION_COLUMN_LENGTH = 4
SCOPE_COLUMN_LENGTH = 16
SCOPE_KEY_COLUMN_LENGTH = 32
SQL_HASH_COLUMN_LENGTH = 64
HASH_BASIS_COLUMN_LENGTH = 16
VERSION_LABEL_COLUMN_LENGTH = 10
DESTRUCTIVE_SUMMARY_COLUMN_LENGTH = 500
ACTOR_USERNAME_COLUMN_LENGTH = 128
REASON_COLUMN_LENGTH = 500


def _quoted_values(values: tuple[str, ...]) -> str:
    """Arma la lista ``'a', 'b'`` de un ``IN (...)`` desde un vocabulario cerrado propio."""
    return ", ".join(f"'{value}'" for value in values)


class MigrationDestructiveApproval(Base, TimestampMixin):
    __tablename__ = "migration_destructive_approvals"
    __table_args__ = (
        # Una sola aprobación vigente por (versión, dirección, alcance): `live_key` es 1 o NULL
        # y los NULL no chocan. Por prefijo izquierdo también sirve la búsqueda por versión.
        UniqueConstraint(
            "model_migration_id",
            "direction",
            "scope_key",
            "live_key",
            name="uq_mda_live",
        ),
        Index("ix_mda_model_id", "database_model_id", "id"),
        Index("ix_mda_managed_db", "managed_database_id"),
        CheckConstraint(
            f"direction IN ({_quoted_values(DESTRUCTIVE_APPROVAL_DIRECTIONS)})",
            name="direction",
        ),
        CheckConstraint(
            f"scope IN ({_quoted_values(DESTRUCTIVE_APPROVAL_SCOPES)})",
            name="scope",
        ),
        # `live_key IS NOT NULL` explícito: con NULL, `live_key = 1` da NULL y un CHECK que
        # evalúa a NULL PASA, así que una fila vigente sin `live_key` se saltearía el UNIQUE.
        CheckConstraint(
            f"(revoked_at IS NULL AND live_key IS NOT NULL AND live_key = {LIVE_KEY_ACTIVE}) "
            "OR (revoked_at IS NOT NULL AND live_key IS NULL)",
            name="live_key_revoked",
        ),
        {
            "comment": (
                "Aprobaciones de una segunda persona para ejecutar migraciones destructivas en "
                "entornos protegidos. Una fila por versión, dirección y alcance"
            )
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="ID único de la aprobación"
    )

    model_migration_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("model_migrations.id", ondelete="CASCADE"),
        nullable=False,
        comment=(
            "Versión aprobada. Se identifica por id y no por número de versión: borrar una "
            "versión renumera las siguientes. CASCADE: sin la versión no hay nada que aprobar"
        ),
    )

    database_model_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("database_models.id", ondelete="CASCADE"),
        nullable=False,
        comment="Blueprint de la versión (desnormalizado para listar las aprobaciones de un blueprint)",
    )

    direction: Mapped[str] = mapped_column(
        String(DIRECTION_COLUMN_LENGTH),
        nullable=False,
        comment="Dirección aprobada: up (aplicar) o down (rollback). Una no cubre a la otra",
    )

    scope: Mapped[str] = mapped_column(
        String(SCOPE_COLUMN_LENGTH),
        nullable=False,
        comment=(
            "Alcance: database (una base) o blueprint (todas las bases del blueprint en "
            "entornos protegidos, incluidas las futuras)"
        ),
    )

    managed_database_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("managed_databases.id", ondelete="CASCADE"),
        nullable=True,
        comment="Base cubierta cuando scope=database; NULL cuando scope=blueprint",
    )

    scope_key: Mapped[str] = mapped_column(
        String(SCOPE_KEY_COLUMN_LENGTH),
        nullable=False,
        comment=(
            "'blueprint' o 'database:{id}'. Existe porque managed_database_id es NULL en el "
            "alcance blueprint y un NULL dentro del UNIQUE lo dejaría sin efecto"
        ),
    )

    sql_hash: Mapped[str] = mapped_column(
        CHAR(SQL_HASH_COLUMN_LENGTH),
        nullable=False,
        comment=(
            "SHA-256 del SQL fuente aprobado (ver hash_basis). La aprobación solo vale mientras "
            "el SQL actual produzca el mismo hash"
        ),
    )

    hash_basis: Mapped[str] = mapped_column(
        String(HASH_BASIS_COLUMN_LENGTH),
        nullable=False,
        default=HASH_BASIS_RAW_SOURCE_V1,
        server_default=HASH_BASIS_RAW_SOURCE_V1,
        comment=(
            "Cómo se calculó sql_hash. Una base distinta de la vigente fuerza una "
            "re-aprobación en vez de comparar hashes de cosas distintas"
        ),
    )

    version_label: Mapped[str] = mapped_column(
        String(VERSION_LABEL_COLUMN_LENGTH),
        nullable=False,
        comment="Número de versión al aprobar ('0003'), copiado como texto para el rastro",
    )

    destructive_summary: Mapped[str | None] = mapped_column(
        String(DESTRUCTIVE_SUMMARY_COLUMN_LENGTH),
        nullable=True,
        comment="Resumen de qué destruye el SQL, mostrado al aprobador",
    )

    # ---- Autoría de la versión al momento de aprobar (snapshots, sin FK) ----------------- #
    author_admin_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="Creador de la versión al aprobar (sin FK). NULL = desconocido"
    )
    author_username: Mapped[str | None] = mapped_column(
        String(ACTOR_USERNAME_COLUMN_LENGTH),
        nullable=True,
        comment="Nombre del creador de la versión al aprobar",
    )
    editor_admin_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Último editor del SQL al aprobar (sin FK). NULL = desconocido",
    )
    editor_username: Mapped[str | None] = mapped_column(
        String(ACTOR_USERNAME_COLUMN_LENGTH),
        nullable=True,
        comment="Nombre del último editor del SQL al aprobar",
    )

    author_unknown: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
        comment=(
            "True si el creador o el último editor eran desconocidos al aprobar (versiones "
            "anteriores a esa trazabilidad): la regla de cuatro ojos no pudo verificarse del todo"
        ),
    )

    reason: Mapped[str] = mapped_column(
        String(REASON_COLUMN_LENGTH),
        nullable=False,
        comment="Motivo declarado por el aprobador (10 a 500 caracteres)",
    )

    approved_by_admin_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Usuario del gateway que aprobó (sin FK)"
    )
    approved_by_username: Mapped[str] = mapped_column(
        String(ACTOR_USERNAME_COLUMN_LENGTH),
        nullable=False,
        comment="Nombre del aprobador al momento de aprobar",
    )

    live_key: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
        comment="1 si la aprobación está vigente, NULL si fue revocada o reemplazada; base del UNIQUE",
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="Fecha y hora (UTC) de la revocación o del reemplazo"
    )
    revoked_by_admin_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="Quien revocó (sin FK). NULL si sigue vigente"
    )
    revoked_by_username: Mapped[str | None] = mapped_column(
        String(ACTOR_USERNAME_COLUMN_LENGTH), nullable=True, comment="Nombre de quien revocó"
    )
    revoke_reason: Mapped[str | None] = mapped_column(
        String(REASON_COLUMN_LENGTH),
        nullable=True,
        comment="Motivo de la revocación; 'superseded' si la reemplazó una aprobación nueva",
    )

    last_executed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="Última vez (UTC) que una ejecución consumió esta aprobación"
    )
    last_executed_by_admin_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="Usuario que ejecutó por última vez (sin FK)"
    )
    last_executed_by_username: Mapped[str | None] = mapped_column(
        String(ACTOR_USERNAME_COLUMN_LENGTH),
        nullable=True,
        comment="Nombre de quien ejecutó por última vez",
    )
    execution_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="Cuántas ejecuciones consumieron esta aprobación",
    )

    def __repr__(self) -> str:
        return (
            f"<MigrationDestructiveApproval(id={self.id}, "
            f"model_migration_id={self.model_migration_id}, direction='{self.direction}', "
            f"scope_key='{self.scope_key}', live={self.live_key is not None})>"
        )
