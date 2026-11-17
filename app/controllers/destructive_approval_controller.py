"""
Controller de las APROBACIONES de migraciones destructivas: aprobar, revocar y listar.

Escribe SOLO metadatos del gateway (``migration_destructive_approvals``); no abre conexión a
ningún motor. Quien CONSUME las aprobaciones (el guard de ``managed_migration_controller``) lee
las mismas filas a través de ``destructive_approval_policy.resolve``.

LA REGLA DE CUATRO OJOS SALE DE LOS DATOS, NO DE UN ROL
-------------------------------------------------------
``blueprints.approve_destructive`` y ``blueprints.apply_destructive`` son de ``owner``, y un
owner puede haber escrito el SQL. Por eso el aprobador tiene que ser una persona DISTINTA del
creador de la versión y de su último editor (``ModelMigration.last_sql_editor_*``): se compara
contra los datos, no contra el rol.
"""

import json
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.controllers.managed_migration_controller import ManagedMigrationController
from app.core.actor import identity_of
from app.core.database import Database
from app.core.logger import get_logger
from app.core.scope import ScopePoint, assert_at_point
from app.exceptions import AppHttpException
from app.models.database_model import DatabaseModel
from app.models.enums import EngineType
from app.models.environment import Environment
from app.models.managed_database import ManagedDatabase
from app.models.migration_destructive_approval import MigrationDestructiveApproval
from app.models.model_migration import ModelMigration
from app.services import audit
from app.services.capability_catalog import CODE_SELF_APPROVAL, Capability
from app.services.db_admin.migrations import MigrationRunner, MigrationSpec
from app.services.destructive_approval_catalog import (
    APPROVAL_STATUS_APPROVED,
    AUDIT_ACTION_GRANT,
    AUDIT_ACTION_REVOKE,
    BATCH_ITEM_STATUS_APPROVED,
    BATCH_ITEM_STATUS_FAILED,
    CODE_BATCH_ITEM_FAILED,
    CODE_DOWN_SQL_MISSING,
    CODE_DUPLICATE,
    CODE_MACHINE_ACTOR,
    CODE_NOT_DESTRUCTIVE,
    CODE_NOT_FOUND,
    CODE_SCOPE_INVALID,
    CODE_SQL_CHANGED,
    DESTRUCTIVE_APPROVAL_DIRECTIONS,
    DIRECTION_DOWN,
    DIRECTION_UP,
    HASH_BASIS,
    LIVE_KEY_ACTIVE,
    REVOKE_REASON_SUPERSEDED,
    SCOPE_BLUEPRINT,
    SCOPE_DATABASE,
    SELF_APPROVAL_RULE_CREATOR,
    SELF_APPROVAL_RULE_LAST_EDITOR,
    scope_key_for,
)
from app.services.destructive_approval_policy import (
    approval_hash,
    author_person_id,
    classify_approval,
    destructive_down_versions,
    load_approval_rows,
    person_id,
)
from app.services.destructive_verdicts import (
    load_verdict_rows,
    store_verdict,
    stored_reasons_if_current,
)
from app.services.db_admin.migration_integrity import version_sort_key

logger = get_logger(__name__)

#: Largo máximo de ``destructive_summary`` (columna ``String(500)``).
_SUMMARY_MAX_LENGTH = 500

#: Tipo de objeto de auditoría de estas acciones: el blanco es la VERSIÓN, no la fila de aprobación.
_AUDIT_TARGET_TYPE = "model_migration"


