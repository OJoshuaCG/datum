"""
Vocabulario cerrado de las APROBACIONES de migraciones destructivas.

Una migración destructiva (``DROP TABLE``, ``DROP COLUMN``, ``TRUNCATE``…) no se ejecuta sobre un
entorno que bloquea lo destructivo (``blocks_destructive_migrations``) hasta que una SEGUNDA
persona la aprueba. Este módulo es la fuente de los literales que comparten el modelo
(``MigrationDestructiveApproval``), la migración de Alembic, el servicio de política y los
controllers, para que ninguno repita un string.

Además fija el vocabulario CERRADO del flujo de aprobación: los códigos de error (siempre en
``public_context["code"]``, nunca en ``context``, que solo se expone en ``development``), las
operaciones firmadas del token de confirmación, su TTL y las acciones de auditoría.
"""

#: Dirección de la migración que cubre la aprobación. Una aprobación ``up`` NO cubre el
#: rollback: el ``down_sql`` es otro SQL, con otro hash y puede destruir cosas distintas.
DIRECTION_UP = "up"
DIRECTION_DOWN = "down"
DESTRUCTIVE_APPROVAL_DIRECTIONS: tuple[str, ...] = (DIRECTION_UP, DIRECTION_DOWN)

#: Alcance de la aprobación: una base puntual, o todas las bases del blueprint que viven en un
#: entorno protegido (incluidas las que se creen después de aprobar).
SCOPE_DATABASE = "database"
SCOPE_BLUEPRINT = "blueprint"
DESTRUCTIVE_APPROVAL_SCOPES: tuple[str, ...] = (SCOPE_DATABASE, SCOPE_BLUEPRINT)

#: Base del hash que ata la aprobación al SQL aprobado: SHA-256 del texto FUENTE tal como lo
#: escribió el autor (``up_sql`` y sus overrides por motor, o ``down_sql``), no del SQL ya
#: resuelto por motor. Así una actualización de sqlglot no deja huérfana ninguna aprobación. La
#: columna ``hash_basis`` existe para que una canonicalización futura conviva con las filas viejas.
HASH_BASIS_RAW_SOURCE_V1 = "raw_source_v1"

#: Valor de ``scope_key`` para una aprobación de alcance blueprint (la de alcance base usa
#: ``database:{id}``, ver ``scope_key_for``).
SCOPE_KEY_BLUEPRINT = "blueprint"
SCOPE_KEY_DATABASE_PREFIX = "database:"

#: Valor de ``live_key`` mientras la aprobación está vigente (la revocada lo deja en NULL).
LIVE_KEY_ACTIVE = 1


def scope_key_for(scope: str, managed_database_id: int | None) -> str:
    """
    Clave de unicidad del alcance: ``'blueprint'`` o ``'database:{id}'``.

    Existe porque ``managed_database_id`` es NULL en el alcance blueprint, y en un ``UNIQUE`` los
    NULL son distintos en MySQL, MariaDB, PostgreSQL y SQLite: sin una columna que SIEMPRE tenga
    valor, dos aprobaciones vivas del mismo alcance blueprint no chocarían.
    """
    if scope == SCOPE_BLUEPRINT:
        return SCOPE_KEY_BLUEPRINT
    if scope == SCOPE_DATABASE and managed_database_id is not None:
        return f"{SCOPE_KEY_DATABASE_PREFIX}{managed_database_id}"
    raise ValueError(
        "El alcance 'database' exige managed_database_id y el alcance debe ser uno de "
        f"{DESTRUCTIVE_APPROVAL_SCOPES}."
    )


#: Alias de ``HASH_BASIS_RAW_SOURCE_V1`` con el nombre que usa el servicio de política al hashear.
HASH_BASIS = HASH_BASIS_RAW_SOURCE_V1

#: Motivo con el que se cierra una aprobación que la reemplazó otra (re-aprobación tras editar el
#: SQL). Distingue "reemplazada" de "revocada a mano" al calcular ``approval_status``.
REVOKE_REASON_SUPERSEDED = "superseded"

#: Largo del motivo declarado al aprobar y al revocar (se guarda en columnas de 500).
REASON_MIN_LENGTH = 10
REASON_MAX_LENGTH = 500

# --------------------------------------------------------------------------- #
# Estado de aprobación de una versión (A-5)                                     #
# --------------------------------------------------------------------------- #
#: ``approved``: hay una aprobación vigente cuyo hash coincide con el SQL actual.
#: ``unapproved``: nunca se aprobó (o la única aprobación es de un SQL que ya no existe y no
#: quedó vigente).
#: ``revoked``: la aprobación de ESTE SQL se revocó a mano y no hay otra vigente.
#: ``stale_hash``: hay una aprobación vigente pero de otro SQL (se editó después de aprobar) o
#: calculada con otra ``hash_basis``: requiere re-aprobación, nunca se toma por aprobada.
APPROVAL_STATUS_APPROVED = "approved"
APPROVAL_STATUS_UNAPPROVED = "unapproved"
APPROVAL_STATUS_REVOKED = "revoked"
APPROVAL_STATUS_STALE_HASH = "stale_hash"
APPROVAL_STATUSES: tuple[str, ...] = (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_UNAPPROVED,
    APPROVAL_STATUS_REVOKED,
    APPROVAL_STATUS_STALE_HASH,
)

