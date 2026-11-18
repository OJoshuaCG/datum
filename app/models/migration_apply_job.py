"""
Modelo ``MigrationApplyJob`` — ``apply-all`` de un blueprint corrido en background.

QUÉ PROBLEMA RESUELVE
---------------------
``POST .../apply-all`` recorre hasta 100 bases en serie DENTRO del request: con migraciones
largas el request dura minutos u horas, y el operador no ve nada hasta el final. El job hace el
mismo trabajo (el mismo código, ``ManagedMigrationController._apply_all_item``) fuera del
request y deja el progreso por base consultable.

QUÉ CORRE EN EL REQUEST Y QUÉ EN EL WORKER
------------------------------------------
Todas las validaciones del lote (alcance, capa 2, guards por motor, canal, integridad y el
preflight destructivo todo-o-nada) corren EN EL REQUEST que crea el job: si alguna falla, el
request responde el error y no se crea ninguna fila. El worker solo recorre las bases que ese
request ya aprobó.

POR QUÉ LOS ÍTEMS VIVEN EN UNA COLUMNA JSON Y NO EN UNA TABLA DE ÍTEMS
---------------------------------------------------------------------
El ítem es exactamente el dict que ya devuelve el ``apply-all`` síncrono (``ApplyAllItemOut``):
solo se escribe cuando la base TERMINA y nunca se consulta por campo. Una tabla de ítems
duplicaría ese contrato columna por columna sin habilitar ninguna consulta nueva.

DURABILIDAD
-----------
Igual que el resto de la familia de runners (clon, lote de clon, collation, export): NO es una
cola durable. El plan preparado y el ``Actor`` viven en memoria del worker; si el proceso se
reinicia, el barrido del ``lifespan`` deja el job ``interrupted`` y se relanza a mano (las bases
que ya terminaron reportan cero pendientes la segunda vez).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

MIGRATION_APPLY_JOB_PENDING = "pending"  # creado, esperando turno en el pool
MIGRATION_APPLY_JOB_RUNNING = "running"  # el worker está recorriendo las bases
MIGRATION_APPLY_JOB_DONE = "done"  # terminaron todas sin fallos
MIGRATION_APPLY_JOB_PARTIAL = "partial"  # terminaron todas, con al menos una fallida
MIGRATION_APPLY_JOB_FAILED = "failed"  # el worker se cayó antes de poder cerrar el recorrido
MIGRATION_APPLY_JOB_INTERRUPTED = "interrupted"  # el proceso murió a mitad (barrido)

MIGRATION_APPLY_JOB_STATUSES: tuple[str, ...] = (
    MIGRATION_APPLY_JOB_PENDING,
    MIGRATION_APPLY_JOB_RUNNING,
    MIGRATION_APPLY_JOB_DONE,
    MIGRATION_APPLY_JOB_PARTIAL,
    MIGRATION_APPLY_JOB_FAILED,
    MIGRATION_APPLY_JOB_INTERRUPTED,
)

# Estados en los que el job todavía puede escribir sobre las bases del blueprint. Un segundo
# job del MISMO blueprint se rechaza mientras exista uno en estos estados.
MIGRATION_APPLY_JOB_ACTIVE: tuple[str, ...] = (
    MIGRATION_APPLY_JOB_PENDING,
    MIGRATION_APPLY_JOB_RUNNING,
)

MIGRATION_APPLY_JOB_TERMINAL: tuple[str, ...] = (
    MIGRATION_APPLY_JOB_DONE,
    MIGRATION_APPLY_JOB_PARTIAL,
    MIGRATION_APPLY_JOB_FAILED,
    MIGRATION_APPLY_JOB_INTERRUPTED,
)


class MigrationApplyJob(Base, TimestampMixin):
    __tablename__ = "migration_apply_jobs"
    __table_args__ = (
        {"comment": "apply-all de un blueprint corrido en background, con progreso por base"},
    )

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="ID único del job"
    )
    model_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("database_models.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Blueprint cuyas migraciones se aplican. CASCADE: sin blueprint no hay job",
    )

    # ---- Parámetros congelados al crear (lo que el operador pidió) --------------------- #
    environment_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="Filtro de entorno pedido (sin FK: historial desacoplado)"
    )
    target_version: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Versión tope del lote, ya resuelta (si se pidió por canal, la del canal)",
    )
    channel_slug: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="Canal pedido, si el tope se resolvió por canal"
    )
    on_failure: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Modo de reconciliación por base: auto | reconcile | leave",
    )
    force: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
        comment="Override de cuarentena pedido (NO saltea el guard de entornos)",
    )

    # ---- Alcance y progreso ------------------------------------------------------------ #
    total_databases: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Bases del blueprint, sin filtros (mismo número que el apply-all síncrono)",
    )
    matched_databases: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Bases que cumplían los filtros, antes del tope"
    )
    planned: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Bases que el job va a recorrer (tope aplicado)"
    )
    processed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="Bases ya terminadas, con éxito o no. El polling lee 'processed de planned'",
    )
    failed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="Bases terminadas con error (ok=false)",
    )
    results: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "JSON: lista de ítems con el MISMO formato que results[] del apply-all síncrono, "
            "uno por base terminada, en orden de recorrido"
        ),
    )

    # ---- Autoría (el worker NO hereda el contexto de la request) ----------------------- #
    created_by_admin_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="Admin que creó el job (sin FK: historial desacoplado)"
    )
    created_by_username: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="Username del admin (mismo ancho que audit_log)"
    )
    origin_request_id: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="Request ID que creó el job; el worker lo reusa en sus logs y su auditoría",
    )

    # ---- Estado ------------------------------------------------------------------------ #
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=MIGRATION_APPLY_JOB_PENDING,
        server_default=MIGRATION_APPLY_JOB_PENDING,
        index=True,
        comment="pending | running | done | partial | failed | interrupted",
    )
    error: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Motivo acotado del fallo del JOB (nunca el mensaje crudo del motor)",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="Cuándo arrancó la primera base (UTC)"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="Cuándo terminó el recorrido (UTC)"
    )

    def __repr__(self) -> str:
        return (
            f"<MigrationApplyJob(id={self.id}, model_id={self.model_id}, "
            f"status='{self.status}', {self.processed}/{self.planned})>"
        )