def _utcnow() -> datetime:
    """UTC naive: así guarda sus fechas todo el esquema de metadatos del gateway."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _actor_ref(admin_id: int | None, username: str | None) -> dict:
    return {"admin_id": admin_id, "username": username}


def refresh_destructive_verdicts_safely(
    session, model_id: int | None, version: str | None = None
) -> None:
    """
    Gancho para los flujos que crean o editan versiones: precalcula el veredicto de destructividad
    para que el próximo listado no tenga que analizar. Con ``version`` solo esa; sin ella, todas
    las del blueprint (alta desde snapshot).

    Nunca rompe la operación que lo llama. El veredicto es un dato derivado y el listado lo
    recalcula si falta o si su huella no coincide, así que fallar acá solo deja una lectura lenta,
    mientras que tumbar el alta o la edición de una versión por esto sería peor. Hace su propio
    ``commit``; llamarlo DESPUÉS del commit de la operación, nunca dentro de su transacción.
    """
    if model_id is None:
        return
    try:
        specs = ManagedMigrationController._load_specs(session, model_id, only_version=version)
        DestructiveApprovalController().refresh_verdicts(session, specs)
        session.commit()
    except Exception:  # noqa: BLE001 — ver docstring
        session.rollback()
        logger.exception(
            "No se pudieron precalcular los veredictos destructivos del blueprint %s", model_id
        )


class DestructiveApprovalController:
    def __init__(self) -> None:
        # El analizador de "¿es destructiva?" es el MISMO que usa el guard al ejecutar. Si el
        # approve usara otro, una versión que el guard bloquea podría ser "no destructiva" acá y
        # quedar imposible de aprobar (o al revés: aprobable y nunca bloqueada).
        self._migration_controller = ManagedMigrationController()
        self._runner = MigrationRunner()

    @staticmethod
    def _session():
        return Database().get_declarative_base_session()

    # ------------------------------------------------------------------ #
    # Carga y validación de contexto                                      #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _not_found(message: str, **context) -> AppHttpException:
        return AppHttpException(
            message=message,
            status_code=404,
            public_context={"code": CODE_NOT_FOUND},
            context=context,
        )

    def _migration_or_404(self, session, model_id: int, version: str) -> ModelMigration:
        if session.get(DatabaseModel, model_id) is None:
            raise self._not_found("Blueprint no encontrado.", model_id=model_id)
        migration = (
            session.query(ModelMigration)
            .filter(ModelMigration.model_id == model_id, ModelMigration.version == version)
            .first()
        )
        if migration is None:
            raise self._not_found(
                "La versión no existe en el blueprint.", model_id=model_id, version=version
            )
        return migration

    def _assert_database_belongs_to_model(
        self, session, managed_database_id: int, model_id: int
    ) -> None:
        managed_database = session.get(ManagedDatabase, managed_database_id)
        if managed_database is None:
            raise self._not_found(
                "Base de datos gestionada no encontrada.", managed_database_id=managed_database_id
            )
        if managed_database.model_id != model_id:
            raise AppHttpException(
                message="La base de datos no usa este blueprint: no hay nada que aprobar para ella.",
                status_code=422,
                public_context={"code": CODE_SCOPE_INVALID},
                context={"managed_database_id": managed_database_id, "model_id": model_id},
            )

    def model_id_of_database(self, managed_database_id: int) -> int:
        """
        El blueprint de una base, para la ruta de aprobación POR BASE (que recibe ``db_id`` y no
        el blueprint). Una base sin blueprint no tiene versiones que aprobar: 422, no 404.
        """
        session = self._session()
        try:
            managed_database = session.get(ManagedDatabase, managed_database_id)
            if managed_database is None:
                raise self._not_found(
                    "Base de datos gestionada no encontrada.",
                    managed_database_id=managed_database_id,
                )
            if managed_database.model_id is None:
                raise AppHttpException(
                    message="La base de datos no tiene un blueprint asignado: no hay nada que aprobar.",
                    status_code=422,
                    public_context={"code": CODE_SCOPE_INVALID},
                    context={"managed_database_id": managed_database_id},
                )
            return managed_database.model_id
        finally:
            session.close()

    @staticmethod
    def _assert_not_machine(actor) -> None:
        """
        Un token de agente o de integración NUNCA aprueba. Aprobar es la mitad humana de los
        cuatro ojos; una máquina que la ejerza deja de haber una segunda persona.
        """
        if getattr(actor, "is_machine", False):
            raise AppHttpException(
                message="Solo una persona puede aprobar una migración destructiva.",
                status_code=403,
                public_context={"code": CODE_MACHINE_ACTOR},
            )

    @classmethod
    def is_approver_of_record(cls, actor, approval_id: int) -> bool:
        """
        ¿El actor es la persona que firmó esta aprobación? Autoriza a REVOCARLA sin la capacidad
        (enmienda A-2): quien aprobó tiene que poder frenar su propio error aunque después le hayan
        quitado ``approve_destructive``. Revocar solo bloquea, nunca habilita una ejecución.

        Una máquina nunca es aprobadora de registro: un actor de integración lleva el ``id`` de su
        emisor, y sin este corte un token heredaría el permiso de revocar de la persona.
        """
        if getattr(actor, "is_machine", False):
            return False
        actor_id, _ = identity_of(actor)
        if actor_id is None:
            return False
        session = cls._session()
        try:
            approver_id = (
                session.query(MigrationDestructiveApproval.approved_by_admin_id)
                .filter(MigrationDestructiveApproval.id == approval_id)
                .scalar()
            )
        finally:
            session.close()
        return approver_id is not None and approver_id == actor_id

    @staticmethod
    def _assert_authority_over_every_protected_environment(session, actor) -> None:
        """
        Una aprobación de alcance blueprint cubre también las bases FUTURAS, así que quien la da
        tiene que poder aprobar en CADA entorno que bloquea lo destructivo, no solo donde hoy hay
        bases. Con el destino ``model`` solo (el guard de la ruta) un owner acotado a staging
        pre-aprobaría las futuras bases de producción de un blueprint que aún no tiene ninguna.

        Se recorren TODOS los entornos bloqueantes, activos o no: ``_env_policy_for`` del guard no
        filtra por ``is_active``, así que una base en un entorno inactivo que bloquea también
        queda cubierta por esta aprobación.
        """
        blocking_environment_ids = [
            environment_id
            for (environment_id,) in session.query(Environment.id)
            .filter(Environment.blocks_destructive_migrations.is_(True))
            .all()
        ]
        for environment_id in blocking_environment_ids:
            assert_at_point(
                actor,
                Capability.BLUEPRINTS_APPROVE_DESTRUCTIVE,
                ScopePoint(environment_id=environment_id, server_id=None),
            )

    # ------------------------------------------------------------------ #
    # Análisis del SQL                                                    #
    # ------------------------------------------------------------------ #
    def _reasons_by_engine(self, spec: MigrationSpec, direction: str) -> dict[str, tuple[str, ...]]:
        """
        Por qué el SQL de esa dirección es destructivo, motor por motor (solo aparecen los motores
        donde lo es). Se mira CADA motor porque una aprobación de alcance blueprint vale para
        bases de cualquiera, y el ``DROP`` puede vivir solo en el override de uno.
        """
        reasons_by_engine: dict[str, tuple[str, ...]] = {}
        for engine in EngineType:
            if direction == DIRECTION_UP:
                destructive_versions = self._migration_controller._destructive_versions(
                    [spec], engine
                )
            else:
                destructive_versions = destructive_down_versions(self._runner, [spec], engine)
            reasons = destructive_versions.get(spec.version)
            if reasons:
                reasons_by_engine[engine.value] = reasons
        return reasons_by_engine

    def refresh_verdicts(self, session, specs: list[MigrationSpec]) -> int:
        """
        Recalcula y guarda el veredicto de cada dirección de esas versiones. NO hace ``commit``:
        lo decide el llamador, que sabe qué más viaja en esa transacción. Devuelve cuántos
        veredictos escribió.

        Guarda también los NO destructivos (``{}``): sin esa fila, "no destructiva" y "nunca
        analizada" serían indistinguibles y el listado volvería a parsear toda versión limpia.
        """
        written_count = 0
        for spec in specs:
            for direction in DESTRUCTIVE_APPROVAL_DIRECTIONS:
                if direction == DIRECTION_DOWN and not spec.down_sql:
                    continue
                store_verdict(session, spec, direction, self._reasons_by_engine(spec, direction))
                written_count += 1
        return written_count

    @staticmethod
    def _summary_of(reasons_by_engine: dict[str, tuple[str, ...]]) -> str:
        parts = [
            f"{engine}: {', '.join(reasons)}" for engine, reasons in sorted(reasons_by_engine.items())
        ]
        return "; ".join(parts)[:_SUMMARY_MAX_LENGTH]

    # ------------------------------------------------------------------ #
    # Serialización                                                       #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _serialize_approval(approval: MigrationDestructiveApproval) -> dict:
        revoked_by = (
            _actor_ref(approval.revoked_by_admin_id, approval.revoked_by_username)
            if approval.revoked_at is not None
            else None
        )
        last_executed_by = (
            _actor_ref(approval.last_executed_by_admin_id, approval.last_executed_by_username)
            if approval.last_executed_at is not None
            else None
        )
        return {
            "id": approval.id,
            "model_migration_id": approval.model_migration_id,
            "database_model_id": approval.database_model_id,
            "version": approval.version_label,
            "direction": approval.direction,
            "scope": approval.scope,
            "managed_database_id": approval.managed_database_id,
            "sql_hash": approval.sql_hash,
            "hash_basis": approval.hash_basis,
            "destructive_summary": approval.destructive_summary,
            "author": _actor_ref(approval.author_admin_id, approval.author_username),
            "last_sql_editor": _actor_ref(approval.editor_admin_id, approval.editor_username),
            "author_unknown": bool(approval.author_unknown),
            "reason": approval.reason,
            "approved_by": _actor_ref(approval.approved_by_admin_id, approval.approved_by_username),
            "approved_at": approval.created_at,
            "is_live": approval.live_key is not None,
            "revoked_at": approval.revoked_at,
            "revoked_by": revoked_by,
            "revoke_reason": approval.revoke_reason,
            "last_executed_at": approval.last_executed_at,
            "last_executed_by": last_executed_by,
            "execution_count": approval.execution_count,
        }

    @staticmethod
    def _audit_detail(approval: MigrationDestructiveApproval, **extra) -> str:
        """
        Resumen de auditoría SIN el motivo libre ni el SQL: autoría, alcance, dirección, versión
        y hash alcanzan para reconstruir quién autorizó qué, y ``reason`` ya vive en la fila.
        """
        detail = {
            "approval_id": approval.id,
            "version": approval.version_label,
            "direction": approval.direction,
            "scope": approval.scope,
            "managed_database_id": approval.managed_database_id,
            "sql_hash": approval.sql_hash,
            "author": approval.author_admin_id,
            "last_sql_editor": approval.editor_admin_id,
            "approver": approval.approved_by_admin_id,
            "author_unknown": bool(approval.author_unknown),
            **extra,
        }
        return json.dumps(detail, sort_keys=True, ensure_ascii=False)

    # ------------------------------------------------------------------ #
    # Aprobar                                                             #
    # ------------------------------------------------------------------ #
    def approve(
        self,
        model_id: int,
        version: str,
        *,
        direction: str,
        expected_sql_hash: str,
        reason: str,
        actor,
        managed_database_id: int | None = None,
    ) -> dict:
        """
        Aprueba UNA versión en UNA dirección, ligada al hash del SQL que el aprobador revisó.

        Alcance ``database`` si llega ``managed_database_id`` (la ruta por base), ``blueprint`` si
        no. Aprobar de nuevo después de editar el SQL REEMPLAZA la aprobación vieja en la misma
        transacción (queda como ``superseded``); aprobar el MISMO hash otra vez es 409 ``duplicate``.
        """
        self._assert_not_machine(actor)
        if direction not in DESTRUCTIVE_APPROVAL_DIRECTIONS:
            raise ValueError(f"Dirección desconocida: {direction!r}.")

        session = self._session()
        try:
            migration = self._migration_or_404(session, model_id, version)

            if managed_database_id is not None:
                scope = SCOPE_DATABASE
                self._assert_database_belongs_to_model(session, managed_database_id, model_id)
            else:
                scope = SCOPE_BLUEPRINT
                self._assert_authority_over_every_protected_environment(session, actor)

            if direction == DIRECTION_DOWN and not migration.down_sql:
                raise AppHttpException(
                    message="La versión no tiene down_sql: no hay rollback que aprobar.",
                    status_code=422,
                    public_context={"code": CODE_DOWN_SQL_MISSING},
                )

            approver_person_id = person_id(actor)
            creator_person_id = author_person_id(
                migration.created_by_admin_id, migration.created_by_actor_type
            )
            editor_person_id = migration.last_sql_editor_admin_id
            self._assert_four_eyes(approver_person_id, creator_person_id, editor_person_id)

            # "No destructivo" va ANTES que el hash, igual que ``down_sql_missing``: es una
            # imposibilidad que ningún hash corrige. Al revés, el operador recibiría
            # ``sql_changed``, releería la versión y recién al reintentar sabría que no había
            # nada que aprobar.
            spec = ManagedMigrationController._load_specs(
                session, model_id, only_version=version
            )[0]
            reasons_by_engine = self._reasons_by_engine(spec, direction)
            if not reasons_by_engine:
                raise AppHttpException(
                    message=(
                        "El SQL de esta dirección no es destructivo en ningún motor: no hay "
                        "nada que aprobar."
                    ),
                    status_code=422,
                    public_context={"code": CODE_NOT_DESTRUCTIVE},
                )

            current_hash = approval_hash(spec, direction)
            if expected_sql_hash != current_hash:
                raise AppHttpException(
                    message=(
                        "El SQL cambió desde que lo revisó. Vuelva a leer la versión antes de "
                        "aprobar."
                    ),
                    status_code=409,
                    public_context={"code": CODE_SQL_CHANGED},
                )

            scope_key = scope_key_for(scope, managed_database_id)
            now = _utcnow()
            approver_id, approver_username = identity_of(actor)

            live_row = (
                session.query(MigrationDestructiveApproval)
                .filter(
                    MigrationDestructiveApproval.model_migration_id == migration.id,
                    MigrationDestructiveApproval.direction == direction,
                    MigrationDestructiveApproval.scope_key == scope_key,
                    MigrationDestructiveApproval.live_key == LIVE_KEY_ACTIVE,
                )
                .first()
            )
            if live_row is not None:
                if live_row.sql_hash == current_hash and live_row.hash_basis == HASH_BASIS:
                    raise AppHttpException(
                        message="Esta versión ya tiene una aprobación vigente para ese SQL.",
                        status_code=409,
                        public_context={"code": CODE_DUPLICATE},
                        context={"approval_id": live_row.id},
                    )
                # El SQL cambió desde la aprobación vieja: se cierra como "reemplazada" y se
                # emite la nueva en la MISMA transacción. El flush explícito garantiza que el
                # UPDATE (que libera ``live_key``) corra antes del INSERT de la nueva, o el
                # UNIQUE (versión, dirección, alcance, live_key) chocaría consigo mismo.
                live_row.live_key = None
                live_row.revoked_at = now
                live_row.revoked_by_admin_id = approver_id
                live_row.revoked_by_username = approver_username
                live_row.revoke_reason = REVOKE_REASON_SUPERSEDED
                session.flush()

            approval = MigrationDestructiveApproval(
                model_migration_id=migration.id,
                database_model_id=model_id,
                direction=direction,
                scope=scope,
                managed_database_id=managed_database_id,
                scope_key=scope_key,
                sql_hash=current_hash,
                hash_basis=HASH_BASIS,
                version_label=migration.version,
                destructive_summary=self._summary_of(reasons_by_engine),
                author_admin_id=creator_person_id,
                author_username=migration.created_by_username,
                editor_admin_id=editor_person_id,
                editor_username=migration.last_sql_editor_username,
                author_unknown=creator_person_id is None or editor_person_id is None,
                reason=reason,
                approved_by_admin_id=approver_id,
                approved_by_username=approver_username,
                live_key=LIVE_KEY_ACTIVE,
            )
            session.add(approval)
            try:
                session.commit()
            except IntegrityError as exc:
                # Dos aprobaciones concurrentes del mismo alcance: ganó la otra (UNIQUE).
                session.rollback()
                raise AppHttpException(
                    message="Esta versión ya tiene una aprobación vigente para ese alcance.",
                    status_code=409,
                    public_context={"code": CODE_DUPLICATE},
                ) from exc
            session.refresh(approval)

            audit.record(
                AUDIT_ACTION_GRANT,
                admin=actor,
                target_type=_AUDIT_TARGET_TYPE,
                target_id=migration.id,
                touched_engine=False,
                detail=self._audit_detail(approval),
            )
            return self._serialize_approval(approval)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def approve_batch(
        self,
        model_id: int,
        items: list[dict],
        *,
        reason: str,
        actor,
        managed_database_id: int | None = None,
    ) -> dict:
        """
        Aprueba varias versiones de un mismo blueprint, con resultado PARCIAL por ítem.

        Cada ítem pasa por ``approve`` (mismo hash, mismos cuatro ojos, misma auditoría) en su
        propia transacción: una versión con el SQL cambiado o sin ``down_sql`` no frena las demás,
        y cada una queda con su código de error. No existe atajo del lote: lo que ``approve``
        rechaza, el lote lo rechaza igual.

        Lo que no depende del ítem (máquina, autoridad sobre todos los entornos protegidos en el
        alcance blueprint) se corta ANTES de aprobar nada, para no devolver N veces el mismo 403.
        """
        self._assert_not_machine(actor)
        if managed_database_id is None:
            session = self._session()
            try:
                self._assert_authority_over_every_protected_environment(session, actor)
            finally:
                session.close()

        results: list[dict] = []
        for item in items:
            results.append(
                self._approve_batch_item(
                    model_id,
                    item,
                    reason=reason,
                    actor=actor,
                    managed_database_id=managed_database_id,
                )
            )

        approved_count = sum(1 for r in results if r["status"] == BATCH_ITEM_STATUS_APPROVED)
        return {
            "total": len(results),
            "approved_count": approved_count,
            "failed_count": len(results) - approved_count,
            "results": results,
        }

    def _approve_batch_item(
        self,
        model_id: int,
        item: dict,
        *,
        reason: str,
        actor,
        managed_database_id: int | None,
    ) -> dict:
        version = item["version"]
        direction = item["direction"]
        try:
            approval = self.approve(
                model_id,
                version,
                direction=direction,
                expected_sql_hash=item["expected_sql_hash"],
                reason=reason,
                actor=actor,
                managed_database_id=managed_database_id,
            )
        except AppHttpException as exc:
            public_context = exc.public_context or {}
            return {
                "version": version,
                "direction": direction,
                "status": BATCH_ITEM_STATUS_FAILED,
                "approval": None,
                "error": {
                    "code": public_context.get("code", CODE_BATCH_ITEM_FAILED),
                    "message": exc.message,
                    "status_code": exc.status_code,
                    "rule": public_context.get("rule"),
                },
            }
        except Exception:
            # El detalle del motor o de la BD del gateway va al log, no al cliente.
            logger.exception(
                "Aprobación destructiva en lote: error inesperado en la versión %s (%s)",
                version,
                direction,
            )
            return {
                "version": version,
                "direction": direction,
                "status": BATCH_ITEM_STATUS_FAILED,
                "approval": None,
                "error": {
                    "code": CODE_BATCH_ITEM_FAILED,
                    "message": "Error inesperado al aprobar esta versión.",
                    "status_code": 500,
                    "rule": None,
                },
            }
        return {
            "version": version,
            "direction": direction,
            "status": BATCH_ITEM_STATUS_APPROVED,
            "approval": approval,
            "error": None,
        }

    @staticmethod
    def _assert_four_eyes(
        approver_person_id: int | None,
        creator_person_id: int | None,
        editor_person_id: int | None,
    ) -> None:
        """
        El aprobador tiene que ser OTRA persona que el creador y que el último editor.

        Un creador o editor desconocido (``None``: versión anterior a la trazabilidad, o escrita
        por un token sin emisor) NO bloquea —no hay a quién compararlo— y deja la aprobación
        marcada ``author_unknown``. Un aprobador sin id de persona no puede llegar acá (las
        máquinas se rechazan antes).
        """
        if approver_person_id is None:
            return
        violated_rule: str | None = None
        if approver_person_id == creator_person_id:
            violated_rule = SELF_APPROVAL_RULE_CREATOR
        elif approver_person_id == editor_person_id:
            violated_rule = SELF_APPROVAL_RULE_LAST_EDITOR
        if violated_rule is None:
            return
        raise AppHttpException(
            message=(
                "No puede aprobar una migración que usted mismo creó o editó por última vez: "
                "tiene que aprobarla otra persona."
            ),
            status_code=409,
            public_context={"code": CODE_SELF_APPROVAL, "rule": violated_rule},
        )

    # ------------------------------------------------------------------ #
    # Revocar                                                             #
    # ------------------------------------------------------------------ #
    def revoke(self, approval_id: int, *, reason: str, actor) -> dict:
        """
        Revoca una aprobación. Idempotente: revocar una ya revocada (o reemplazada) devuelve la
        fila TAL CUAL —mismo revocador, misma fecha— y no es un error.

        La autorización (quien aprobó, o ``approve_destructive`` en el alcance de la aprobación) ya
        la resolvió el guard de la ruta; acá solo se escribe. El ``UPDATE`` condicional (``revoked_at IS NULL``)
        hace que dos revocaciones simultáneas tengan UN ganador sin leer-y-escribir.
        """
        session = self._session()
        try:
            approval = session.get(MigrationDestructiveApproval, approval_id)
            if approval is None:
                raise self._not_found("Aprobación no encontrada.", approval_id=approval_id)

            revoker_id, revoker_username = identity_of(actor)
            revoke_statement = (
                update(MigrationDestructiveApproval)
                .where(
                    MigrationDestructiveApproval.id == approval_id,
                    MigrationDestructiveApproval.revoked_at.is_(None),
                )
                .values(
                    live_key=None,
                    revoked_at=_utcnow(),
                    revoked_by_admin_id=revoker_id,
                    revoked_by_username=revoker_username,
                    revoke_reason=reason,
                )
            )
            was_revoked_now = session.execute(revoke_statement).rowcount == 1
            session.commit()
            session.expire_all()

            approval = session.get(MigrationDestructiveApproval, approval_id)
            if was_revoked_now:
                audit.record(
                    AUDIT_ACTION_REVOKE,
                    admin=actor,
                    target_type=_AUDIT_TARGET_TYPE,
                    target_id=approval.model_migration_id,
                    touched_engine=False,
                    detail=self._audit_detail(approval, revoker=revoker_id),
                )
            return self._serialize_approval(approval)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    # Listar                                                              #
    # ------------------------------------------------------------------ #
    def list_versions(self, model_id: int, *, managed_database_id: int | None = None) -> list[dict]:
        """
        Las versiones destructivas del blueprint, por dirección, con su estado de aprobación.

        Con ``managed_database_id`` el estado se evalúa para ESA base (cuentan las aprobaciones de
        su alcance y las del blueprint); sin ella, solo las de alcance blueprint. "Destructiva" se
        decide sobre TODOS los motores.

        El veredicto sale de ``model_migration_destructive_verdicts`` cuando la fila existe y su
        huella coincide con la de HOY (ver ``destructive_verdicts``); si no, se analiza en vivo
        (un parseo por versión y motor) y se guarda para la próxima lectura. Una fila vieja o
        ausente solo cuesta tiempo, nunca da un veredicto desactualizado.
        """
        session = self._session()
        try:
            if session.get(DatabaseModel, model_id) is None:
                raise self._not_found("Blueprint no encontrado.", model_id=model_id)
            if managed_database_id is not None:
                self._assert_database_belongs_to_model(session, managed_database_id, model_id)

            specs = ManagedMigrationController._load_specs(session, model_id)
            specs.sort(key=lambda spec: version_sort_key(spec.version))
            authorship_by_migration_id = {
                row.id: row
                for row in session.query(
                    ModelMigration.id,
                    ModelMigration.name,
                    ModelMigration.created_by_admin_id,
                    ModelMigration.created_by_username,
                    ModelMigration.created_by_actor_type,
                    ModelMigration.last_sql_editor_admin_id,
                    ModelMigration.last_sql_editor_username,
                )
                .filter(ModelMigration.model_id == model_id)
                .all()
            }
            migration_ids = [spec.id for spec in specs]
            approvals_by_direction = {
                direction: load_approval_rows(session, migration_ids, direction)
                for direction in DESTRUCTIVE_APPROVAL_DIRECTIONS
            }

            stored_verdict_rows = load_verdict_rows(session, migration_ids)
            analyzed_live: list[tuple[MigrationSpec, str, dict[str, tuple[str, ...]]]] = []

            items: list[dict] = []
            for spec in specs:
                authorship = authorship_by_migration_id[spec.id]
                creator_person_id = author_person_id(
                    authorship.created_by_admin_id, authorship.created_by_actor_type
                )
                editor_person_id = authorship.last_sql_editor_admin_id
                for direction in DESTRUCTIVE_APPROVAL_DIRECTIONS:
                    if direction == DIRECTION_DOWN and not spec.down_sql:
                        continue
                    stored_reasons = stored_reasons_if_current(
                        stored_verdict_rows, spec, direction
                    )
                    if stored_reasons is not None:
                        reasons_by_engine = stored_reasons
                    else:
                        reasons_by_engine = self._reasons_by_engine(spec, direction)
                        analyzed_live.append((spec, direction, reasons_by_engine))
                    if not reasons_by_engine:
                        continue
                    approvals = approvals_by_direction[direction][spec.id]
                    current_hash = approval_hash(spec, direction)
                    status, satisfying_approval = classify_approval(
                        approvals, current_hash, managed_database_id
                    )
                    items.append(
                        {
                            "migration_id": spec.id,
                            "version": spec.version,
                            "name": authorship.name,
                            "direction": direction,
                            "sql_hash": current_hash,
                            "reasons_by_engine": {
                                engine: list(reasons)
                                for engine, reasons in reasons_by_engine.items()
                            },
                            "author": _actor_ref(creator_person_id, authorship.created_by_username),
                            "last_sql_editor": _actor_ref(
                                editor_person_id, authorship.last_sql_editor_username
                            ),
                            "author_unknown": creator_person_id is None or editor_person_id is None,
                            "approval_status": status,
                            "effective_approval_id": (
                                satisfying_approval.id
                                if status == APPROVAL_STATUS_APPROVED and satisfying_approval
                                else None
                            ),
                            "approvals": [
                                self._serialize_approval(approval)
                                for approval in sorted(
                                    approvals, key=lambda approval: approval.id, reverse=True
                                )
                            ],
                        }
                    )
            self._persist_live_verdicts(session, model_id, analyzed_live)
            return items
        finally:
            session.close()

    @staticmethod
    def _persist_live_verdicts(
        session,
        model_id: int,
        analyzed_live: list[tuple[MigrationSpec, str, dict[str, tuple[str, ...]]]],
    ) -> None:
        """
        Guarda lo que el listado tuvo que analizar en vivo, para que la próxima lectura no lo
        repita. Es una optimización: si falla (carrera, BD de solo lectura), se registra y la
        respuesta ya calculada sale igual. Un GET nunca puede fallar por no poder cachear.
        """
        if not analyzed_live:
            return
        try:
            for spec, direction, reasons_by_engine in analyzed_live:
                store_verdict(session, spec, direction, reasons_by_engine)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception(
                "No se pudieron guardar los veredictos destructivos del blueprint %s", model_id
            )