# --------------------------------------------------------------------------- #
# Códigos de error (public_context["code"])                                     #
# --------------------------------------------------------------------------- #
_CODE_PREFIX = "destructive_approval."

CODE_NOT_FOUND = f"{_CODE_PREFIX}not_found"  # 404
CODE_NOT_DESTRUCTIVE = f"{_CODE_PREFIX}not_destructive"  # 422: no hay nada que aprobar
CODE_DOWN_SQL_MISSING = f"{_CODE_PREFIX}down_sql_missing"  # 422: dirección down sin down_sql
CODE_SCOPE_INVALID = f"{_CODE_PREFIX}scope_invalid"  # 422: la base no es del blueprint
CODE_SQL_CHANGED = f"{_CODE_PREFIX}sql_changed"  # 409: expected_sql_hash != SQL actual
CODE_DUPLICATE = f"{_CODE_PREFIX}duplicate"  # 409: ya hay una aprobación vigente igual
CODE_MACHINE_ACTOR = f"{_CODE_PREFIX}machine_actor"  # 403: una máquina no aprueba
CODE_CONFIRMATION_REQUIRED = f"{_CODE_PREFIX}confirmation_required"  # 409
CODE_CONFIRMATION_INVALID = f"{_CODE_PREFIX}confirmation_invalid"  # 422
CODE_CONFIRMATION_EXPIRED = f"{_CODE_PREFIX}confirmation_expired"  # 410
CODE_CONFIRM_PHRASE_MISMATCH = f"{_CODE_PREFIX}confirm_phrase_mismatch"  # 422
CODE_BATCH_MULTI_ENVIRONMENT = f"{_CODE_PREFIX}batch_multi_environment"  # 422
CODE_EXECUTOR_FORBIDDEN = f"{_CODE_PREFIX}executor_forbidden"  # 403: falta apply_destructive

#: No existe ``already_revoked``: revocar algo ya revocado es idempotente (200 con la fila
#: intacta), así que no hay error que nombrar.
ERROR_CODES: frozenset[str] = frozenset(
    {
        CODE_NOT_FOUND,
        CODE_NOT_DESTRUCTIVE,
        CODE_DOWN_SQL_MISSING,
        CODE_SCOPE_INVALID,
        CODE_SQL_CHANGED,
        CODE_DUPLICATE,
        CODE_MACHINE_ACTOR,
        CODE_CONFIRMATION_REQUIRED,
        CODE_CONFIRMATION_INVALID,
        CODE_CONFIRMATION_EXPIRED,
        CODE_CONFIRM_PHRASE_MISMATCH,
        CODE_BATCH_MULTI_ENVIRONMENT,
        CODE_EXECUTOR_FORBIDDEN,
    }
)

#: Qué regla de cuatro ojos se violó. Viaja en ``public_context["rule"]`` junto a
#: ``access.self_approval_forbidden`` para que la pantalla diga a quién le toca ceder.
SELF_APPROVAL_RULE_CREATOR = "creator"
SELF_APPROVAL_RULE_LAST_EDITOR = "last_editor"

# --------------------------------------------------------------------------- #
# Token de confirmación                                                         #
# --------------------------------------------------------------------------- #
#: Operaciones firmadas en el ``confirm_token``. Son operaciones NUEVAS: los tokens ya emitidos
#: para otras operaciones no se ven afectados.
OPERATION_APPLY_DESTRUCTIVE = "migration.apply_destructive"
OPERATION_APPLY_ALL_DESTRUCTIVE = "migration.apply_all_destructive"
OPERATION_ROLLBACK_DESTRUCTIVE = "migration.rollback_destructive"

#: 300 s y no los 120 s por defecto del token: cubre leer el plan destructivo, reescribir el
#: nombre de la base y pasar por el prompt de step-up (ventana de 5 minutos). El subject del token
#: ata el conjunto exacto de aprobaciones, así que un TTL más largo no amplía lo que autoriza; una
#: revocación posterior igual la atrapa el re-chequeo por base al ejecutar.
DESTRUCTIVE_CONFIRM_TTL_SECONDS = 300

# --------------------------------------------------------------------------- #
# Auditoría                                                                     #
# --------------------------------------------------------------------------- #
AUDIT_ACTION_GRANT = "migration.destructive_approval.grant"
AUDIT_ACTION_REVOKE = "migration.destructive_approval.revoke"
AUDIT_ACTION_CONSUME = "migration.destructive_approval.consume"
