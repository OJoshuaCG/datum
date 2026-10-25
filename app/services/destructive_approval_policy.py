"""
Política de las aprobaciones de migraciones destructivas: hash del SQL, identidad de la persona,
resolución de "¿esta versión está aprobada para esta base?" y traducción de los errores del token
de confirmación al vocabulario cerrado del catálogo.

Es lógica pura más UNA consulta (``load_approval_rows``): no autoriza ni audita, eso lo hacen los
controllers. Vive aparte para que el controller de aprobaciones (que escribe las filas) y el guard
de ``managed_migration_controller`` (que las consume) lean la MISMA definición de "aprobada".
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.actor import identity_of, person_of
from app.exceptions import AppHttpException
from app.models.enums import EngineType
from app.models.migration_destructive_approval import MigrationDestructiveApproval
from app.services import confirm_token
from app.services.db_admin import migration_facts
from app.services.db_admin.migrations import ManifestStatement, MigrationRunner, MigrationSpec
from app.services.destructive_approval_catalog import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_REVOKED,
    APPROVAL_STATUS_STALE_HASH,
    APPROVAL_STATUS_UNAPPROVED,
    CODE_CONFIRMATION_EXPIRED,
    CODE_CONFIRMATION_INVALID,
    DIRECTION_DOWN,
    DIRECTION_UP,
    HASH_BASIS,
    REVOKE_REASON_SUPERSEDED,
    SCOPE_BLUEPRINT,
    SCOPE_DATABASE,
)

#: Separador de campos dentro del texto que se hashea (``US``, unit separator): no aparece en SQL
#: real, así que dos combinaciones distintas de campos no pueden producir el mismo texto.
_FIELD_SEPARATOR = "\x1f"

#: Marca de un campo ausente (``None``). Distinta de la cadena vacía: "sin override" y "override
#: vacío" son SQL distintos y no pueden compartir hash.
_NULL_MARKER = "\x00"

#: ``created_by_actor_type`` de una versión escrita con un token de agente: ``created_by_admin_id``
#: guarda ahí el PK del TOKEN, que comparte espacio de enteros con los usuarios.
_ACTOR_TYPE_API_TOKEN = "api_token"

_HTTP_GONE = 410
_HTTP_UNPROCESSABLE = 422


class SqlSource(Protocol):
    """Lo que ``approval_hash`` necesita: lo cumple ``MigrationSpec`` (manifiesto incluido)."""

    up_sql: str
    up_sql_mysql: str | None
    up_sql_postgresql: str | None
    down_sql: str | None
    manifest: tuple[ManifestStatement, ...]


# --------------------------------------------------------------------------- #
# Hash                                                                         #
# --------------------------------------------------------------------------- #
def approval_hash(source: SqlSource, direction: str) -> str:
    """
    SHA-256 del SQL FUENTE de la dirección indicada (``HASH_BASIS = raw_source_v1``).

    POR QUÉ EL TEXTO FUENTE Y NO EL SQL RESUELTO POR MOTOR: una actualización de sqlglot cambia la
    traducción a PostgreSQL y dejaría huérfana toda aprobación existente sin que nadie haya tocado
    una línea. El aprobador revisa lo que el autor escribió, así que eso es lo que se ata.

    ``up`` cubre ``up_sql`` y sus dos overrides por motor: editar CUALQUIERA invalida la
    aprobación para todos los motores (lo conservador). ``down`` cubre ``down_sql`` Y el reverso
    de cada fila del manifiesto: con manifiesto usable el rollback ejecuta ESOS reversos, no el
    ``down_sql`` (``MigrationRunner.statement_lists``), y un PATCH de ``down_sql`` no purga el
    manifiesto. Hashear solo ``down_sql`` dejaría aprobar un texto y ejecutar otro. El hash
    resuelto por motor se registra aparte, en el detalle de auditoría del consumo, como forense.
    """
    if direction == DIRECTION_UP:
        fields: tuple[str | None, ...] = (
            source.up_sql,
            source.up_sql_mysql,
            source.up_sql_postgresql,
        )
    elif direction == DIRECTION_DOWN:
        manifest_down_sqls = tuple(statement.down_sql for statement in source.manifest)
        fields = (source.down_sql, *manifest_down_sqls)
    else:
        raise ValueError(f"Dirección de migración desconocida: {direction!r}.")

    encoded_fields = [_NULL_MARKER if field is None else field for field in fields]
    hashed_text = _FIELD_SEPARATOR.join([HASH_BASIS, *encoded_fields])
    return hashlib.sha256(hashed_text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Identidad                                                                    #
# --------------------------------------------------------------------------- #
def person_id(actor) -> int | None:
    """
    El id de la PERSONA detrás de un actor, o ``None`` si no se puede atribuir a una.

    La regla vive en ``person_of`` (``app/core/actor.py``), la misma con la que
    ``ModelMigrationController._sql_editor_fields`` guarda al editor: así el autor y el aprobador
    se comparan con la misma vara.
    """
    person_identity_id, _ = identity_of(person_of(actor))
    return person_identity_id


def author_person_id(created_by_admin_id: int | None, created_by_actor_type: str | None) -> int | None:
    """
    El creador de una versión como PERSONA, o ``None`` si es desconocido.

    ``created_by_admin_id`` de una versión escrita con un token de agente es el PK del token (ver
    ``_ACTOR_TYPE_API_TOKEN``): no identifica a nadie, así que cuenta como desconocido y no se
    compara contra el aprobador. Un token de integración guarda el id del EMISOR y sí cuenta.
    """
    if created_by_actor_type == _ACTOR_TYPE_API_TOKEN:
        return None
    return created_by_admin_id


# --------------------------------------------------------------------------- #
# Resolución                                                                   #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ApprovalEntry:
    """Una aprobación vigente que cubre una versión (lo que se ata al token y a la auditoría)."""

    migration_id: int
    version: str
    sql_hash: str
    approval_id: int
    scope: str


@dataclass(frozen=True)
class VersionApproval:
    """Estado de aprobación de UNA versión en UNA dirección para una base concreta."""

    migration_id: int
    version: str
    direction: str
    current_hash: str
    status: str
    approval_id: int | None
    scope: str | None


@dataclass(frozen=True)
class Resolution:
    """
    Resultado de resolver las versiones destructivas pendientes de una base.

    ``unapproved`` incluye toda versión cuyo estado NO sea ``approved`` (sin aprobar, revocada o
    con hash viejo): para el guard las tres son lo mismo, "no se puede ejecutar". La diferencia
    la lleva ``statuses``, que es lo que el dry-run le muestra al operador.
    """

    approved: tuple[ApprovalEntry, ...]
    unapproved: tuple[str, ...]
    statuses: dict[str, VersionApproval]

    @property
    def is_fully_approved(self) -> bool:
        return not self.unapproved


def _applies_to_database(
    approval: MigrationDestructiveApproval, managed_database_id: int | None
) -> bool:
    """
    ¿La aprobación cubre a esta base? Una de alcance blueprint cubre a todas (incluidas las que se
    creen después); una de alcance base, solo a la suya. Sin base (listado a nivel blueprint),
    solo cuentan las de alcance blueprint.
    """
    if approval.scope == SCOPE_BLUEPRINT:
        return True
    return (
        approval.scope == SCOPE_DATABASE
        and managed_database_id is not None
        and approval.managed_database_id == managed_database_id
    )


def _hash_matches(approval: MigrationDestructiveApproval, current_hash: str) -> bool:
    """El hash coincide Y se calculó con la base vigente (otra base fuerza re-aprobación)."""
    return approval.sql_hash == current_hash and approval.hash_basis == HASH_BASIS


def classify_approval(
    approvals: Iterable[MigrationDestructiveApproval],
    current_hash: str,
    managed_database_id: int | None,
) -> tuple[str, MigrationDestructiveApproval | None]:
    """
    Estado de aprobación (A-5) de una versión en una dirección, y la fila que la satisface.

    ``approvals`` son TODAS las filas (vigentes e históricas) de esa versión y dirección.
    Precedencia: ``approved`` > ``stale_hash`` > ``revoked`` > ``unapproved``.

    - ``approved``: hay una vigente aplicable con el hash actual. Si hay dos (una de la base y
      una del blueprint) gana la de la BASE y, a igualdad, la de menor id: es determinista.
    - ``stale_hash``: hay vigentes aplicables pero ninguna con el hash actual. Es "re-aprobación
      necesaria", nunca se toma por aprobada.
    - ``revoked``: no hay vigente y alguna aplicable, revocada A MANO, tiene el hash actual. Las
      reemplazadas (``superseded``) no cuentan: no fueron una decisión de revocar.
    - ``unapproved``: cualquier otro caso.
    """
    applicable = [a for a in approvals if _applies_to_database(a, managed_database_id)]
    live = [a for a in applicable if a.live_key is not None and a.revoked_at is None]

    matching_live = [a for a in live if _hash_matches(a, current_hash)]
    if matching_live:
        database_scope_first = sorted(
            matching_live, key=lambda a: (0 if a.scope == SCOPE_DATABASE else 1, a.id)
        )
        return APPROVAL_STATUS_APPROVED, database_scope_first[0]

    if live:
        return APPROVAL_STATUS_STALE_HASH, None

    revoked_by_hand = [
        a
        for a in applicable
        if a.revoked_at is not None
        and a.revoke_reason != REVOKE_REASON_SUPERSEDED
        and _hash_matches(a, current_hash)
    ]
    if revoked_by_hand:
        return APPROVAL_STATUS_REVOKED, max(revoked_by_hand, key=lambda a: a.id)

    return APPROVAL_STATUS_UNAPPROVED, None


def load_approval_rows(
    session: Session, migration_ids: Sequence[int], direction: str
) -> dict[int, list[MigrationDestructiveApproval]]:
    """Todas las filas de aprobación de esas versiones y dirección, agrupadas por versión."""
    rows_by_migration: dict[int, list[MigrationDestructiveApproval]] = {
        migration_id: [] for migration_id in migration_ids
    }
    if not migration_ids:
        return rows_by_migration
    rows = (
        session.query(MigrationDestructiveApproval)
        .filter(
            MigrationDestructiveApproval.model_migration_id.in_(list(migration_ids)),
            MigrationDestructiveApproval.direction == direction,
        )
        .order_by(MigrationDestructiveApproval.id.asc())
        .all()
    )
    for row in rows:
        rows_by_migration[row.model_migration_id].append(row)
    return rows_by_migration


def resolve(
    session: Session,
    managed_database_id: int,
    direction: str,
    hits: Sequence[MigrationSpec],
) -> Resolution:
    """
    Resuelve qué versiones destructivas pendientes (``hits``) están aprobadas para esta base.

    Lee de la ``session`` que recibe: quien la use como RE-CHEQUEO de revocación (justo antes de
    ejecutar) tiene que pasarle una sesión fresca, o verá el estado que ya tenía cacheado.
    """
    rows_by_migration = load_approval_rows(session, [spec.id for spec in hits], direction)

    approved_entries: list[ApprovalEntry] = []
    unapproved_versions: list[str] = []
    statuses: dict[str, VersionApproval] = {}

    for spec in hits:
        current_hash = approval_hash(spec, direction)
        status, satisfying_row = classify_approval(
            rows_by_migration[spec.id], current_hash, managed_database_id
        )
        statuses[spec.version] = VersionApproval(
            migration_id=spec.id,
            version=spec.version,
            direction=direction,
            current_hash=current_hash,
            status=status,
            approval_id=satisfying_row.id if satisfying_row is not None else None,
            scope=satisfying_row.scope if satisfying_row is not None else None,
        )
        # Por el ESTADO, no por la fila: ``classify_approval`` también devuelve la fila de una
        # aprobación ``revoked`` (para que el plan muestre cuál se revocó), y contarla como
        # aprobada haría que revocar no bloquee nada.
        if status == APPROVAL_STATUS_APPROVED:
            approved_entries.append(
                ApprovalEntry(
                    migration_id=spec.id,
                    version=spec.version,
                    sql_hash=current_hash,
                    approval_id=satisfying_row.id,
                    scope=satisfying_row.scope,
                )
            )
        else:
            unapproved_versions.append(spec.version)

    return Resolution(
        approved=tuple(approved_entries),
        unapproved=tuple(unapproved_versions),
        statuses=statuses,
    )


def load_approvals_by_id(
    session: Session, approval_ids: Sequence[int]
) -> dict[int, MigrationDestructiveApproval]:
    """
    Las filas de aprobación por id. Las usa la auditoría del consumo, que además de QUÉ versión se
    ejecutó tiene que nombrar quién aprobó y quién la escribió (A-10): ``ApprovalEntry`` solo lleva
    lo que ata el token, no la autoría.
    """
    if not approval_ids:
        return {}
    rows = (
        session.query(MigrationDestructiveApproval)
        .filter(MigrationDestructiveApproval.id.in_(list(approval_ids)))
        .all()
    )
    return {row.id: row for row in rows}


# --------------------------------------------------------------------------- #
# Versiones destructivas en la dirección down                                  #
# --------------------------------------------------------------------------- #
def destructive_reasons(facts: migration_facts.MigrationFacts) -> list[str]:
    """
    Motivos legibles por los que un SQL analizado es destructivo; vacío si no lo es.

    Única fuente del texto para la subida (``ManagedMigrationController._destructive_versions``)
    y la bajada (``destructive_down_versions``): el operador compara estos motivos entre el plan
    de apply y el de rollback, y dos redacciones distintas del mismo hecho lo confundirían.
    """
    reasons: list[str] = []
    if facts.destructive_statements:
        reasons.append(
            "sentencias " + ",".join(str(index) for index in facts.destructive_statements)
        )
    if facts.charset_conversion_statements:
        reasons.append(
            "conversión de charset/collation en sentencias "
            + ",".join(str(index) for index in facts.charset_conversion_statements)
        )
    return reasons


def effective_down_sql(
    runner: MigrationRunner, spec: MigrationSpec, engine: EngineType
) -> str | None:
    """
    El reverso que el rollback EJECUTA en este motor, unido en un solo texto; ``None`` si no hay.

    Sale de ``MigrationRunner.statement_lists``, la misma fuente que el codegen del
    ``downgrade()``, para que el guard clasifique exactamente lo que va a correr.
    """
    _, down_statements, _ = runner.statement_lists(spec, engine)
    if not down_statements:
        return None
    return ";\n".join(down_statements)


def destructive_down_versions(
    runner: MigrationRunner, specs: Sequence[MigrationSpec], engine: EngineType
) -> dict[str, tuple[str, ...]]:
    """
    Qué versiones tienen un ``down_sql`` destructivo para ESTE motor, y por qué.

    Mismo criterio que ``ManagedMigrationController._destructive_versions`` para la subida
    (``migration_facts.analyze`` sobre el SQL resuelto para el motor, charset incluido), porque
    dos clasificadores distintos terminarían contradiciéndose y el permisivo gobernaría el guard.
    Analiza el reverso EFECTIVO (``effective_down_sql``), no ``down_sql``: con manifiesto usable
    el rollback ejecuta los reversos de sus filas, y clasificar otro texto dejaría pasar un
    ``DROP`` del manifiesto detrás de un ``down_sql`` inocuo. El flag ``destructive`` del
    manifiesto no se usa: describe la subida, no la bajada. Una versión sin reverso no aparece
    (no hay nada que revertir ni que aprobar).
    """
    destructive_versions: dict[str, tuple[str, ...]] = {}
    for spec in specs:
        down_sql = effective_down_sql(runner, spec, engine)
        if not down_sql:
            continue
        facts = migration_facts.analyze(down_sql, spec.kind, spec.has_non_portable)
        reasons = destructive_reasons(facts)
        if reasons:
            destructive_versions[spec.version] = tuple(reasons)
    return destructive_versions


# --------------------------------------------------------------------------- #
# Subject del token de confirmación                                            #
# --------------------------------------------------------------------------- #
#: Etiquetas del destino cuando no hay versión explícita: apply sin ``version`` va al head y un
#: rollback sin ``target_version`` desciende hasta la base (sin ninguna versión aplicada).
TARGET_LABEL_HEAD = "head"
TARGET_LABEL_BASE = "base"


def db_subject(
    managed_database_id: int,
    direction: str,
    target_label: str,
    from_version: str | None,
    entries: Iterable[ApprovalEntry],
) -> str:
    """
    Subject del token de UNA base: ata el token a la base, la dirección, el destino, la versión de
    partida y al conjunto EXACTO de aprobaciones (versión + hash + id de aprobación).

    Atar ``from_version`` hace que un token emitido para "0003 -> 0005" no sirva si entre el plan
    y la ejecución alguien movió la base. Atar el id de aprobación, no solo el hash, hace que
    revocar y volver a aprobar el mismo SQL invalide el token: la autorización que lo respaldaba
    ya no es la misma. Las entradas van ordenadas para que el orden de resolución no cambie el
    subject entre el dry-run y la ejecución.
    """
    approvals_part = ",".join(
        sorted(f"{e.migration_id}:{e.sql_hash}:{e.approval_id}" for e in entries)
    )
    return (
        f"db={managed_database_id};dir={direction};target={target_label};"
        f"from={from_version or TARGET_LABEL_BASE};{approvals_part}"
    )


def batch_subject(
    model_id: int,
    target_label: str,
    entries: Iterable[tuple[int, int, str, int]],
) -> str:
    """
    Subject del token de ``apply_all``. Cada entrada es ``(db_id, migration_id, sql_hash,
    approval_id)``.

    Es ESTÁTICO a propósito: se calcula solo con aprobaciones y versiones del blueprint, sin leer
    ninguna base destino, para poder verificarlo ANTES de tocar la primera. Lo que cambie entre el
    dry-run y la ejecución (una revocación, una nueva aprobación) cambia el subject y el token
    deja de valer.
    """
    entries_part = ",".join(
        sorted(f"{db}:{mid}:{sql_hash}:{aid}" for db, mid, sql_hash, aid in entries)
    )
    return f"model={model_id};target={target_label};{entries_part}"


# --------------------------------------------------------------------------- #
# Token de confirmación                                                        #
# --------------------------------------------------------------------------- #
#: status de ``confirm_token.verify`` → (código del catálogo, mensaje para el operador).
_VERIFY_ERRORS_BY_STATUS: dict[int, tuple[str, str]] = {
    _HTTP_UNPROCESSABLE: (
        CODE_CONFIRMATION_INVALID,
        "El token de confirmación no corresponde a esta operación. Si cambió el SQL, las "
        "aprobaciones o la base, solicite de nuevo el plan (dry-run).",
    ),
    _HTTP_GONE: (
        CODE_CONFIRMATION_EXPIRED,
        "El token de confirmación expiró; solicite de nuevo el plan (dry-run).",
    ),
}


def wrap_verify(
    token: str, operation: str, server_id: int, db_name: str, *, subject: str
) -> None:
    """
    ``confirm_token.verify`` con los errores en el vocabulario del catálogo.

    ``verify`` levanta 422/410 SIN código en ``public_context``: la pantalla no podría distinguir
    "expiró, pedí otro plan" de "no corresponde". Acá se traducen a
    ``destructive_approval.confirmation_invalid`` / ``confirmation_expired`` conservando el
    status. El mensaje de ``verify`` no se reenvía: es genérico, pero el catálogo es la fuente.
    """
    try:
        confirm_token.verify(token, operation, server_id, db_name, subject=subject)
    except AppHttpException as exc:
        translated_error = _VERIFY_ERRORS_BY_STATUS.get(exc.status_code)
        if translated_error is None:
            raise
        error_code, error_message = translated_error
        raise AppHttpException(
            message=error_message,
            status_code=exc.status_code,
            public_context={"code": error_code},
        ) from exc
