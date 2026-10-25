"""
Schemas Pydantic de las APROBACIONES de migraciones destructivas.

Una aprobación es el visto bueno de una SEGUNDA persona para ejecutar un SQL destructivo
(``DROP``, ``TRUNCATE``…) sobre un entorno protegido. Está atada al hash del SQL que se aprobó:
si el SQL cambia, la aprobación sigue existiendo como evidencia pero deja de valer
(``approval_status = stale_hash``).
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.services.destructive_approval_catalog import (
    DESTRUCTIVE_APPROVAL_DIRECTIONS,
    REASON_MAX_LENGTH,
    REASON_MIN_LENGTH,
)

_SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"

#: Motivo declarado: se recorta antes de medir, para que "          " no cuente como motivo.
ReasonText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=REASON_MIN_LENGTH, max_length=REASON_MAX_LENGTH
    ),
]


class DestructiveApprovalCreate(BaseModel):
    direction: str = Field(
        description="Dirección que se aprueba: 'up' (aplicar) o 'down' (rollback). Una no cubre a la otra."
    )
    expected_sql_hash: str = Field(
        pattern=_SHA256_HEX_PATTERN,
        description=(
            "Hash del SQL que el aprobador revisó (el 'sql_hash' que devolvió el listado de "
            "versiones destructivas). Si el SQL cambió desde entonces: 409 "
            "destructive_approval.sql_changed."
        ),
    )
    reason: ReasonText = Field(
        description=f"Motivo de la aprobación ({REASON_MIN_LENGTH} a {REASON_MAX_LENGTH} caracteres)."
    )

    @field_validator("direction")
    @classmethod
    def _direction_is_known(cls, value: str) -> str:
        if value not in DESTRUCTIVE_APPROVAL_DIRECTIONS:
            raise ValueError(f"'direction' debe ser uno de {list(DESTRUCTIVE_APPROVAL_DIRECTIONS)}.")
        return value


class DestructiveApprovalRevoke(BaseModel):
    reason: ReasonText = Field(
        description=f"Motivo de la revocación ({REASON_MIN_LENGTH} a {REASON_MAX_LENGTH} caracteres)."
    )


class ActorRefOut(BaseModel):
    """Una persona tal como quedó registrada (snapshot: sobrevive a que se borre el usuario)."""

    admin_id: int | None = Field(None, description="Null = desconocido.")
    username: str | None = None


class DestructiveApprovalOut(BaseModel):
    id: int
    model_migration_id: int
    database_model_id: int
    version: str
    direction: str
    scope: str = Field(description="'database' (una base) o 'blueprint' (todas las bases protegidas).")
    managed_database_id: int | None = Field(None, description="Solo en alcance 'database'.")
    sql_hash: str
    hash_basis: str
    destructive_summary: str | None = None
    author: ActorRefOut = Field(description="Creador de la versión al aprobar.")
    last_sql_editor: ActorRefOut = Field(description="Último editor del SQL al aprobar.")
    author_unknown: bool = Field(
        description="True si el creador o el último editor eran desconocidos: la regla de cuatro ojos no pudo verificarse del todo."
    )
    reason: str
    approved_by: ActorRefOut
    approved_at: datetime
    is_live: bool = Field(description="False si fue revocada o reemplazada.")
    revoked_at: datetime | None = None
    revoked_by: ActorRefOut | None = None
    revoke_reason: str | None = Field(None, description="'superseded' si la reemplazó una aprobación nueva.")
    last_executed_at: datetime | None = None
    last_executed_by: ActorRefOut | None = None
    execution_count: int = 0


class DestructiveVersionOut(BaseModel):
    """Una versión destructiva en una dirección, con su estado de aprobación."""

    migration_id: int
    version: str
    name: str
    direction: str
    sql_hash: str = Field(description="Hash actual del SQL: es el 'expected_sql_hash' al aprobar.")
    reasons_by_engine: dict[str, list[str]] = Field(
        description="Por qué es destructiva en cada motor donde lo es."
    )
    author: ActorRefOut
    last_sql_editor: ActorRefOut
    author_unknown: bool
    approval_status: str = Field(
        description=(
            "approved | unapproved | revoked | stale_hash. Se evalúa para la base indicada en "
            "'managed_database_id' o, sin ella, solo contra aprobaciones de alcance blueprint."
        )
    )
    effective_approval_id: int | None = Field(
        None, description="La aprobación que satisface esta versión (solo si approval_status = approved)."
    )
    approvals: list[DestructiveApprovalOut] = Field(
        description="Historial completo: vigentes, revocadas y reemplazadas, la más reciente primero."
    )


class DestructivePlanVersionOut(BaseModel):
    """Una versión destructiva pendiente (o a revertir) y el estado de su aprobación."""

    version: str
    migration_id: int
    sql_hash: str = Field(description="Hash del SQL de la dirección que se va a ejecutar.")
    reasons: list[str] = Field(default_factory=list, description="Por qué es destructiva en este motor.")
    approval_status: str = Field(description="approved | unapproved | revoked | stale_hash.")
    approval_id: int | None = Field(None, description="La aprobación que la satisface (solo si approved).")
    approval_scope: str | None = Field(None, description="'database' o 'blueprint' (solo si approved).")


class DestructivePlanOut(BaseModel):
    """
    Plan destructivo de UNA base (dry-run de apply o de rollback). Solo aparece cuando el entorno
    bloquea lo destructivo y hay versiones destructivas en juego.
    """

    direction: str = Field(description="'up' (apply) o 'down' (rollback).")
    requires_confirmation: bool = True
    executor_allowed: bool = Field(
        description="True si quien consulta es una persona con blueprints.apply_destructive en esta base."
    )
    all_approved: bool
    versions: list[DestructivePlanVersionOut] = Field(default_factory=list)
    confirm_token: str | None = Field(
        None,
        description=(
            "Solo si todas las versiones están aprobadas Y quien consulta es una persona que puede "
            "ejecutarlas. Hay que devolverlo en la ejecución junto con 'confirm_name'."
        ),
    )
    expires_at: datetime | None = None
    confirm_name_expected: str = Field(description="Lo que hay que re-tipear: el nombre de la base.")


class DestructiveUnapprovedDatabaseOut(BaseModel):
    managed_database_id: int
    versions: list[str]


class DestructiveConfirmationOut(BaseModel):
    """Confirmación a nivel de LOTE de ``apply-all`` (un único token para todas las bases)."""

    required: bool = True
    confirm_token: str | None = Field(
        None,
        description=(
            "Solo si el lote toca UN entorno protegido, todas las versiones destructivas están "
            "aprobadas y quien consulta puede ejecutarlas en cada base."
        ),
    )
    expires_at: datetime | None = None
    environment_slug: str | None = Field(
        None, description="Lo que hay que re-tipear. Null si el lote abarca más de un entorno protegido."
    )
    environment_slugs: list[str] = Field(
        default_factory=list,
        description="Entornos protegidos con versiones destructivas en el lote. Más de uno: filtrar por entorno.",
    )
    entry_count: int = Field(0, description="Cantidad de aprobaciones que respaldan el lote.")
    executor_allowed: bool
    all_approved: bool
    unapproved_databases: list[DestructiveUnapprovedDatabaseOut] = Field(default_factory=list)
