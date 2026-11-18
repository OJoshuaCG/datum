"""
Controller de aplicación de migraciones sobre BDs gestionadas (TOCA el motor).

Orquesta el ``MigrationRunner`` (Alembic embebido) y el inventario del gateway:
- ``status``   : versión actual de la BD (leída de ``_gw_v_{slug}``) vs. pendientes.
- ``apply``    : aplica pendientes; registra ``database_migration_history`` y
                 actualiza ``managed_database.model_version``.
- ``rollback`` : revierte la última (409 si la versión actual no tiene ``down_sql``).
- ``stamp``    : marca versión sin ejecutar (BDs pre-existentes).
- ``apply_all``: aplica a TODAS las BDs del blueprint (síncrono, acotado; el job
                 asíncrono real es del Plan 06).

Integridad: antes de tocar el motor se re-valida el ``checksum`` de cada migración
(detecta alteración directa en la BD del gateway).
"""

import hashlib
import json
import re
from collections.abc import Callable, Collection
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NamedTuple

from sqlalchemy import or_ as sa_or
from sqlalchemy import update as sa_update

from app.controllers.blueprint_channel_controller import (
    reject_channel_with_target_version,
    resolve_channel_version,
)
from app.controllers.common import build_target, engine_value, get_server_or_404
from app.core.database import Database
from app.core.environments import (
    DB_HOST,
    DB_NAME,
    DB_PASS,
    DB_PORT,
    DB_USER,
    MIGRATION_CAPTURE_ENABLED,
)
from app.core.logger import get_logger
from app.core.remote_engine import UNKNOWN_DATABASE_CODES
from app.core.scope import assert_at_with_code, can_at, partition_for_batch
from app.core.scope_targets import database as database_target
from app.core.scope_targets import points_for_databases
from app.exceptions import AppHttpException
from app.models.database_migration_history import DatabaseMigrationHistory
from app.models.database_model import DatabaseModel
from app.models.environment import Environment
from app.models.enums import EngineType, MigrationStatus, ProvisionStatus
from app.models.managed_database import ManagedDatabase
from app.models.migration_destructive_approval import MigrationDestructiveApproval
from app.models.model_migration import ModelMigration
from app.models.model_migration_statement import ModelMigrationStatement
from app.core.actor import Actor, actor_type_of, identity_of
from app.core.context import current_http_identifier
from app.services import audit
from app.services.channel_pointer_sync import sync_automatic_pointers_safely
from app.services import engine_error_catalog
from app.services import environment_catalog as ecodes
from app.services import migration_capture_catalog as ccodes
from app.services import provisioning_catalog as pcodes
from app.services.capability_catalog import CODE_FORBIDDEN, Capability
from app.services.db_admin import migration_facts, migration_progress, migration_results
from app.services.db_admin.factory import get_adapter
from app.services.db_admin.migration_integrity import compute_checksum, version_sort_key
from app.services.db_admin.migrations import (
    ManifestStatement,
    MigrationResult,
    MigrationRunner,
    MigrationSpec,
)
from app.services.db_admin.identifiers import references_gateway_internal_table
from app.services.db_admin.sql_dialect import SqlTranslator, split_sql_statements
from app.services.confirm_token import issue as issue_confirm_token
from app.services.destructive_approval_catalog import (
    AUDIT_ACTION_CONSUME,
    CODE_BATCH_MULTI_ENVIRONMENT,
    CODE_CONFIRM_PHRASE_MISMATCH,
    CODE_CONFIRMATION_REQUIRED,
    CODE_EXECUTOR_FORBIDDEN,
    DESTRUCTIVE_CONFIRM_TTL_SECONDS,
    DIRECTION_DOWN,
    DIRECTION_UP,
    OPERATION_APPLY_ALL_DESTRUCTIVE,
    OPERATION_APPLY_DESTRUCTIVE,
    OPERATION_ROLLBACK_DESTRUCTIVE,
)
from app.services.destructive_approval_policy import (
    TARGET_LABEL_BASE,
    TARGET_LABEL_HEAD,
    Resolution,
    batch_subject,
    db_subject,
    destructive_down_versions,
    destructive_reasons,
    load_approvals_by_id,
    resolve,
    wrap_verify,
)


logger = get_logger(__name__)


class _VersionRow(NamedTuple):
    """``(id, version)`` de una migración: lo único que ``status`` necesita de cada una."""

    id: int
    version: str


@dataclass
class _BatchPreflight:
    """
    Resultado del preflight destructivo de ``apply_all``.

    ``confirmed`` son las entradas ``(db_id, migration_id, sql_hash, approval_id)`` que respaldaron
    el token verificado (``None`` = el lote no exige confirmación, o el actor es una máquina y cada
    ítem fallará por su cuenta). ``confirmation`` es lo que se informa al cliente.
    """

    confirmed: frozenset | None = None
    confirmation: dict | None = None


@dataclass(frozen=True)
class _ApplyAllPlan:
    """
    Lote de ``apply_all`` ya validado: lo que ``_prepare_apply_all`` resolvió en el request.

    Vive solo en memoria (incluye el closure memoizado ``destructive_by_engine``). El job en
    background lo recibe tal cual en su worker; por eso no es durable: un reinicio deja el job
    ``interrupted`` en vez de reconstruir el plan.
    """

    model_id: int
    slug: str
    specs: list
    total: int
    matched: int
    dbs: list[tuple]
    omitted: list[int]
    env_policies: dict
    targets: dict[int, tuple]
    environment_id: int | None
    target_version: str | None
    resolved_channel: dict | None
    dry_run: bool
    force: bool
    on_failure: str
    destructive_by_engine: Callable
    up_to_target_specs: list
    preflight: _BatchPreflight


class _ApplyAllItemOutcome(NamedTuple):
    """Ítem de una base de ``apply_all`` y si un guard de entorno o confirmación la denegó."""

    item: dict
    denied: bool


class ManagedMigrationController:
    def __init__(self):
        self.db = Database(DB_NAME, DB_USER, DB_PASS, DB_HOST, DB_PORT)
        self.runner = MigrationRunner()

    def _session(self):
        return self.db.get_declarative_base_session()

    # ------------------------------------------------------------------ #
    # Carga de contexto                                                   #
    # ------------------------------------------------------------------ #
    def _load_context(self, session, db_id: int):
        """Devuelve (managed_db, server, model) validando blueprint asignado."""
        md = session.get(ManagedDatabase, db_id)
        if not md:
            raise AppHttpException(
                message="Base de datos gestionada no encontrada.",
                status_code=404,
                context={"managed_database_id": db_id},
            )
        if md.model_id is None:
            raise AppHttpException(
                message="La BD no tiene un blueprint asignado; nada que migrar.",
                status_code=422,
                context={"managed_database_id": db_id},
            )
        server = get_server_or_404(session, md.server_id)
        model = session.get(DatabaseModel, md.model_id)
        if model is None:
            raise AppHttpException(
                message="El blueprint asignado a la BD ya no existe.",
                status_code=409,
                context={"managed_database_id": db_id, "model_id": md.model_id},
            )
        return md, server, model

    # ------------------------------------------------------------------ #
    # La BD puede NO existir en el motor                                  #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_unknown_database(exc: AppHttpException) -> bool:
        """
        ¿El 404 que subió el runner significa "la base no existe" (1049 / 3D000)?

        Mirar solo el status NO alcanza: el errno 1008 ("can't drop database") también mapea a
        404. Y esto es lo que va a disparar un CREATE DATABASE desde la UI, así que el conjunto
        de códigos tiene que ser cerrado. El código nativo viaja como STRING en el ``context``
        de la excepción; ese atributo es accesible in-process aunque el handler solo lo publique
        en ``development``.
        """
        ctx = getattr(exc, "context", None)
        return (
            getattr(exc, "status_code", None) == 404
            and isinstance(ctx, dict)
            and str(ctx.get("remote_error_code") or "") in UNKNOWN_DATABASE_CODES
        )

    def _read_current_version_tolerant(
        self, target, db_name: str, slug: str
    ) -> tuple[str | None, bool]:
        """
        ``(current_version, database_exists)`` — no explota si la BD no existe en el motor.

        Cuesta CERO en el camino feliz: ``list_databases()`` solo se paga cuando el driver ya
        falló. La re-confirmación no es paranoia: un 1049 con la BD PRESENTE tiene otra causa
        (privilegios del pseudo-root sobre esa base, carrera con un drop) y ahí el 404 original
        tiene que propagarse tal cual — un "no existe" falso mandaría al operador a crear una
        base que sí está.
        """
        try:
            return self.runner.get_current_version(target, db_name, slug), True
        except AppHttpException as exc:
            if not self._is_unknown_database(exc):
                raise
            if db_name in get_adapter(target).list_databases():
                raise
            return None, False

    @contextmanager
    def _translating_unknown_database(
        self, target, db_id: int, db_name: str, server_id: int, *, op: str
    ):
        """
        Convierte el 404 opaco del driver (1049/3D000) en un 409 ACCIONABLE.

        Se usa donde el propio runner ya abre conexión a la BD destino (``stamp``,
        ``reconcile_partial``): ahí un ``list_databases()`` preventivo sería un round-trip de
        más y una superficie de fallo nueva, sin comprar nada — el runner ya falla solo si la
        base no está. Lo que faltaba no era detectarlo, era **decirlo**: el 404 genérico ("El
        recurso solicitado no existe en el servidor destino") es indistinguible del 404 de "BD
        no encontrada en el inventario" y no nombra la salida.

        La confirmación con ``list_databases()`` se paga SOLO en el camino de error, y sirve
        para no afirmar "no existe" cuando el 1049 viene de otra causa (privilegios del
        pseudo-root sobre esa base, carrera con un drop): ahí el 404 original se propaga.

        **El estado ``pending`` de la fila NO se usa como atajo, y es deliberado.** Está rancio
        en las DOS direcciones: una BD creada con ``POST /servers/{id}/databases?register=false``
        EXISTE con la fila en ``pending``, y una fila ``active`` puede apuntar a una base que
        alguien dropeó. Rechazar por ``pending`` dejaría a la primera sin salida (``provision``
        le daría 409 por existir). El plano físico es la única fuente de verdad.
        """
        try:
            yield
        except AppHttpException as exc:
            if not self._is_unknown_database(exc):
                raise
            if db_name in get_adapter(target).list_databases():
                raise
            raise self._not_provisioned(db_id, db_name, server_id, op=op) from exc

    @staticmethod
    def _not_provisioned(
        db_id: int, db_name: str, server_id: int, *, op: str
    ) -> AppHttpException:
        return AppHttpException(
            message=(
                f"La base de datos '{db_name}' no existe en el motor: nunca se aprovisionó "
                f"(o se borró por fuera del gateway). No hay nada sobre lo que {op}. "
                f"Aprovisionala con POST /managed-databases/{db_id}/provision y reintentá."
            ),
            status_code=409,
            public_context={"code": pcodes.CODE_NOT_PROVISIONED, "database": db_name},
            context={"managed_database_id": db_id, "server_id": server_id},
        )

    @staticmethod
    def _load_versions(session, model_id: int) -> list[_VersionRow]:
        """
        Solo ``(id, version)`` de las versiones del blueprint, en orden numérico.

        ``status`` únicamente necesita saber QUÉ versiones existen para calcular las
        pendientes. ``_load_specs`` trae además cinco columnas ``LONGTEXT`` de SQL y el
        manifiesto de sentencias, y ``status`` es de las lecturas que más se piden.
        """
        rows = (
            session.query(ModelMigration.id, ModelMigration.version)
            .filter(ModelMigration.model_id == model_id)
            .all()
        )
        version_rows = [_VersionRow(id=row.id, version=row.version) for row in rows]
        version_rows.sort(key=lambda version_row: version_sort_key(version_row.version))
        return version_rows

    @staticmethod
    def _load_specs(
        session,
        model_id: int,
        *,
        only_version: str | None = None,
        only_ids: Collection[int] | None = None,
    ) -> list[MigrationSpec]:
        """
        Las versiones del blueprint como datos planos. ``only_version`` carga UNA sola: lo usa
        el controller de aprobaciones destructivas, que no necesita (ni debe pagar el SQL de)
        todas las versiones de un blueprint para aprobar una. ``only_ids`` carga solo esas
        filas: lo usa ``status`` para las aplicaciones parciales.
        """
        rows_query = session.query(ModelMigration).filter(ModelMigration.model_id == model_id)
        if only_version is not None:
            rows_query = rows_query.filter(ModelMigration.version == only_version)
        if only_ids is not None:
            rows_query = rows_query.filter(ModelMigration.id.in_(list(only_ids)))
        rows = rows_query.all()
        # MANIFIESTO de sentencias por migración (una sola consulta para todas). Es
        # OPCIONAL: las migraciones escritas a mano no lo tienen y siguen funcionando por
        # el camino del splitter.
        manifests: dict[int, list[ModelMigrationStatement]] = {}
        if rows:
            for st in (
                session.query(ModelMigrationStatement)
                .filter(
                    ModelMigrationStatement.model_migration_id.in_([r.id for r in rows])
                )
                .order_by(ModelMigrationStatement.seq.asc())
                .all()
            ):
                manifests.setdefault(st.model_migration_id, []).append(st)
        specs = [
            MigrationSpec(
                id=r.id,
                version=r.version,
                name=r.name,
                up_sql=r.up_sql,
                up_sql_mysql=r.up_sql_mysql,
                up_sql_postgresql=r.up_sql_postgresql,
                down_sql=r.down_sql,
                checksum=r.checksum,
                kind=r.kind,
                has_non_portable=r.has_non_portable,
                source_engine=r.source_engine,
                capture_selects=r.capture_selects,
                manifest=tuple(
                    ManifestStatement(
                        seq=st.seq,
                        up_sql=st.up_sql,
                        down_sql=st.down_sql,
                        down_confirmed=st.down_confirmed,
                        object_type=st.object_type,
                        object_name=st.object_name,
                        destructive=st.destructive,
                    )
                    for st in manifests.get(r.id, [])
                ),
            )
            for r in rows
        ]
        # Orden NUMÉRICO de versión (no lexicográfico): status/latest dependen de él.
        specs.sort(key=lambda s: version_sort_key(s.version))
        return specs

    @staticmethod
    def _verify_integrity(specs: list[MigrationSpec]) -> None:
        for spec in specs:
            expected = compute_checksum(
                spec.up_sql, spec.up_sql_mysql, spec.up_sql_postgresql,
                spec.down_sql, spec.version,
            )
            if expected != spec.checksum:
                raise AppHttpException(
                    message=(
                        f"Integridad: la migración {spec.version} fue alterada "
                        "(checksum no coincide). Se aborta para no aplicar SQL no verificado."
                    ),
                    status_code=409,
                    context={"version": spec.version},
                )

    # ------------------------------------------------------------------ #
    # Estado                                                              #
    # ------------------------------------------------------------------ #
    def status(self, db_id: int) -> dict:
        session = self._session()
        try:
            md, server, model = self._load_context(session, db_id)
            version_rows = self._load_versions(session, model.id)
            slug = model.slug
            db_name, model_id = md.name, model.id
            cached_version = md.model_version
            engine = EngineType(engine_value(server))
            target = build_target(server)
        finally:
            session.close()

        # ``status`` es una LECTURA cuyo trabajo entero es describir la realidad, así que "la
        # BD no existe en el motor" es un estado que describir, no un fallo de la petición: se
        # informa con ``database_exists`` y se devuelve 200. Un 409 acá tiraría justo lo que el
        # operador necesita para decidir (pendientes, slug, blueprint — todo computable sin el
        # motor) y dejaría a la SPA pintando un ErrorState sin lugar donde poner el CTA.
        current, database_exists = self._read_current_version_tolerant(target, db_name, slug)
        latest = version_rows[-1].version if version_rows else None
        # ``compute_pending`` solo lee ``.version``: la regla de orden/corte sigue siendo la
        # única del runner, sin pagar el SQL de cada versión para saber cuáles faltan.
        pending = self.runner.compute_pending(current, version_rows)
        # Aplicación PARCIAL pendiente: ``current_version`` NO la refleja (Alembic no
        # alcanzó a registrarla), así que sin este campo el estado se lee como sano y el
        # admin descubre el problema recién cuando el rollback se niega.
        incomplete = migration_progress.incomplete_progress_for_database(db_id, direction="up")
        # Contabilidad HUÉRFANA. ``current is None`` es ambiguo: puede ser "la base está en
        # cero" o "la tabla de versión existe con OTRO nombre y el gateway no la ve". La
        # segunda no falla, no avisa, y hace que la cadena entera figure pendiente — es
        # exactamente como un renombrado de slug terminó reaplicando desde la 0001 sobre
        # bases que ya tenían el esquema.
        #
        # La sonda se dispara SOLO ante la firma del problema: el motor no reporta versión
        # PERO el inventario tenía una registrada. Una BD genuinamente nueva tiene las dos en
        # ``None`` y no paga ni una consulta de más, que es el caso común.
        orphan_tables: list[str] = []
        if current is None and database_exists and cached_version is not None:
            try:
                from app.services.db_admin.factory import get_adapter
                from app.services.db_admin.identifiers import GATEWAY_TABLE_PREFIXES

                prefijo = GATEWAY_TABLE_PREFIXES[0]
                orphan_tables = [
                    n
                    for n in get_adapter(target).list_internal_tables(db_name)
                    if n.startswith(prefijo)
                ]
            except AppHttpException:
                # La sonda es un EXTRA de diagnóstico: que falle no puede tumbar el estado,
                # que es la llamada con la que el operador decide qué hacer.
                orphan_tables = []

        # El SQL completo solo hace falta para planear la reconciliación de las filas que
        # quedaron a medias, que es el caso raro.
        partial_ids = {row["model_migration_id"] for row in incomplete}
        by_id: dict[int, MigrationSpec] = {}
        if partial_ids:
            partial_session = self._session()
            try:
                by_id = {
                    spec.id: spec
                    for spec in self._load_specs(partial_session, model_id, only_ids=partial_ids)
                }
            finally:
                partial_session.close()
        return {
            "managed_database_id": db_id,
            "model_id": model_id,
            "slug": slug,
            "database_exists": database_exists,
            "current_version": current,
            "latest_available": latest,
            "pending_count": len(pending),
            "pending_versions": [s.version for s in pending],
            "cached_version": cached_version,
            "orphan_version_tables": orphan_tables,
            "has_orphan_accounting": bool(orphan_tables),
            "has_partial_application": bool(incomplete),
            "partial_application": [
                self._partial_entry(by_id.get(row["model_migration_id"]), engine, row)
                for row in incomplete
            ],
        }

    @classmethod
    def _partial_entry(cls, spec: MigrationSpec | None, engine: EngineType, row: dict) -> dict:
        """
        Una entrada de ``partial_application``, ya resuelta con si se puede reconciliar.

        El frontend necesita saberlo ANTES de ofrecer el botón: sin manifiesto de
        sentencias la reconciliación automática no es posible y la salida es
        ``stamp?force=true`` tras reconciliar a mano.

        Los DOS flags no son redundantes y son excluyentes entre sí (ver
        ``_reconcile_plan``). Con ``reconcilable_with_force`` la SPA tiene que ofrecer el
        botón igual: el endpoint acepta esa reconciliación con ``force=true``. Mirar solo
        ``reconcilable`` escondía la única salida automática que había.
        """
        plan = cls._reconcile_plan(spec, engine, row) if spec is not None else None
        return {
            "version": spec.version if spec is not None else None,
            "model_migration_id": row["model_migration_id"],
            "applied_statements": row["last_statement_index"],
            "total_statements": row["total_statements"],
            "reconcilable": bool(plan and plan["reconcilable"]),
            "reconcilable_with_force": bool(plan and plan["reconcilable_with_force"]),
            "reason": (
                None
                if plan and plan["reconcilable"]
                else (
                    plan["reason"] if plan
                    else "la migración ya no existe en el blueprint"
                )
            ),
            "statements_to_undo": plan["count"] if plan else 0,
        }

    # ------------------------------------------------------------------ #
    # Guard de entorno: DDL destructivo                                   #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _env_policy_for(session, db_ids: list[int]) -> dict[int, tuple[bool, str]]:
        """
        Política de entorno por BD: ``{db_id: (bloquea_destructivas, slug)}``.

        Se resuelve UNA vez por lote y devuelve **valores planos, no filas ORM**: en
        ``apply_all`` la sesión se cierra antes del bucle, así que instancias del ORM quedarían
        desligadas y cualquier acceso a un atributo explotaría. Mismo criterio que el cacheo de
        ``ServerTarget`` por servidor, y que el desempaquetado temprano de ``md.name``/
        ``md.server_id`` que ya hace ``apply``.

        Las BDs sin entorno NO aparecen en el dict. Eso las deja PERMISIVAS, y es deliberado:
        romper todos los ``apply-all`` que hoy funcionan no es aceptable, y toda BD existente
        nació antes de que existiera esta columna.

        ATENCIÓN — esta asimetría no se traslada. Si algún día se agrega el gate de consultas
        para agentes (plan 11 §6), ahí un entorno sin asignar debe NEGAR, porque no hay
        comportamiento previo que romper y un default permisivo dejaría toda BD sin clasificar
        consultable. No "unifiques" los dos comportamientos.
        """
        if not db_ids:
            return {}
        rows = (
            session.query(
                ManagedDatabase.id,
                Environment.blocks_destructive_migrations,
                Environment.slug,
            )
            .join(Environment, Environment.id == ManagedDatabase.environment_id)
            .filter(ManagedDatabase.id.in_(db_ids))
            .all()
        )
        return {db_id: (bool(blocks), slug) for db_id, blocks, slug in rows}

    def _destructive_versions(
        self, specs: list[MigrationSpec], engine: EngineType
    ) -> dict[str, tuple[str, ...]]:
        """
        Qué versiones son destructivas y por qué, para ESTE motor.

        Dos decisiones acá, y las dos son la corrección de un fail-open real:

        1. **NO se lee el manifiesto como fuente primaria.** ``ModelMigrationStatement`` tiene
           una columna ``destructive`` que parecería la fuente natural, pero esas filas CASI
           NUNCA EXISTEN: el manifiesto es opcional y lo escribe únicamente la adopción de un
           diff estructural (``_write_statement_manifest`` corta con
           ``if not statements or not source_engine: return``, y ``ModelMigrationCreate`` no
           tiene ni campo ``statements``). O sea: una migración escrita a mano con
           ``up_sql = "DROP TABLE clientes"`` no produce ni una fila, y un guard basado en el
           manifiesto la dejaría pasar. La fuente es ``migration_facts.analyze``, que decide
           por AST y es lo mismo que el listado ya publica como insignia ``destructive``.
        2. **Se analiza el SQL RESUELTO PARA EL MOTOR**, vía ``select_up_sql``, y no
           ``spec.up_sql``. Una migración puede traer override por motor
           (``up_sql_mysql`` / ``up_sql_postgresql``), y el ``DROP`` puede vivir SOLO en el
           override: mirando ``up_sql`` sería invisible justo en el motor donde se ejecuta.

        El manifiesto se usa igual, en OR: donde existe, agrega información y nunca la quita
        (fail-closed). ``analyze`` está memoizado, así que el costo real es una vez por SQL.

        3. **La CONVERSIÓN de charset/collation cuenta como destructiva**, aunque el AST no la
           vea. ``sqlglot`` degrada ``ALTER TABLE … CONVERT TO CHARACTER SET`` y
           ``ALTER DATABASE … CHARACTER SET`` a ``exp.Command`` (opaco), así que
           ``_is_destructive`` devuelve False y sin esto pasaban a producción sin gate — pese
           a re-codificar FÍSICAMENTE cada valor de texto de cada tabla, con pérdida de
           caracteres si el charset destino es más angosto y hasta cambios de TIPO de columna
           (``TEXT``→``MEDIUMTEXT``). El repo ya lo clasificaba así en el otro camino
           (``schema_diff.py``: "re-encoding físico: destructivo", con ``data_conversion``);
           los dos clasificadores se contradecían y el permisivo era justo el que gobierna
           este guard.

           El detector es de FORMA, no de mención (``migration_facts._CHARSET_CONVERSION_RE``):
           usar ``forced_charsets`` habría marcado el ``DEFAULT CHARSET=utf8mb4`` de
           prácticamente todo ``CREATE TABLE`` de MySQL y habría bloqueado media base
           instalada de golpe.
        """
        out: dict[str, tuple[str, ...]] = {}
        for spec in specs:
            sql = self.runner.select_up_sql(spec, engine)
            facts = migration_facts.analyze(sql, spec.kind, spec.has_non_portable)
            reasons = destructive_reasons(facts)
            if any(st.destructive for st in spec.manifest):
                reasons.append("manifiesto")
            if reasons:
                out[spec.version] = tuple(reasons)
        return out

    # ------------------------------------------------------------------ #
    # Guard de entorno + aprobaciones de migraciones destructivas          #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_human_actor(actor) -> bool:
        """
        Solo una PERSONA con sesión consume aprobaciones. Un token de agente, uno de integración,
        un ``dict`` legado o ``None`` conservan el bloqueo duro de siempre: la aprobación es el
        visto bueno de una segunda persona para que OTRA persona ejecute, y una máquina que la
        consumiera dejaría el control en manos de quien tenga el token.
        """
        return isinstance(actor, Actor) and not actor.is_machine

    @staticmethod
    def _destructive_blocked(
        db_id: int,
        *,
        direction: str,
        env_slug: str,
        hits: list[MigrationSpec],
        destructive: dict[str, tuple[str, ...]],
        resolution: Resolution | None = None,
    ) -> AppHttpException:
        """
        El 409 ``environment.destructive_blocked``. ``blocked_versions`` son las que NO se pueden
        ejecutar: sin aprobación vigente (sin aprobar, revocada o con el SQL cambiado). Sin
        ``resolution`` (actor máquina) son todas las destructivas, como antes de las aprobaciones.

        ``approval_status`` por versión le dice a la pantalla si falta aprobar, si se revocó o si
        hay que re-aprobar porque se editó el SQL, sin que tenga que pedir el listado aparte.
        """
        all_versions = [spec.version for spec in hits]
        blocked_versions = list(resolution.unapproved) if resolution is not None else all_versions
        approved_versions = (
            [entry.version for entry in resolution.approved] if resolution is not None else []
        )
        approval_status_by_version = (
            {version: status.status for version, status in resolution.statuses.items()}
            if resolution is not None
            else {}
        )
        action = "revertir" if direction == DIRECTION_DOWN else "aplicar"
        return AppHttpException(
            message=(
                f"El entorno '{env_slug}' bloquea las migraciones destructivas y las versiones "
                f"{', '.join(blocked_versions)} contienen sentencias que pueden perder datos "
                "(DROP / TRUNCATE / DELETE sin WHERE / ALTER DROP COLUMN). "
                f"Para {action} hace falta una aprobación vigente de otra persona. "
                "'force' no habilita esta operación."
            ),
            status_code=409,
            public_context={
                "code": ecodes.CODE_DESTRUCTIVE_BLOCKED,
                "environment_slug": env_slug,
                "blocked_versions": blocked_versions,
                "direction": direction,
                "approved_versions": approved_versions,
                "approval_status": approval_status_by_version,
            },
            context={
                "managed_database_id": db_id,
                "reasons": {v: destructive.get(v, ()) for v in blocked_versions},
            },
        )

    @staticmethod
    def _utcnow() -> datetime:
        """UTC naive: así guarda sus fechas todo el esquema de metadatos del gateway."""
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def _resolved_sql_hash(
        self, spec: MigrationSpec, engine: EngineType, direction: str
    ) -> str:
        """
        Hash del SQL YA resuelto para el motor. NO gobierna nada (el gate es el hash del texto
        fuente): se guarda en la auditoría para que un cambio de traducción entre la aprobación y
        la ejecución (p. ej. una actualización de sqlglot) quede visible como forense.
        """
        if direction == DIRECTION_UP:
            resolved_sql = self.runner.select_up_sql(spec, engine)
        else:
            resolved_sql = self.runner.select_down_sql(spec, engine)
        return hashlib.sha256((resolved_sql or "").encode("utf-8")).hexdigest()

    def _consume_audit_detail(
        self,
        session,
        db_id: int,
        *,
        direction: str,
        hits: list[MigrationSpec],
        resolution: Resolution,
        actor,
        engine: EngineType,
        env_slug: str,
        batch: bool,
    ) -> str:
        """
        Detalle de la auditoría del consumo (A-10): versiones, aprobaciones y su alcance, dirección,
        ejecutor, aprobador, autor, ``author_unknown`` y hashes. Sin SQL ni el motivo libre.
        """
        approvals_by_id = load_approvals_by_id(
            session, [entry.approval_id for entry in resolution.approved]
        )
        spec_by_version = {spec.version: spec for spec in hits}
        executor_id, _ = identity_of(actor)
        versions_detail = []
        for entry in resolution.approved:
            approval = approvals_by_id.get(entry.approval_id)
            versions_detail.append(
                {
                    "version": entry.version,
                    "migration_id": entry.migration_id,
                    "approval_id": entry.approval_id,
                    "scope": entry.scope,
                    "sql_hash": entry.sql_hash,
                    "engine_resolved_hash": self._resolved_sql_hash(
                        spec_by_version[entry.version], engine, direction
                    ),
                    "approver": approval.approved_by_admin_id if approval else None,
                    "author": approval.author_admin_id if approval else None,
                    "last_sql_editor": approval.editor_admin_id if approval else None,
                    "author_unknown": bool(approval.author_unknown) if approval else None,
                }
            )
        detail = {
            "direction": direction,
            "environment": env_slug,
            "managed_database_id": db_id,
            "batch": batch,
            "executor": executor_id,
            "versions": versions_detail,
        }
        return json.dumps(detail, sort_keys=True, ensure_ascii=False)

    def _consume_check(
        self,
        db_id: int,
        *,
        direction: str,
        hits: list[MigrationSpec],
        destructive: dict[str, tuple[str, ...]],
        env_slug: str,
        actor,
        engine: EngineType,
        operation: str,
        server_id: int,
        db_name: str,
        target_label: str,
        from_version: str | None,
        confirm_token: str | None,
        confirm_name: str | None,
        batch_confirmed: frozenset | None,
    ) -> Resolution | None:
        """
        Decide si las versiones destructivas ``hits`` de una base protegida pueden ejecutarse, y
        consume sus aprobaciones. Reemplaza al bloqueo duro: ahora se ejecutan SOLO si se cumplen
        TODAS estas condiciones, en este orden:

        1. el actor es una persona (una máquina conserva el bloqueo duro);
        2. cada versión tiene una aprobación vigente cuyo hash coincide, leída en una sesión
           FRESCA: es el re-chequeo de revocación que cierra el TOCTOU entre el plan y la
           ejecución, así que no se reutiliza ninguna sesión ni resolución anterior;
        3. el actor tiene ``blueprints.apply_destructive`` en la base (y step-up fresco);
        4. un ``confirm_token`` válido para este conjunto exacto de aprobaciones, o, dentro de
           ``apply_all``, que el lote ya lo verificó (``batch_confirmed``) y que ESTAS
           aprobaciones sean las mismas que respaldaron el token;
        5. la re-escritura del nombre de la base (``confirm_name``): el token da frescura, no
           intención, así que el servidor valida el re-tipeo (A-6).

        ``force`` no interviene en ninguna de las cinco. La auditoría del consumo es fail-closed:
        si no se puede persistir, no se ejecuta nada.
        """
        if not hits:
            return None

        if not self._is_human_actor(actor):
            raise self._destructive_blocked(
                db_id, direction=direction, env_slug=env_slug, hits=hits, destructive=destructive
            )

        session = self._session()
        try:
            resolution = resolve(session, db_id, direction, hits)
            if not resolution.is_fully_approved:
                raise self._destructive_blocked(
                    db_id,
                    direction=direction,
                    env_slug=env_slug,
                    hits=hits,
                    destructive=destructive,
                    resolution=resolution,
                )

            assert_at_with_code(
                actor,
                Capability.BLUEPRINTS_APPLY_DESTRUCTIVE,
                database_target(db_id),
                code=CODE_EXECUTOR_FORBIDDEN,
                message=(
                    "Ejecutar migraciones destructivas en un entorno protegido exige la "
                    "capacidad blueprints.apply_destructive sobre esta base."
                ),
            )

            if batch_confirmed is not None:
                needed_entries = {
                    (db_id, entry.migration_id, entry.sql_hash, entry.approval_id)
                    for entry in resolution.approved
                }
                if not needed_entries <= batch_confirmed:
                    raise self._confirmation_required(
                        "Las aprobaciones de esta base cambiaron desde que se confirmó el lote. "
                        "Solicite de nuevo el plan (dry-run)."
                    )
            else:
                if not confirm_token:
                    raise self._confirmation_required(
                        "Ejecutar migraciones destructivas en un entorno protegido exige el "
                        "'confirm_token' del plan (dry-run) y re-escribir el nombre de la base."
                    )
                subject = db_subject(
                    db_id, direction, target_label, from_version, resolution.approved
                )
                wrap_verify(confirm_token, operation, server_id, db_name, subject=subject)
                if confirm_name != db_name:
                    raise AppHttpException(
                        message="Para confirmar hay que re-escribir el nombre exacto de la base.",
                        status_code=422,
                        public_context={"code": CODE_CONFIRM_PHRASE_MISMATCH},
                    )

            audit.record_intent(
                AUDIT_ACTION_CONSUME,
                admin=actor,
                target_type="managed_database",
                target_id=db_id,
                server_id=server_id,
                detail=self._consume_audit_detail(
                    session,
                    db_id,
                    direction=direction,
                    hits=hits,
                    resolution=resolution,
                    actor=actor,
                    engine=engine,
                    env_slug=env_slug,
                    batch=batch_confirmed is not None,
                ),
            )
            return resolution
        finally:
            session.close()

    @staticmethod
    def _confirmation_required(message: str) -> AppHttpException:
        return AppHttpException(
            message=message,
            status_code=409,
            public_context={"code": CODE_CONFIRMATION_REQUIRED},
        )

    def _register_consumption(self, resolution: Resolution | None, actor) -> None:
        """
        Anota en cada aprobación usada quién la ejecutó y cuándo, DESPUÉS de que el motor devolvió.

        Se cuenta como consumida aunque la migración haya fallado a mitad: el SQL destructivo
        pudo haber corrido en parte y la aprobación se usó. Es un dato de seguimiento, no una
        barrera, así que si falla se registra en el log y NO se levanta: ya se ejecutó SQL sobre la
        base de un tercero y un 500 acá taparía el resultado real.
        """
        if resolution is None or not resolution.approved:
            return
        executor_id, executor_username = identity_of(actor)
        approval_ids = [entry.approval_id for entry in resolution.approved]
        session = self._session()
        try:
            session.execute(
                sa_update(MigrationDestructiveApproval)
                .where(MigrationDestructiveApproval.id.in_(approval_ids))
                .values(
                    last_executed_at=self._utcnow(),
                    last_executed_by_admin_id=executor_id,
                    last_executed_by_username=executor_username,
                    execution_count=MigrationDestructiveApproval.execution_count + 1,
                )
            )
            session.commit()
        except Exception:  # noqa: BLE001 - dato de seguimiento posterior a la ejecución
            session.rollback()
            logger.exception(
                "No se pudo registrar el consumo de las aprobaciones %s.", approval_ids
            )
        finally:
            session.close()

    def _build_destructive_plan(
        self,
        db_id: int,
        *,
        db_name: str,
        server_id: int,
        direction: str,
        hits: list[MigrationSpec],
        destructive: dict[str, tuple[str, ...]],
        actor,
        operation: str,
        target_label: str,
        from_version: str | None,
        issue_token: bool,
    ) -> tuple[dict | None, list[str]]:
        """
        ``(destructive_plan, blocked_by)`` de UNA base para el dry-run.

        El estado de aprobación por versión se informa a cualquier actor (es lectura). El token se
        emite solo si TODAS están aprobadas Y quien consulta es una persona que puede ejecutarlas
        (A-8): un token que su dueño no puede usar es una promesa falsa. ``blocked_by`` son las
        versiones sin aprobación vigente; para una máquina, todas (no consume aprobaciones).
        """
        if not hits:
            return None, []
        session = self._session()
        try:
            resolution = resolve(session, db_id, direction, hits)
        finally:
            session.close()

        is_human = self._is_human_actor(actor)
        executor_allowed = is_human and can_at(
            actor, Capability.BLUEPRINTS_APPLY_DESTRUCTIVE, database_target(db_id)
        )
        all_approved = resolution.is_fully_approved

        versions = []
        for spec in hits:
            version_status = resolution.statuses[spec.version]
            versions.append(
                {
                    "version": spec.version,
                    "migration_id": spec.id,
                    "sql_hash": version_status.current_hash,
                    "reasons": list(destructive.get(spec.version, ())),
                    "approval_status": version_status.status,
                    "approval_id": version_status.approval_id,
                    "approval_scope": version_status.scope,
                }
            )

        token: str | None = None
        expires_at: datetime | None = None
        if issue_token and is_human and executor_allowed and all_approved:
            subject = db_subject(db_id, direction, target_label, from_version, resolution.approved)
            token, expires_at = issue_confirm_token(
                operation,
                server_id,
                db_name,
                DESTRUCTIVE_CONFIRM_TTL_SECONDS,
                subject=subject,
            )

        plan = {
            "direction": direction,
            "requires_confirmation": True,
            "executor_allowed": executor_allowed,
            "all_approved": all_approved,
            "versions": versions,
            "confirm_token": token,
            "expires_at": expires_at,
            "confirm_name_expected": db_name,
        }
        blocked_by = (
            list(resolution.unapproved) if is_human else [spec.version for spec in hits]
        )
        return plan, blocked_by

    # ------------------------------------------------------------------ #
    # Aplicación                                                          #
    # ------------------------------------------------------------------ #
    def apply(
        self,
        db_id: int,
        *,
        up_to_version: str | None = None,
        force: bool = False,
        dry_run: bool = False,
        on_failure: str = "auto",
        admin: "dict | Actor | None" = None,
        confirm_token: str | None = None,
        confirm_name: str | None = None,
    ) -> dict:
        """
        Aplica las pendientes a UNA BD. ``confirm_token`` y ``confirm_name`` solo cuentan cuando
        hay versiones destructivas en un entorno protegido (ver ``_consume_check``); los demás
        llamadores internos (alta de BD, adopción, API de integración) no los mandan, así que
        nunca consumen aprobaciones.
        """
        session = self._session()
        try:
            md, server, model = self._load_context(session, db_id)
            specs = self._load_specs(session, model.id)
            self._verify_integrity(specs)
            slug, engine = model.slug, EngineType(engine_value(server))
            self._guard_cross_engine(session, model.id, engine)
            self._guard_reviewed_baseline(session, model.id)
            # El gate de ``reviewed`` de la captura NO va acá: se evalúa en ``_run_apply``
            # sobre las versiones REALMENTE pendientes (``apply?version=X`` aplica un prefijo
            # estricto, así que una versión posterior sin revisar no debe bloquear ni el
            # dry_run). Ver ``_guard_reviewed_capture``.
            self._guard_untranslatable_sql(specs, engine)
            self._guard_gateway_internal_sql(specs)
            db_name, server_id = md.name, md.server_id
            quarantined = md.status == ProvisionStatus.error
            target = build_target(server)
            # Política del entorno, resuelta mientras la sesión sigue abierta (valores planos:
            # después del close una fila ORM quedaría desligada).
            env_policy = self._env_policy_for(session, [db_id]).get(db_id)
        finally:
            session.close()

        if not specs:
            raise AppHttpException(
                message="El blueprint no tiene migraciones definidas.",
                status_code=422,
                context={"model_id": model.id},
            )

        # Versión objetivo inexistente: evita aplicar silenciosamente "todo lo ≤ X"
        # cuando X no es una versión real del blueprint. Comparación NUMÉRICA.
        self._assert_target_version_exists(specs, up_to_version, model.id)

        # ROB1 — cuarentena: una migración fallida previa pudo dejar la BD en estado
        # parcial (DDL no transaccional en MySQL). Se exige inspección + force=true.
        self._guard_quarantine(db_id, quarantined, force, dry_run)

        if dry_run:
            return self._dry_run_plan(
                db_id, db_name, server_id, target, slug, specs, up_to_version,
                env_policy=env_policy,
                destructive_for=lambda pending_specs: self._destructive_versions(
                    pending_specs, engine
                ),
                admin=admin,
            )

        if on_failure not in self._ON_FAILURE_MODES:
            raise AppHttpException(
                message="'on_failure' invalido.",
                status_code=422,
                context={"on_failure": on_failure, "allowed": list(self._ON_FAILURE_MODES)},
            )

        return self._run_apply(
            db_id, db_name=db_name, server_id=server_id, target=target,
            engine=engine, slug=slug, specs=specs, model_id=model.id,
            up_to_version=up_to_version, was_quarantined=quarantined, admin=admin,
            on_failure=on_failure,
            env_blocks_destructive=bool(env_policy and env_policy[0]),
            env_slug=env_policy[1] if env_policy else None,
            confirm_token=confirm_token,
            confirm_name=confirm_name,
        )

    @staticmethod
    def _guard_cross_engine(session, model_id: int, engine: EngineType) -> None:
        """
        Un baseline de snapshot queda atado a su ``source_engine`` en dos casos:

        - objetos NO portables (rutinas/triggers/events): sqlglot no transpila código
          procedural;
        - migraciones de DATOS (``kind='data'``): la sintaxis upsert difiere por motor
          (``ON DUPLICATE KEY UPDATE`` vs ``ON CONFLICT``) y no se traduce.

        En ambos casos, aplicar ese blueprint a un servidor de otro motor se bloquea (422).
        """
        row = (
            session.query(ModelMigration)
            .filter(
                ModelMigration.model_id == model_id,
                ModelMigration.source_engine.isnot(None),
                sa_or(
                    ModelMigration.has_non_portable.is_(True),
                    ModelMigration.kind == "data",
                ),
            )
            .first()
        )
        if row and row.source_engine != engine.value:
            reason = (
                "datos-semilla (INSERT con sintaxis upsert por motor)"
                if row.kind == "data"
                else "objetos no portables (rutinas/triggers)"
            )
            raise AppHttpException(
                message=(
                    f"El blueprint tiene una migración de snapshot del motor "
                    f"'{row.source_engine}' con {reason}: no puede aplicarse a un servidor "
                    f"'{engine.value}'. Genere un baseline específico para este motor."
                ),
                status_code=422,
                context={"source_engine": row.source_engine, "target_engine": engine.value},
            )

    # Construcciones de MySQL que, en el SQL CRUDO, delatan que la traducción a PostgreSQL
    # no va a ser confiable. Es un pre-filtro BARATO: solo si alguna aparece se paga el
    # transpilado completo para confirmarlo (ver _guard_untranslatable_sql).
    _MYSQLISM_RE = re.compile(
        r"\b(?:MODIFY\s+COLUMN|CHANGE\s+COLUMN|DROP\s+PRIMARY\s+KEY"
        r"|DROP\s+FOREIGN\s+KEY|DROP\s+CHECK|ENGINE\s*=|AUTO_INCREMENT\s*=)",
        re.IGNORECASE,
    )

    def _guard_untranslatable_sql(self, specs: list[MigrationSpec], engine: EngineType) -> None:
        """
        Bloquea (422) aplicar a PostgreSQL una migración cuyo DDL de MySQL no se puede
        traducir con certeza.

        sqlglot transpila bien expresiones y tipos, pero deja VERBATIM varias formas de DDL
        de MySQL al escribir PostgreSQL: ``MODIFY COLUMN``, ``DROP PRIMARY KEY``,
        ``ENGINE=``… El resultado es SQL sintácticamente inválido que antes solo se
        descubría cuando el motor lo rechazaba, a mitad de la migración. Peor: cuando
        ``translate`` devuelve ``None``, ``select_up_sql`` cae al ``up_sql`` base, que en
        dialecto MySQL crudo es igual de inválido contra PostgreSQL — o sea que "fallar
        callado" no era una defensa. Ahora se detecta ANTES de tocar el motor y se le pide
        al admin lo único que resuelve el caso: un ``up_sql_postgresql`` explícito.

        Las reescrituras seguras (``DROP INDEX … ON``, ``DROP FOREIGN KEY``/``INDEX``/
        ``CHECK`` → ``DROP CONSTRAINT``) las hace el traductor y NO bloquean.

        Coste: el pre-filtro es una regex sobre el SQL crudo; el transpilado (caro) solo se
        ejecuta si esa regex encuentra algo, y encima está memoizado.
        """
        if engine != EngineType.postgresql:
            return
        translator = SqlTranslator()
        problems: dict[str, list[str]] = {}
        for spec in specs:
            if spec.up_sql_postgresql or spec.kind == "data":
                continue  # override explícito, o ya cubierto por _guard_cross_engine
            if not self._MYSQLISM_RE.search(spec.up_sql):
                continue  # pre-filtro barato: nada sospechoso, no se paga el transpilado
            blockers = translator.translation_blockers(spec.up_sql, engine)
            if blockers:
                problems[spec.version] = blockers
        if problems:
            detail = "; ".join(f"{v}: {', '.join(b)}" for v, b in sorted(problems.items()))
            raise AppHttpException(
                message=(
                    "No se puede aplicar a PostgreSQL: hay migraciones con DDL específico "
                    f"de MySQL que no se traduce de forma confiable ({detail}). Define un "
                    "'up_sql_postgresql' explícito en esas versiones (PATCH) y reintenta."
                ),
                status_code=422,
                context={"target_engine": engine.value, "untranslatable": problems},
                public_context={"untranslatable": problems},
            )

    @staticmethod
    def _guard_gateway_internal_sql(specs: list[MigrationSpec]) -> None:
        """
        Bloquea (409) aplicar una migración cuyo SQL toque la contabilidad INTERNA del
        gateway (``_gw_v_{slug}`` / ``_gw_stg_*``).

        Es la red de seguridad para versiones creadas ANTES del fix del snapshot: el diff
        estructural incluía la tabla de versión del destino y podía generar
        ``DROP TABLE _gw_v_{slug}``. Aplicar eso ejecuta todo el DDL y después mata a
        Alembic al registrar la versión (``1146``), dejando la BD con los cambios hechos
        pero SIN puntero de versión — y como el fallo ocurre en la contabilidad y no en una
        sentencia, la auto-reconciliación no lo detecta como aplicación parcial.

        Se valida ANTES de tocar el motor y sobre TODAS las variantes de SQL (base +
        overrides por motor + rollback). La salida es corregir el ``up_sql`` de esa versión
        con un ``PATCH`` (la creación y la edición ya rechazan este SQL de entrada).
        """
        offenders: dict[str, list[str]] = {}
        for spec in specs:
            hits: list[str] = []
            for sql in (spec.up_sql, spec.up_sql_mysql, spec.up_sql_postgresql, spec.down_sql):
                for prefix in references_gateway_internal_table(sql or ""):
                    if prefix not in hits:
                        hits.append(prefix)
            if hits:
                offenders[spec.version] = hits
        if offenders:
            detail = ", ".join(
                f"{v} (toca {', '.join(p)}*)" for v, p in sorted(offenders.items())
            )
            raise AppHttpException(
                message=(
                    f"No se puede aplicar: hay versiones cuyo SQL toca la contabilidad "
                    f"interna del gateway — {detail}. Esas sentencias borrarían o "
                    "alterarían la tabla de versión de Alembic y dejarían la BD sin "
                    "puntero de versión. Corregí el 'up_sql' de esas versiones con un "
                    "PATCH (quitando esas sentencias) antes de aplicar. Si una BD ya "
                    "quedó así, recreá su puntero con 'stamp' en la versión que "
                    "físicamente tiene aplicada."
                ),
                status_code=409,
                context={"model_id": None, "offending_versions": offenders},
                public_context={"offending_versions": offenders},
            )

    @staticmethod
    def _guard_reviewed_baseline(session, model_id: int) -> None:
        """
        R1: un baseline de SNAPSHOT contiene DDL capturado del motor (potencialmente no
        confiable). Bloquea (409) ``apply``/``apply-all`` mientras el blueprint tenga un
        baseline ``reviewed=false``: un admin debe revisar el SQL y aprobarlo
        (PATCH reviewed=true). NO afecta a ``stamp`` (que no ejecuta SQL).
        """
        rows = (
            session.query(ModelMigration.version)
            .filter(
                ModelMigration.model_id == model_id,
                ModelMigration.is_baseline.is_(True),
                ModelMigration.reviewed.is_(False),
            )
            .all()
        )
        if rows:
            versions = [r[0] for r in rows]
            raise AppHttpException(
                message=(
                    f"El blueprint tiene un baseline de snapshot SIN revisar ({', '.join(versions)}). "
                    "Contiene DDL capturado del motor: revísalo y apruébalo "
                    "(PATCH reviewed=true en esa versión) antes de aplicar."
                ),
                status_code=409,
                context={"model_id": model_id, "unreviewed_baseline": versions},
            )

    @staticmethod
    def _guard_reviewed_capture(
        session, model_id: int, *, migration_ids: list[int] | None = None
    ) -> None:
        """
        Una versión con ``capture_selects=true`` va a EXTRAER datos de negocio de la BD
        destino y guardarlos (cifrados) en la BD del gateway. Mismo mecanismo que el gate R1
        de los baselines de snapshot: nace ``reviewed=false`` y no se puede aplicar hasta que
        un admin revise QUÉ consulta y lo apruebe explícitamente (PATCH reviewed=true).

        Va aparte de ``_guard_reviewed_baseline`` porque el motivo del 409 es distinto y el
        operador necesita leer el correcto: allí "DDL capturado del motor sin revisar", acá
        "esta versión extrae datos".

        ``migration_ids`` ACOTA el chequeo a las versiones que REALMENTE se van a ejecutar, y
        ambos caminos lo usan así: ``rollback`` con el camino a revertir, ``apply`` con las
        pendientes hasta la versión objetivo. Evaluarlo sobre TODO el blueprint estaba mal
        (una premisa falsa: ``apply?version=X`` aplica un prefijo ESTRICTO, no la cadena
        completa) — con 0001..0010 y solo 0010 sin revisar, un ``apply?version=0007`` devolvía
        409 nombrando una versión que esa corrida no iba a tocar, y ni siquiera se podía
        previsualizar con ``dry_run``. Es el mismo criterio que gobierna el rollback: bloquear
        por una versión futura sin revisar le quita al operador la salida que tiene ante una
        migración mala.

        **Kill switch global.** Con ``MIGRATION_CAPTURE_ENABLED=False`` el codegen no emite ni
        una llamada a ``capture_statement``, así que capturar es FÍSICAMENTE imposible: no hay
        riesgo que gatear, y mantener el 409 solo bloqueaba la recuperación (el ``rollback`` no
        tiene ningún ``force`` con el que saltearlo).

        **Es el ÚNICO gate de la captura.** El consentimiento por corrida
        (``allow_result_capture``) se retiró — el porqué está en ``_capture_versions``, que ocupa
        su lugar como noticia. Consecuencia: la calidad de ESTE guard importa más que antes,
        porque ya no hay una segunda red detrás. Lo que lo sostiene es que la aprobación es de una
        CONSULTA y no de la versión: cambiar ``up_sql``, los overrides por motor o el ``down_sql``
        vuelve ``reviewed`` a ``False`` automáticamente (ver ``update_migration``), y ``stamp``
        tiene su propio guard porque es lo que habilita el ``rollback`` de una versión.
        """
        if not MIGRATION_CAPTURE_ENABLED:
            return
        if migration_ids is not None and not migration_ids:
            return  # nada que revisar en este subconjunto: ni se consulta
        q = session.query(ModelMigration.version).filter(
            ModelMigration.model_id == model_id,
            ModelMigration.capture_selects.is_(True),
            ModelMigration.reviewed.is_(False),
        )
        if migration_ids is not None:
            q = q.filter(ModelMigration.id.in_(migration_ids))
        rows = q.all()
        if rows:
            versions = [r[0] for r in rows]
            raise AppHttpException(
                message=(
                    f"El blueprint tiene versiones con captura de resultados SIN revisar "
                    f"({', '.join(versions)}). Esas migraciones guardan el resultado de sus "
                    "SELECT (datos de la BD destino) en el gateway: revisá qué consultan y "
                    "aprobalas (PATCH reviewed=true) antes de aplicar o revertir."
                ),
                status_code=409,
                context={"model_id": model_id, "unreviewed_capture": versions},
                # ``code`` además de ``unreviewed_capture``: en ``apply_all`` la ruta responde 200
                # con los rechazos por BD, así que el ``public_context`` de la respuesta HTTP no
                # existe y el controller copia el código a ``item["error_code"]``. Sin él ese
                # campo salía ``None`` y el cliente tenía que matchear prosa.
                public_context={
                    "code": ccodes.CODE_UNREVIEWED_CAPTURE,
                    "unreviewed_capture": versions,
                },
            )

    @staticmethod
    def _capture_versions(specs: list[MigrationSpec]) -> list[str]:
        """
        Versiones del subconjunto que van a capturar el resultado de sus ``SELECT``.

        Es una NOTICIA, no un gate. Acá vivía ``_guard_capture_consent``, que exigía
        ``allow_result_capture=true`` en CADA corrida además del opt-in de la versión y de su
        aprobación. Se retiró, y el motivo importa para que nadie lo reponga sin releerlo:

        - **La premisa no aplicaba.** Se justificaba con "un blueprint se replica sobre N BDs de
          dueños potencialmente distintos, y quien aplica sobre UNA tiene que saber". Esos dueños
          son los ``ServerUser`` de las bases DESTINO; a nivel gateway hay un **administrador
          único** (``app/core/auth.py``: "no gestiona múltiples usuarios", sin roles ni permisos, y
          ningún router de usuarios expuesto). La misma persona activa la captura, aprueba
          ``reviewed`` y dispara el apply: no había un segundo par de ojos, solo un segundo momento.
        - **No dejaba rastro.** Pasar el flag no se auditaba en ninguna parte. Lo único auditado es
          la escritura efectiva (``_capture_pointer``), que ocurre con o sin gate — o sea, fricción
          sin evidencia forense. Compará con el guard de entorno, que sí registra
          ``migration.environment_denied`` al rechazar.
        - **``apply_all`` ya lo contradecía**: un único query param autorizaba N bases de entornos
          distintos, exactamente lo contrario de "conciencia de ESTA base".
        - Una BD nueva arranca sin versión, así que recibe la cadena COMPLETA y arrastra versiones
          históricas cuya captura tenía sentido sobre bases con datos. Sobre una base recién creada
          esos ``SELECT`` devuelven cero filas: el gate saltaba más fuerte donde el riesgo era
          menor, que es como se entrena el reflejo "siempre que sí" — y ese reflejo después se
          aplica también en producción. El control quedaba más débil, no más fuerte.

        El gate que SÍ queda es ``_guard_reviewed_capture``: aprueba una CONSULTA concreta y se
        revoca sola si el SQL cambia. Lo único que el consentimiento aportaba de verdad —el
        re-aviso en el momento de ejecutar— se compensa con esta lista, que viaja en el plan del
        dry-run y en el ``detail`` de la auditoría de intento. Es más de lo que dejaba el flag.
        """
        if not MIGRATION_CAPTURE_ENABLED:
            # Kill switch: el codegen no emite una sola llamada de captura, así que anunciarla
            # sería mentir.
            return []
        return [s.version for s in specs if s.capture_selects]

    @staticmethod
    def _guard_quarantine(db_id: int, quarantined: bool, force: bool, dry_run: bool) -> None:
        if quarantined and not force and not dry_run:
            raise AppHttpException(
                message=(
                    "La BD está en cuarentena por un fallo de migración previo. "
                    "Inspeccione el estado real y reintente con force=true."
                ),
                status_code=409,
                context={"managed_database_id": db_id, "required": "force=true"},
            )

    @staticmethod
    def _assert_target_version_exists(
        specs: list, target_version: str | None, model_id: int
    ) -> None:
        """
        422 si la versión objetivo no es una versión real del blueprint. Evita aplicar en
        silencio "todo lo ≤ X" cuando X no existe. Comparación NUMÉRICA. Lo comparten ``apply``
        y ``apply_all`` para que los dos caminos no puedan divergir en qué versión aceptan.
        """
        if target_version is None:
            return
        known_versions = {version_sort_key(spec.version) for spec in specs}
        if version_sort_key(target_version) in known_versions:
            return
        raise AppHttpException(
            message=(
                f"La versión objetivo {target_version} no existe en el blueprint. "
                f"Versiones disponibles: {', '.join(spec.version for spec in specs)}."
            ),
            status_code=422,
            context={"target_version": target_version, "model_id": model_id},
        )

    def _dry_run_plan(
        self, db_id, db_name, server_id, target, slug, specs, up_to_version,
        *,
        env_policy: tuple[bool, str] | None = None,
        destructive_for: Callable[[list[MigrationSpec]], dict[str, tuple[str, ...]]] | None = None,
        admin: "dict | Actor | None" = None,
        issue_token: bool = True,
    ) -> dict:
        """
        Calcula el plan (pendientes) SIN tocar el motor más que para leer la versión.

        ``destructive_for`` recibe las versiones PENDIENTES y devuelve cuáles son destructivas. Es
        una función y no un dict precalculado porque qué está pendiente solo se sabe después de
        leer la versión de la BD, y analizar con sqlglot todo el blueprint para quedarse con las
        pendientes era el costo dominante del dry-run.

        ``destructive_plan`` informa el estado de aprobación de cada versión destructiva pendiente y
        emite el ``confirm_token`` (ver ``_build_destructive_plan``). ``apply_all`` pasa
        ``issue_token=False``: en el lote hay UN token para todas las bases, no uno por base.

        El dry-run **informa** el bloqueo por entorno en ``blocked_by`` pero NO lo aplica: es
        la llamada con la que el operador descubre qué lo frena, así que hacerla fallar le
        quitaría el diagnóstico. Precedente exacto del criterio: ``_guard_quarantine`` recibe
        ``dry_run`` y se saltea. El guard real vive en ``_run_apply``, que este camino no
        ejecuta.

        Por el mismo criterio, una BD que NO existe en el motor tampoco hace fallar el
        dry-run: se informa con ``database_exists`` y ``no_op``, que es exactamente el
        diagnóstico que el operador vino a buscar.

        ``will_capture_versions`` sigue esa misma forma: es el PRONÓSTICO de qué versiones de esta
        corrida van a extraer filas y guardarlas (cifradas) en el gateway. Ocupa el lugar del 409
        de consentimiento que se retiró (ver ``_capture_versions``): la información se da ANTES, en
        la llamada que existe para decidir, en vez de trabar la que existe para ejecutar.

        El nombre NO es ``capture_versions``, y no es capricho: ese era exactamente el token del
        ``public_context`` del 409 que se borró, así que reusarlo dejaría el ``grep`` ambiguo para
        siempre entre "lo que anunciaba el gate muerto" y "lo que anuncia el plan". Y contra
        ``captured_versions`` (el HECHO, en la respuesta de la corrida real) se diferenciaría en
        dos letras — un par así se confunde leyendo un diff.
        """
        current, database_exists = self._read_current_version_tolerant(target, db_name, slug)
        pending = self.runner.compute_pending(current, specs, up_to_version)
        pending_versions = [s.version for s in pending]
        blocked_by: list[str] = []
        destructive_plan: dict | None = None
        destructive = (
            destructive_for(pending) if destructive_for and env_policy and env_policy[0] else None
        )
        if env_policy and env_policy[0] and destructive:
            hits = [spec for spec in pending if spec.version in destructive]
            if database_exists:
                destructive_plan, blocked_by = self._build_destructive_plan(
                    db_id,
                    db_name=db_name,
                    server_id=server_id,
                    direction=DIRECTION_UP,
                    hits=hits,
                    destructive=destructive,
                    actor=admin,
                    operation=OPERATION_APPLY_DESTRUCTIVE,
                    target_label=up_to_version or TARGET_LABEL_HEAD,
                    from_version=current,
                    issue_token=issue_token,
                )
            else:
                blocked_by = [spec.version for spec in hits]
        return {
            "will_capture_versions": self._capture_versions(pending),
            "managed_database_id": db_id,
            "database_name": db_name,
            "server_id": server_id,
            "dry_run": True,
            "database_exists": database_exists,
            "from_version": current,
            "current_version": current,  # alias retrocompatible
            "to_version": pending_versions[-1] if pending_versions else current,
            "target_version": up_to_version,
            # Sin base física no hay nada que aplicar, por más pendientes que liste el plan.
            "no_op": len(pending) == 0 or not database_exists,
            "pending_versions": pending_versions,
            "pending_count": len(pending),
            "environment_slug": env_policy[1] if env_policy else None,
            "blocked_by": blocked_by,
            "destructive_plan": destructive_plan,
        }

    # Política ante un fallo a mitad de una migración (solo aplica cuando el motor NO es
    # transaccional — en PostgreSQL el propio motor deshace la migración y no hay nada que
    # reconciliar).
    #   'auto'      → reconcilia SOLO si puede deshacer TODO lo aplicado (default).
    #   'reconcile' → reconcilia igual, salteando lo que no tiene reverso (equivale a force).
    #   'leave'     → no toca nada: cuarentena + checkpoint, como antes.
    _ON_FAILURE_MODES = ("auto", "reconcile", "leave")

    def _auto_reconcile_after_failure(
        self, db_id, *, db_name, target, engine, specs, results, mode, admin,
    ) -> dict | None:
        """
        Deshace la aplicación parcial INMEDIATAMENTE después del fallo, en la misma llamada.

        Es la "sobreprotección" que faltaba: sin esto, un `apply` que falla a mitad devolvía
        200 con `failed=true` y dejaba al admin con una BD en estado desconocido, un
        checkpoint que tenía que entender, y la tentación de `stamp --force` + `rollback`
        (que es exactamente el camino que corrompe: stampear afirma que las 50 sentencias
        corrieron, y el rollback ejecuta 50 reversos contra 10 cambios reales).

        Devuelve el resultado de la reconciliación, o ``None`` si no había nada que hacer
        (motor transaccional, sin checkpoint, o ``mode='leave'``).
        """
        if mode == "leave":
            return None
        failed = next((r for r in results if r.status == "failed"), None)
        if failed is None:
            return None
        spec = next((s for s in specs if s.id == failed.migration_id), None)
        if spec is None:
            return None
        row = next(
            (
                r
                for r in migration_progress.incomplete_progress_for_database(db_id, "up")
                if r["model_migration_id"] == spec.id
            ),
            None,
        )
        if row is None:
            # Sin checkpoint incompleto no hay estado parcial que deshacer: o el motor es
            # transaccional (PostgreSQL ya lo revirtió), o falló la PRIMERA sentencia, o la
            # migración no era resumible y no se puede saber qué corrió.
            return None
        plan = self._reconcile_plan(spec, engine, row)
        if not plan["inverses"]:
            return None
        if mode == "auto" and not plan["reconcilable"]:
            # Hay sentencias aplicadas SIN reverso: deshacer solo una parte dejaría un
            # estado igual de raro pero MENOS documentado. Se deja como está y el admin
            # decide (reconcile-partial con force, o a mano).
            logger.info(
                "No se auto-reconcilia %s: %d sentencia(s) aplicada(s) sin reverso.",
                spec.version, len(plan["unreversible"]),
            )
            return None
        audit.record_intent(
            "migration.auto_reconcile",
            admin=admin, target_type="managed_database", target_id=db_id,
            server_id=None,
            detail=(
                f"auto-reconciliación tras fallo de {spec.version}: deshacer "
                f"{len(plan['inverses'])} sentencia(s)"
            ),
        )
        undo = self.runner.reconcile_partial(
            target, db_name=db_name, engine=engine, managed_db_id=db_id,
            spec=spec, inverses=plan["inverses"],
            total_statements=row["total_statements"],
        )
        undo_failed = any(r.status == "failed" for r in undo)
        remaining = migration_progress.get_progress(db_id, spec.id, "up")
        fully = not undo_failed and remaining is None
        audit.record(
            "migration.auto_reconcile",
            status="error" if undo_failed else "success",
            admin=admin, target_type="managed_database", target_id=db_id,
            touched_engine=True,
            detail=(
                f"{sum(1 for r in undo if r.status == 'applied')}/{len(plan['inverses'])} "
                f"reverso(s) de {spec.version}"
                + (" — estado reconciliado" if fully else " — INCOMPLETO")
            ),
        )
        return {
            "version": spec.version,
            "attempted": True,
            "undone_count": sum(1 for r in undo if r.status == "applied"),
            "statements_to_undo": len(plan["inverses"]),
            "fully_reconciled": fully,
            "unconfirmed_reverses": plan["unconfirmed"],
            "unreversible_statements": plan["unreversible"],
            "error": next((r.error for r in undo if r.status == "failed"), None),
            "error_code": next((r.error_code for r in undo if r.status == "failed"), None),
        }

    @staticmethod
    def _guard_partial_down_before_apply(db_id: int, specs: list[MigrationSpec]) -> None:
        """
        Bloquea (409) el ``apply`` mientras haya un ROLLBACK parcialmente aplicado.

        Simétrico de ROB2 (que bloquea el rollback con un apply parcial): si el
        ``downgrade`` de la versión N falló a mitad, Alembic nunca movió el puntero — el
        ledger sigue en N — pero la BD ya tiene ALGUNOS reversos de N ejecutados. Un
        ``apply`` en ese estado lee "current=N", calcula pendientes desde ahí y deja la
        versión N a medio deshacer para siempre (ninguna re-aplicación la repara, porque
        para el ledger N "ya está aplicada"). La salida correcta es REINTENTAR el
        rollback, que retoma desde el checkpoint de dirección ``down``.
        """
        incomplete = migration_progress.incomplete_progress_for_database(db_id, direction="down")
        if not incomplete:
            return
        by_id = {s.id: s.version for s in specs}
        detail = ", ".join(
            f"versión {by_id.get(row['model_migration_id'], row['model_migration_id'])} "
            f"({row['last_statement_index']}/{row['total_statements']} reversos)"
            for row in incomplete
        )
        raise AppHttpException(
            message=(
                f"No se puede aplicar: hay un ROLLBACK parcialmente ejecutado ({detail}). "
                "La versión sigue registrada pero sus cambios están a medio deshacer; "
                "aplicar encima congelaría ese estado. Reintente el 'rollback' (retoma "
                "automáticamente desde el último reverso exitoso) antes de aplicar."
            ),
            status_code=409,
            context={"managed_database_id": db_id, "incomplete_progress": incomplete},
            public_context={"incomplete_progress": incomplete},
        )

    def _run_apply(
        self, db_id, *, db_name, server_id, target, engine, slug, specs, model_id,
        up_to_version, was_quarantined, admin, on_failure: str = "auto",
        env_blocks_destructive: bool = False,
        env_slug: str | None = None,
        destructive_versions: dict[str, tuple[str, ...]] | None = None,
        confirm_token: str | None = None,
        confirm_name: str | None = None,
        batch_confirmed: frozenset | None = None,
    ) -> dict:
        """
        Ejecuta el apply real sobre UNA BD ya cargada/validada (reutilizable por apply_all).

        ``confirm_token``/``confirm_name`` son los de un apply por BD. ``batch_confirmed`` lo manda
        ``apply_all`` cuando YA verificó el token del lote y el re-tipeo del entorno: acá solo se
        re-chequea, por BD, que las aprobaciones vigentes sigan siendo las confirmadas (TOCTOU).
        """
        # Simétrico de ROB2: con un rollback a medio ejecutar, aplicar encima opera a
        # ciegas. Va acá (no en apply()) para cubrir también apply_all, que captura la
        # excepción por BD sin abortar el lote.
        self._guard_partial_down_before_apply(db_id, specs)
        # Versión ANTES de aplicar (read-only) para reportar el salto from→to. Se lee con el
        # lector TOLERANTE para poder distinguir "sin migraciones" de "sin base": el 404 crudo
        # del driver (1049/3D000) es indistinguible del 404 de "BD no encontrada en el
        # inventario", y —peor— dejar seguir el apply termina en ``_set_quarantine`` marcando
        # la BD en ``error``, que enmascara la causa real. Cuesta cero en el camino feliz, y
        # cubre ``apply`` Y ``apply_all`` desde este único punto (mismo criterio que los tres
        # guards de abajo). Un ``list_databases()`` incondicional acá sería N+1 sobre el lote.
        from_version, database_exists = self._read_current_version_tolerant(
            target, db_name, slug
        )
        if not database_exists:
            raise self._not_provisioned(db_id, db_name, server_id, op="aplicar migraciones")
        # El gate de la captura, sobre las versiones que REALMENTE se van a aplicar en ESTA BD
        # (no sobre todo el blueprint): una versión POSTERIOR a la objetivo no debe bloquear una
        # aplicación que no la incluye. Va acá (no en ``apply``) para cubrir también
        # ``apply_all``, que reporta el 409 por BD.
        pending = self.runner.compute_pending(from_version, specs, up_to_version)
        capture_pending_ids = [s.id for s in pending if s.capture_selects]
        if capture_pending_ids:
            session = self._session()
            try:
                self._guard_reviewed_capture(
                    session, model_id, migration_ids=capture_pending_ids
                )
            finally:
                session.close()
        # Guard de entorno, sobre las pendientes REALES de ESTA BD. Va acá por el mismo
        # criterio que los dos guards de captura de arriba, y con el mismo beneficio: cubre el
        # ``apply`` por BD y ``apply_all`` de una sola vez, sin releer el motor.
        #
        # Ya no es un bloqueo duro: una versión destructiva se ejecuta solo con aprobación vigente,
        # ``apply_destructive``, token y re-tipeo (``_consume_check``). ``force`` sigue sin
        # intervenir, y los llamadores internos (alta de BD, adopción, integración) nunca traen
        # token, así que nunca consumen aprobaciones.
        consumed_approvals: Resolution | None = None
        if env_blocks_destructive:
            destructive = (
                destructive_versions
                if destructive_versions is not None
                else self._destructive_versions(pending, engine)
            )
            destructive_hits = [spec for spec in pending if spec.version in destructive]
            consumed_approvals = self._consume_check(
                db_id,
                direction=DIRECTION_UP,
                hits=destructive_hits,
                destructive=destructive,
                env_slug=env_slug or "?",
                actor=admin,
                engine=engine,
                operation=OPERATION_APPLY_DESTRUCTIVE,
                server_id=server_id,
                db_name=db_name,
                target_label=up_to_version or TARGET_LABEL_HEAD,
                from_version=from_version,
                confirm_token=confirm_token,
                confirm_name=confirm_name,
                batch_confirmed=batch_confirmed,
            )
        # La auditoría de INTENCIÓN nombra las versiones que van a extraer datos. Es la
        # contrapartida de haber retirado el consentimiento por corrida: ese flag no dejaba
        # ningún rastro, así que esto no reemplaza evidencia, la agrega. Queda la intención acá
        # y el resultado en ``migration.select_results.write`` (``_capture_pointer``).
        capturing = self._capture_versions(pending)
        detail = f"apply hasta {up_to_version or 'head'}"
        if capturing:
            detail += f" (captura: {', '.join(capturing)})"
        audit.record(
            "migration.apply", status="attempt", admin=admin,
            target_type="managed_database", target_id=db_id, server_id=server_id,
            touched_engine=True, detail=detail,
        )
        try:
            results = self.runner.apply(
                target, db_name=db_name, slug=slug, engine=engine,
                managed_db_id=db_id, specs=specs, up_to_version=up_to_version,
                # Solo hay guard (y por tanto algo que proteger) en entornos que bloquean.
                analyzed_versions=(
                    frozenset(spec.version for spec in pending) if env_blocks_destructive else None
                ),
            )
        except AppHttpException as exc:
            # Fallo ANTES de aplicar ninguna migración (conexión/lock): no hay
            # resultado por-migración que registrar; dejamos traza en auditoría.
            audit.record(
                "migration.apply", status="error", admin=admin,
                target_type="managed_database", target_id=db_id, server_id=server_id,
                touched_engine=True,
                detail=f"fallo al aplicar (HTTP {getattr(exc, 'status_code', '?')})",
            )
            raise

        self._register_consumption(consumed_approvals, admin)
        self._record_history(db_id, results, direction="up", specs=specs, admin=admin)
        self._mirror_history(
            target, db_name, engine, model_id=model_id, slug=slug, results=results,
            direction="up", specs=specs, admin=admin,
        )
        failed = any(r.status == "failed" for r in results)

        # Auto-protección: si quedó una aplicación PARCIAL (solo posible en MySQL/MariaDB,
        # donde el DDL hace commit implícito), se deshace acá mismo. Va ANTES de sincronizar
        # la versión y de marcar cuarentena para que ambos reflejen el estado YA reconciliado.
        reconciliation = None
        if failed:
            reconciliation = self._auto_reconcile_after_failure(
                db_id, db_name=db_name, target=target, engine=engine, specs=specs,
                results=results, mode=on_failure, admin=admin,
            )

        # model_version se SINCRONIZA releyendo la fuente de verdad (tabla de versión
        # que Alembic mantiene en la BD destino), no la contabilidad local.
        self._sync_model_version_from_engine(db_id, target, db_name, slug)

        # ROB1 — marcar/limpiar cuarentena según el desenlace. Si la auto-reconciliación
        # dejó la BD coincidiendo con su versión registrada, NO se pone en cuarentena: no
        # hay estado parcial que inspeccionar, solo una migración que hay que corregir.
        reconciled_clean = bool(reconciliation and reconciliation["fully_reconciled"])
        self._set_quarantine(db_id, failed and not reconciled_clean, results)

        audit.record(
            "migration.apply", status="error" if failed else "success", admin=admin,
            target_type="managed_database", target_id=db_id, server_id=server_id,
            touched_engine=True,
            detail=f"{sum(1 for r in results if r.status=='applied')} aplicadas"
                   + (" (con fallo)" if failed else ""),
        )
        applied = [r for r in results if r.status == "applied"]
        to_version = applied[-1].version if applied else from_version
        captured, captured_versions = self._capture_pointer(
            db_id, results, admin=admin, server_id=server_id
        )
        return {
            "managed_database_id": db_id,
            "captured_select_count": captured,
            "captured_versions": captured_versions,
            "select_results_available": captured > 0,
            "database_name": db_name,
            "server_id": server_id,
            "from_version": from_version,
            "to_version": to_version,
            "target_version": up_to_version,
            "applied_count": len(applied),
            "failed": failed,
            "quarantined": failed and not reconciled_clean,
            "no_op": len(results) == 0 and not failed,
            "pending_versions": [r.version for r in results],
            "results": [self._result_dict(r) for r in results],
            # Qué hizo el sistema por su cuenta ante el fallo (None si no hizo falta).
            "reconciliation": reconciliation,
        }

    # ------------------------------------------------------------------ #
    # Captura de resultados de SELECT (informe, nunca maquinaria)          #
    # ------------------------------------------------------------------ #
    def _capture_pointer(
        self,
        db_id: int,
        results: list[MigrationResult],
        *,
        admin: "dict | Actor | None",
        server_id: int | None,
    ) -> tuple[int, list[str]]:
        """
        ``(cuántas capturas escribió ESTA corrida, en qué versiones)`` + auditoría de esa
        ESCRITURA (conteos y versiones, nunca valores).

        El número viene del propio runner (``MigrationResult.captured_results``, que devuelve
        ``migration_results.finalize``), NO de un ``COUNT`` sobre la tabla: la tabla acumula
        las capturas de corridas anteriores, así que contarla afirmaba escrituras que no
        habían ocurrido. Caso real: una versión aplicada con captura (1 fila ``up``) y después
        revertida con un ``down_sql`` sin lecturas → el ``rollback`` respondía
        ``captured_select_count: 1`` y auditaba una escritura inexistente. Las capturas
        viejas siguen leyéndose con ``GET …/select-results``; el puntero de la respuesta habla
        solo de lo que esta corrida produjo.

        **Las VERSIONES no son adorno**: sin ellas el cliente solo sabe "se capturó algo" y para
        armar el enlace a ``…/{version}/select-results`` tiene que adivinar. La SPA usaba
        ``to_version`` (la última aplicada), así que un ``apply`` 0005→0010 cuya captura ocurrió
        en 0007 enlazaba a ``…/0010/select-results``, que está VACÍO. El dato ya se calculaba acá
        para contarlo y se tiraba.
        """
        written = sum(r.captured_results for r in results)
        if not written:
            return 0, []
        versions = sorted(
            {r.version for r in results if r.captured_results}, key=version_sort_key
        )
        audit.record(
            "migration.select_results.write",
            admin=admin,
            target_type="managed_database",
            target_id=db_id,
            server_id=server_id,
            touched_engine=False,
            detail=(
                f"{written} resultado(s) de SELECT capturados en "
                f"{len(versions)} versión(es): {', '.join(versions)}"
            ),
        )
        return written, versions

    def select_results(
        self, db_id: int, version: str, *, admin: "dict | Actor | None" = None
    ) -> dict:
        """
        Devuelve las capturas de una versión sobre esta BD, DESCIFRADAS.

        La auditoría es FAIL-CLOSED y va ANTES de descifrar (mismo criterio que
        ``reveal_password``): esto entrega datos de negocio, así que si el rastro no se puede
        persistir la lectura no ocurre.
        """
        session = self._session()
        try:
            md, server, model = self._load_context(session, db_id)
            specs = self._load_specs(session, model.id)
            engine = EngineType(engine_value(server))
            db_name, server_id = md.name, md.server_id
        finally:
            session.close()

        spec = self._spec_or_404(specs, version, model.id)

        audit.record_intent(
            "migration.select_results.read",
            admin=admin,
            target_type="managed_database",
            target_id=db_id,
            server_id=server_id,
            touched_engine=False,
            detail=f"lectura de resultados capturados de la versión {version}",
        )
        items = migration_results.read_results(db_id, spec.id)

        # Índices ESPERADOS: se derivan acá y no se persisten. Que la lista de sentencias
        # salga de ``statement_lists`` es lo que garantiza que estos índices signifiquen lo
        # mismo que los del checkpoint.
        up_statements = self.runner.statement_lists(spec, engine)[0]
        expected = [
            i
            for i, stmt in enumerate(up_statements, start=1)
            if migration_results.is_capturable(stmt, engine=engine.value)
        ]
        present_up = {it["statement_index"] for it in items if it["direction"] == "up"}
        rolled_back = [
            it["statement_index"]
            for it in items
            if it["durability"] == migration_results.DURABILITY_ROLLED_BACK
        ]
        audit.record(
            "migration.select_results.read",
            admin=admin,
            target_type="managed_database",
            target_id=db_id,
            server_id=server_id,
            touched_engine=False,
            detail=f"{len(items)} captura(s) de la versión {version} entregadas",
        )
        return {
            "managed_database_id": db_id,
            "database_name": db_name,
            "server_id": server_id,
            "model_migration_id": spec.id,
            "version": spec.version,
            "capture_selects": spec.capture_selects,
            "stale": any(it["migration_checksum"] != spec.checksum for it in items),
            "expected_indexes": expected,
            "missing_indexes": [i for i in expected if i not in present_up],
            "durability_warning": (
                "PostgreSQL revirtió la transacción de la migración: las sentencias "
                f"{', '.join(str(i) for i in rolled_back)} muestran lo que se vio DURANTE el "
                "intento, no el estado final de la base."
                if rolled_back
                else None
            ),
            "items": items,
        }

    def purge_select_results(
        self, db_id: int, version: str, *, admin: "dict | Actor | None" = None
    ) -> int:
        """Borra las capturas de una versión sobre esta BD. Devuelve cuántas se borraron."""
        session = self._session()
        try:
            md, _server, model = self._load_context(session, db_id)
            specs = self._load_specs(session, model.id)
            server_id = md.server_id
        finally:
            session.close()

        spec = self._spec_or_404(specs, version, model.id)
        audit.record_intent(
            "migration.select_results.purge",
            admin=admin,
            target_type="managed_database",
            target_id=db_id,
            server_id=server_id,
            touched_engine=False,
            detail=f"purga de los resultados capturados de la versión {version}",
        )
        deleted = migration_results.purge(db_id, spec.id)
        audit.record(
            "migration.select_results.purge",
            admin=admin,
            target_type="managed_database",
            target_id=db_id,
            server_id=server_id,
            touched_engine=False,
            detail=f"{deleted} captura(s) de la versión {version} eliminadas",
        )
        return deleted

    @staticmethod
    def _spec_or_404(
        specs: list[MigrationSpec], version: str, model_id: int
    ) -> MigrationSpec:
        spec = next(
            (s for s in specs if version_sort_key(s.version) == version_sort_key(version)),
            None,
        )
        if spec is None:
            raise AppHttpException(
                message=f"La versión {version} no existe en el blueprint de esta BD.",
                status_code=404,
                context={"model_id": model_id, "version": version},
            )
        return spec

    def rollback(
        self,
        db_id: int,
        *,
        confirm_version: str | None = None,
        target_version: str | None = None,
        admin: "dict | Actor | None" = None,
        dry_run: bool = False,
        confirm_token: str | None = None,
        confirm_name: str | None = None,
    ) -> dict:
        """
        Revierte una BD a ``target_version`` de forma SECUENCIAL en una sola llamada
        (análogo a apply, hacia atrás): el sistema detecta qué downgrades hay que
        aplicar y los ejecuta en orden. Si ``target_version`` se omite, revierte solo
        la última migración (compatibilidad). Operación DESTRUCTIVA: exige
        ``confirm_version == versión actual`` (doble intención) y que TODO el camino
        tenga ``down_sql`` confirmado (409 si falta alguno).

        Si alguna versión del camino tiene ``capture_selects=true``, exige lo MISMO que
        ``apply``: que esas versiones estén revisadas (``reviewed=true``). No es simetría
        decorativa: el codegen emite ``capture_statement`` para las sentencias del ``down_sql``
        igual que para las del ``up_sql``, así que un rollback extrae y persiste datos de negocio
        exactamente como un apply — antes del fix B1 no llamaba a ningún guard y bastaba un
        ``confirm_version`` para exfiltrar filas.

        **Guard de entorno.** En una BD de un entorno que bloquea lo destructivo, un ``down_sql``
        destructivo (el ``DROP TABLE`` que deshace un ``CREATE``) exige su PROPIA aprobación de
        dirección ``down``: la de ``up`` no la cubre porque es otro SQL con otro hash. Además
        ``apply_destructive``, ``confirm_token`` y re-escribir el nombre de la base, igual que el
        apply. Con ``dry_run=true`` devuelve el plan (y el token si corresponde) sin tocar el motor
        y ``confirm_version`` pasa a ser opcional.
        """
        # Antes de abrir sesión o leer el motor: sin plan, la doble intención es obligatoria, igual
        # que cuando el parámetro era requerido en la ruta (422 antes de costar una lectura).
        if not dry_run and confirm_version is None:
            raise AppHttpException(
                message="Confirmación requerida: falta 'confirm_version' (la versión actual de la BD).",
                status_code=422,
                context={"managed_database_id": db_id, "required": "confirm_version == current"},
            )
        session = self._session()
        try:
            md, server, model = self._load_context(session, db_id)
            specs = self._load_specs(session, model.id)
            self._verify_integrity(specs)  # el rollback ejecuta DDL destructivo
            # ``model_id`` se captura ACÁ y no se lee de ``model`` más abajo: fuera del
            # ``with`` la instancia queda detached, y un atributo expirado reventaría el
            # rollback entero por un dato que solo alimenta el espejo.
            slug, engine, model_id = model.slug, EngineType(engine_value(server)), model.id
            db_name, server_id = md.name, md.server_id
            target = build_target(server)
            env_policy = self._env_policy_for(session, [db_id]).get(db_id)
        finally:
            session.close()

        # ROB2 — una aplicación PARCIAL bloquea el rollback. Ver el docstring del guard:
        # revertir N-1 mientras la BD tiene media versión N aplicada es el escenario de
        # corrupción que este módulo existe para evitar.
        self._guard_partial_before_rollback(db_id, specs)

        # El chequeo de NO-EXISTENCIA va PRIMERO: el 409 de más abajo ("no tiene ninguna
        # migración aplicada") es verdadero para una BD vacía y MENTIROSO para una que no
        # existe, y manda al operador a buscar el problema donde no está.
        current, database_exists = self._read_current_version_tolerant(target, db_name, slug)
        if not database_exists:
            raise self._not_provisioned(db_id, db_name, server_id, op="revertir")
        if current is None:
            raise AppHttpException(
                message="La BD no tiene ninguna migración aplicada para revertir.",
                status_code=409,
                context={"managed_database_id": db_id},
            )
        # Doble intención: el cliente repite la versión ACTUAL (de la que parte). El plan
        # (dry-run) no ejecuta nada, así que no la exige.
        if not dry_run and confirm_version != current:
            raise AppHttpException(
                message=(
                    "Confirmación requerida: 'confirm_version' debe coincidir con la "
                    f"versión actual de la BD ({current})."
                ),
                status_code=422,
                context={"managed_database_id": db_id, "required": "confirm_version == current"},
            )

        cur_key = version_sort_key(current)
        spec_keys = {version_sort_key(s.version) for s in specs}

        # Determinar el destino del rollback.
        if target_version is None:
            # Compat: revertir UNA migración (la actual). Destino = versión existente
            # inmediatamente inferior, o base (None) si la actual es la primera.
            below = sorted(
                (s.version for s in specs if version_sort_key(s.version) < cur_key),
                key=version_sort_key,
            )
            dest = below[-1] if below else None
        else:
            tkey = version_sort_key(target_version)
            if tkey >= cur_key:
                raise AppHttpException(
                    message=(
                        f"La versión objetivo ({target_version}) debe ser ANTERIOR a la "
                        f"actual ({current}). Para avanzar usa /migrations/apply."
                    ),
                    status_code=422,
                    context={"target_version": target_version, "current": current},
                )
            if tkey not in spec_keys:
                raise AppHttpException(
                    message=f"La versión objetivo {target_version} no existe en el blueprint.",
                    status_code=422,
                    context={"target_version": target_version, "model_id": model.id},
                )
            dest = target_version

        # Camino a revertir: versiones con dest < v <= current (las que se desharán).
        dest_key = version_sort_key(dest) if dest is not None else None
        path = sorted(
            (
                s for s in specs
                if version_sort_key(s.version) <= cur_key
                and (dest_key is None or version_sort_key(s.version) > dest_key)
            ),
            key=lambda s: version_sort_key(s.version),
            reverse=True,
        )
        # Fail-closed: TODO el camino debe tener down_sql confirmado ANTES de ejecutar
        # (evita un rollback que falle a mitad por un downgrade no definido).
        missing = [s.version for s in path if not s.down_sql]
        if missing:
            raise AppHttpException(
                message=(
                    "No se puede revertir: las versiones "
                    f"{', '.join(missing)} no tienen rollback (down_sql) confirmado. "
                    "Confírmalo con PATCH en cada migración."
                ),
                status_code=409,
                context={"managed_database_id": db_id, "missing_down_sql": missing},
                # El frontend necesita esta lista para guiar al admin (PATCH por versión),
                # no es solo debug info: viaja siempre, no solo en development.
                public_context={"missing_down_sql": missing},
            )

        # Guard de entorno sobre el ``down_sql`` del camino. Se calcula ANTES del dry-run para que
        # el plan informe lo mismo que el rollback real exigiría.
        env_blocks_destructive = bool(env_policy and env_policy[0])
        env_slug = env_policy[1] if env_policy else None
        down_destructive: dict[str, tuple[str, ...]] = {}
        down_hits: list[MigrationSpec] = []
        if env_blocks_destructive:
            down_destructive = destructive_down_versions(self.runner, path, engine)
            down_hits = [spec for spec in path if spec.version in down_destructive]
        target_label = dest or TARGET_LABEL_BASE

        if dry_run:
            destructive_plan, blocked_by = self._build_destructive_plan(
                db_id,
                db_name=db_name,
                server_id=server_id,
                direction=DIRECTION_DOWN,
                hits=down_hits,
                destructive=down_destructive,
                actor=admin,
                operation=OPERATION_ROLLBACK_DESTRUCTIVE,
                target_label=target_label,
                from_version=current,
                issue_token=True,
            )
            return {
                "managed_database_id": db_id,
                "database_name": db_name,
                "server_id": server_id,
                "from_version": current,
                "to_version": dest,
                "target_version": dest,
                "dry_run": True,
                "no_op": len(path) == 0,
                "reverted_count": 0,
                "reverted_versions": [],
                "rollback_path_versions": [spec.version for spec in path],
                "environment_slug": env_slug,
                "blocked_by": blocked_by,
                "destructive_plan": destructive_plan,
            }

        # Captura de resultados: el MISMO gate que exige ``apply``, sobre el camino que
        # REALMENTE se va a ejecutar (no sobre todo el blueprint — ver el docstring de
        # ``_guard_reviewed_capture``). Va acá, después de calcular ``path`` y antes de la
        # auditoría de intento: se responde 409 sin tocar el motor.
        capture_path_ids = [s.id for s in path if s.capture_selects]
        if capture_path_ids:
            session = self._session()
            try:
                self._guard_reviewed_capture(
                    session, model.id, migration_ids=capture_path_ids
                )
            finally:
                session.close()

        consumed_approvals = self._consume_check(
            db_id,
            direction=DIRECTION_DOWN,
            hits=down_hits,
            destructive=down_destructive,
            env_slug=env_slug or "?",
            actor=admin,
            engine=engine,
            operation=OPERATION_ROLLBACK_DESTRUCTIVE,
            server_id=server_id,
            db_name=db_name,
            target_label=target_label,
            from_version=current,
            confirm_token=confirm_token,
            confirm_name=confirm_name,
            batch_confirmed=None,
        )

        # Mismo criterio que en ``_run_apply``: la intención queda auditada nombrando qué
        # versiones extraen datos. El ``down_sql`` captura igual que el ``up_sql``.
        capturing = self._capture_versions(path)
        detail = f"rollback {current} -> {dest or 'base'}"
        if capturing:
            detail += f" (captura: {', '.join(capturing)})"
        audit.record(
            "migration.rollback", status="attempt", admin=admin,
            target_type="managed_database", target_id=db_id, server_id=server_id,
            touched_engine=True, detail=detail,
        )
        results = self.runner.rollback_to(
            target, db_name=db_name, slug=slug, engine=engine,
            managed_db_id=db_id, specs=specs, to_version=dest,
        )
        self._register_consumption(consumed_approvals, admin)
        self._record_history(db_id, results, direction="down", specs=path, admin=admin)
        self._mirror_history(
            target, db_name, engine, model_id=model_id, slug=slug, results=results,
            direction="down", specs=path, admin=admin,
        )
        # La versión tras el rollback se RE-LEE del motor (fuente de verdad) y se
        # sincroniza en el inventario del gateway.
        new_current = self.runner.get_current_version(target, db_name, slug)
        self._set_model_version(db_id, new_current)

        failed = any(r.status == "failed" for r in results)
        self._set_quarantine(db_id, failed, results)
        reverted = [r for r in results if r.status == "applied"]

        audit.record(
            "migration.rollback",
            status="error" if failed else "success",
            admin=admin, target_type="managed_database", target_id=db_id,
            server_id=server_id, touched_engine=True,
            detail=f"{len(reverted)} revertida(s): {current} -> {new_current or 'base'}"
                   + (" (con fallo)" if failed else ""),
        )
        captured, captured_versions = self._capture_pointer(
            db_id, results, admin=admin, server_id=server_id
        )
        return {
            "managed_database_id": db_id,
            "database_name": db_name,
            "server_id": server_id,
            "from_version": current,
            "to_version": new_current,
            "target_version": dest,
            "reverted_count": len(reverted),
            "captured_select_count": captured,
            "captured_versions": captured_versions,
            "select_results_available": captured > 0,
            "reverted_versions": [r.version for r in reverted],
            "failed": failed,
            "quarantined": failed,
            "no_op": len(results) == 0,
            "results": [self._result_dict(r) for r in results],
        }

    def stamp(
        self,
        db_id: int,
        version: str,
        *,
        force: bool = False,
        purge: bool = False,
        admin: "dict | Actor | None" = None,
    ) -> dict:
        session = self._session()
        try:
            md, server, model = self._load_context(session, db_id)
            specs = self._load_specs(session, model.id)
            self._verify_integrity(specs)
            slug, engine = model.slug, EngineType(engine_value(server))
            db_name, server_id = md.name, md.server_id
            target = build_target(server)
            self._guard_stamp_unreviewed_capture(session, model.id, version, force)
        finally:
            session.close()

        if purge and not force:
            raise AppHttpException(
                message=(
                    "'purge' descarta el puntero de versión actual sin leerlo, así que "
                    "requiere 'force': es una afirmación de que ya sabés en qué versión está "
                    "esta base."
                ),
                status_code=422,
                context={"managed_database_id": db_id},
            )
        self._guard_partial_checkpoint(db_id, force)

        # ``runner.stamp`` abre conexión a la BD destino, así que una base inexistente YA falla
        # ahí y nunca llega a ``_set_model_version``/``_set_quarantine``. Lo que faltaba era que
        # el error dijera algo útil: sin esto sube el 404 genérico del driver, que no distingue
        # "la base no existe" de "la BD gestionada no está en el inventario" ni nombra la salida.
        with self._translating_unknown_database(
            target, db_id, db_name, server_id, op="marcar una versión"
        ):
            # ``purge`` VACÍA la tabla de versión antes de escribir, en vez de pedirle a
            # Alembic que resuelva el puntero ACTUAL para moverlo. Es la única salida para
            # una BD cuyo puntero nombra una revisión que ya no existe en la cadena: sin
            # purge, Alembic muere con ``Can't locate revision identified by …`` y esa base
            # queda sin apply, sin rollback y sin stamp. Exige ``force`` porque descarta el
            # valor viejo sin leerlo: quien lo pide tiene que estar afirmando que ya sabe
            # dónde está esa base.
            self.runner.stamp(
                target, db_name=db_name, slug=slug, engine=engine,
                managed_db_id=db_id, specs=specs, version=version, purge=purge,
            )
        self._set_model_version(db_id, version)
        # El stamp es una AFIRMACIÓN explícita del admin ("esta BD está en la versión X"):
        # reconcilia el estado, así que también saca a la BD de cuarentena si un apply
        # previo la dejó en 'error' (p. ej. reintentar CREATE TABLE de una tabla ya
        # existente tras adoptarla). No ejecuta SQL, solo marca la versión.
        self._set_quarantine(db_id, failed=False, results=[])
        if force:
            # El admin afirma haber reconciliado el estado físico a mano: cualquier
            # checkpoint de sentencia (de CUALQUIER versión y en CUALQUIER dirección)
            # que quedara pendiente para esta BD ya no es confiable — el stamp
            # reescribe la narrativa de versión por completo, así que el checkpoint
            # quedaría hablando de un estado que el admin acaba de invalidar.
            migration_progress.clear_progress_for_database(db_id)
        # ``stamp`` DECLARA una versión sin ejecutar una línea de DDL, y con eso reescribe
        # ``managed_databases.model_version``. O sea que es la puerta trasera de cualquier
        # política que se apoye en esa caché: un gate de promoción del tipo "no apliques en
        # producción si staging no está al día" se destraba stampeando las BDs de staging al
        # head. Ese gate NO existe todavía (ver TODO.md); cuando se construya, tiene que cerrar
        # este camino y no solo leer la caché. Mientras tanto el rastro es explícito: la
        # auditoría nombra la versión declarada y la que había antes, para que un stamp usado
        # como atajo se pueda reconstruir después.
        audit.record(
            "migration.stamp", admin=admin, target_type="managed_database",
            target_id=db_id, server_id=server_id, touched_engine=True,
            detail=f"stamp DECLARA version {version} (sin ejecutar DDL)"
            + (" (force: checkpoint parcial descartado)" if force else "")
            + (" (purge: tabla de versión vaciada antes de escribir)" if purge else ""),
        )
        return self.status(db_id)

    @staticmethod
    def _guard_stamp_unreviewed_capture(
        session, model_id: int, version: str, force: bool
    ) -> None:
        """
        Bloquea (409) marcar (``stamp``) una versión que tiene la captura de resultados
        activada y AÚN NO fue revisada. Defensa en profundidad, no la barrera principal.

        ``stamp`` no ejecuta SQL, y por eso ``_guard_reviewed_baseline`` lo excluye a
        propósito. Pero el stamp es lo que HABILITA el ``rollback`` de esa versión: sin este
        gate el camino era crear la versión con ``capture_selects=true`` (nace
        ``reviewed=false``) → stampearla (sin control) → ``rollback`` sobre ella. Ese último
        paso ya está cubierto por su propio gate; esto lo corta un paso antes, en el punto
        donde se afirma un estado que el gateway no pudo haber producido: ``apply`` jamás
        aplicó esa versión (su gate de ``reviewed`` lo impide), así que no hay historia
        legítima en la que una BD esté genuinamente en ella.

        ``force=true`` lo omite, con el MISMO significado que ya tiene en este endpoint ("el
        admin afirma haber reconciliado el estado físico a mano"). Hace falta un escape real:
        una versión aplicada hace meses a la que después se le activó la captura queda
        ``reviewed=false``, y una BD que perdió su puntero de versión (ver el incidente de
        ``_gw_v_*``) necesita poder re-stampearla para salir de cuarentena.
        """
        if force:
            return
        row = (
            session.query(ModelMigration.id)
            .filter(
                ModelMigration.model_id == model_id,
                ModelMigration.version == version,
                ModelMigration.capture_selects.is_(True),
                ModelMigration.reviewed.is_(False),
            )
            .first()
        )
        if row is None:
            return
        raise AppHttpException(
            message=(
                f"La versión {version} tiene la captura de resultados activada y SIN revisar: "
                "marcarla habilitaría un rollback que extraería datos de esta base. Revisala "
                "y aprobala (PATCH reviewed=true) antes de marcarla. Si la BD realmente ya "
                "está en esa versión y estás reconciliando a mano, repetí con force=true."
            ),
            status_code=409,
            context={
                "model_id": model_id,
                "version": version,
                "required": "reviewed=true (o force=true)",
            },
            # Código PROPIO, distinto del de apply/rollback: acá ``force=true`` sí es un escape
            # legítimo, y con un código único la SPA ofrecería «Forzar» también donde no sirve.
            public_context={
                "code": ccodes.CODE_UNREVIEWED_CAPTURE_STAMP,
                "unreviewed_capture": [version],
            },
        )

    @staticmethod
    def _guard_partial_before_rollback(db_id: int, specs: list[MigrationSpec]) -> None:
        """
        ROB2: bloquea (409) el ``rollback`` mientras haya una aplicación PARCIAL pendiente.

        Es el agujero más grave que tenía el flujo de rollback. Cuando el ``apply`` de la
        versión N falla a mitad, Alembic NO alcanzó a escribir N en ``_gw_v_{slug}`` (el
        stamp va al final del ``upgrade()``), así que el ledger sigue en N-1 mientras la BD
        tiene, físicamente, las primeras sentencias de N ya commiteadas (AUTOCOMMIT: el DDL
        de MySQL/MariaDB no es transaccional).

        En ese estado, ``rollback`` NO veía nada raro: leía "current = N-1" y se ponía a
        ejecutar el ``down_sql`` de N-1 contra una BD contaminada con parte de N. O falla a
        mitad —dejando un tercer estado inconsistente— o "funciona" por casualidad y deja
        objetos huérfanos de N que el gateway ya no sabe que existen.

        La salida no es forzar: es RECONCILIAR primero (``/migrations/reconcile-partial``,
        que deshace exactamente las sentencias que sí se aplicaron) o reintentar el
        ``apply`` para completar N. Recién entonces el rollback opera sobre un estado
        conocido.
        """
        incomplete = migration_progress.incomplete_progress_for_database(db_id, direction="up")
        if not incomplete:
            return
        by_id = {s.id: s.version for s in specs}
        detail = ", ".join(
            f"versión {by_id.get(row['model_migration_id'], row['model_migration_id'])} "
            f"({row['last_statement_index']}/{row['total_statements']} sentencias)"
            for row in incomplete
        )
        raise AppHttpException(
            message=(
                f"No se puede revertir: hay una aplicación PARCIAL sin resolver ({detail}). "
                "La versión no quedó registrada en la BD, así que el rollback operaría "
                "sobre un estado desconocido. Primero: reintente 'apply' para completarla, "
                "o use 'migrations/reconcile-partial' para deshacer exactamente lo que sí "
                "se aplicó."
            ),
            status_code=409,
            context={"managed_database_id": db_id, "incomplete_progress": incomplete},
            public_context={"incomplete_progress": incomplete},
        )

    @staticmethod
    def _guard_partial_checkpoint(db_id: int, force: bool) -> None:
        """
        ``stamp`` afirma "esta BD está en la versión X" SIN ejecutar SQL. Si hay una
        aplicación PARCIAL detectada (checkpoint de sentencia incompleto: algunas
        sentencias de alguna migración ya commitearon, pero no todas) para esta BD, un
        stamp ciego la enmascararía: un rollback posterior ejecutaría el ``down_sql``
        completo de esa versión contra una BD que solo tiene una FRACCIÓN de los cambios
        físicos — pudiendo fallar a mitad también, o "funcionar" solo por casualidad.

        Bloquea por defecto; ``force=true`` es la vía explícita para "ya reconcilié el
        estado físico a mano, proceda" (descarta el checkpoint, auditado). Sin este
        override, un fallo NO resumible (sentencia rota / DDL no atómico — ver
        ``migration_progress.py``) dejaría al admin sin ninguna salida.

        **ADVERTENCIA sobre el anti-patrón más común.** La reacción intuitiva ante un apply
        que falla a mitad es ``stamp --force`` a la versión que falló y después ``rollback``.
        Eso EMPEORA el problema: el stamp AFIRMA que las N sentencias de esa versión se
        aplicaron, así que el rollback ejecuta los N reversos contra una BD que solo tiene k
        cambios físicos — los N-k reversos de lo que nunca corrió fallan ("no existe") y el
        rollback muere a mitad, dejando un TERCER estado inconsistente. Además ``force``
        descarta el checkpoint, que era la única prueba de dónde había quedado. La vía
        correcta es ``/migrations/reconcile-partial`` (deshace exactamente las k que
        corrieron) o reintentar ``apply``, que retoma desde el checkpoint.
        """
        # AMBAS direcciones: un rollback a medio ejecutar (checkpoint 'down') deja la BD
        # tan desconocida como un apply a medias — stampear encima lo enmascara igual.
        incomplete = migration_progress.incomplete_progress_for_database(db_id, direction="up")
        incomplete += [
            {**row, "direction": "down"}
            for row in migration_progress.incomplete_progress_for_database(db_id, direction="down")
        ]
        if incomplete and not force:
            detail = ", ".join(
                f"migración {row['model_migration_id']} "
                f"({row['last_statement_index']}/{row['total_statements']} sentencias"
                + (" del rollback" if row.get("direction") == "down" else "")
                + ")"
                for row in incomplete
            )
            raise AppHttpException(
                message=(
                    f"No se puede stampear: hay una aplicación PARCIAL detectada ({detail}). "
                    "Vías correctas, en orden de preferencia: (1) "
                    "POST /migrations/reconcile-partial, que deshace EXACTAMENTE las "
                    "sentencias que sí se aplicaron y deja la BD en su versión anterior; "
                    "(2) reintentar 'apply', que retoma automáticamente desde la última "
                    "sentencia exitosa. NO stampees esta versión para después revertirla: "
                    "el stamp afirma que se aplicó COMPLETA, así que el rollback ejecutaría "
                    "todos los reversos contra una BD que solo tiene una fracción de los "
                    "cambios, y fallaría también. Si ya reconciliaste el estado físico a "
                    "mano, repetí el stamp con force=true."
                ),
                status_code=409,
                context={"managed_database_id": db_id, "incomplete_progress": incomplete},
                public_context={"incomplete_progress": incomplete},
            )

    # ------------------------------------------------------------------ #
    # Reconciliación de una aplicación PARCIAL                            #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _reconcile_plan(spec: MigrationSpec, engine: EngineType, progress_row: dict) -> dict:
        """
        Plan de reconciliación de UNA migración parcial: qué reversos hay que ejecutar.

        Requiere el MANIFIESTO (``model_migration_statements``): con solo los blobs
        ``up_sql``/``down_sql`` es imposible saber qué reverso corresponde a la sentencia
        ``k`` — el ``down_sql`` es una secuencia INDEPENDIENTE, con otra cantidad de
        sentencias (los cambios sin reverso simplemente no aparecen). Sin manifiesto se
        devuelve ``reconcilable=False`` con el motivo, en vez de adivinar.

        TRES estados, no dos, porque ``force`` es una salida real y confundirla con
        "imposible" deja al admin sin ninguna:

        - ``reconcilable``: se puede deshacer TODO lo aplicado. Vía normal.
        - ``reconcilable_with_force``: hay reversos, pero alguna sentencia aplicada no
          tiene ninguno. ``reconcile_partial(force=True)`` deshace el resto y reporta lo
          que queda — es exactamente el escape que documenta su docstring.
        - ninguno de los dos: no hay NADA que ejecutar (sin manifiesto, desalineado, o
          ni una sola sentencia aplicada tiene reverso). La única salida es manual +
          ``stamp?force=true``.

        ``reason`` se puebla SIEMPRE que no sea ``reconcilable``, incluido el estado del
        medio: viaja al frontend en ``partial_application[]`` y es lo único que le dice al
        operador por qué la vía normal no está disponible.
        """
        runner = MigrationRunner()
        manifest = runner.usable_manifest(spec, engine)
        applied = int(progress_row["last_statement_index"])
        total = int(progress_row["total_statements"])
        if not manifest:
            return {
                "reconcilable": False,
                "reconcilable_with_force": False,
                "reason": (
                    "esta versión no tiene manifiesto de sentencias para el motor destino "
                    "(migración escrita a mano o SQL editado): no se puede saber qué "
                    "reverso corresponde a cada sentencia aplicada"
                ),
                "count": 0, "unreversible": [], "unconfirmed": [], "inverses": [],
            }
        if len(manifest) != total:
            return {
                "reconcilable": False,
                "reconcilable_with_force": False,
                "reason": (
                    f"el checkpoint habla de {total} sentencias y el manifiesto tiene "
                    f"{len(manifest)}: no coinciden, no se reconcilia a ciegas"
                ),
                "count": 0, "unreversible": [], "unconfirmed": [], "inverses": [],
            }
        # Sentencias efectivamente aplicadas (1..applied), en orden INVERSO.
        pending = [m for m in manifest if m.seq <= applied]
        pending.sort(key=lambda m: m.seq, reverse=True)
        unreversible = [
            {"seq": m.seq, "object_type": m.object_type, "object_name": m.object_name}
            for m in pending if not m.down_sql
        ]
        # Reversos que NO son demostrablemente seguros: existen, pero pueden fallar (una
        # UNIQUE/CHECK/FK que se re-crea VALIDA los datos actuales) o no restaurar los datos
        # (recrear una tabla borrada devuelve la estructura, no las filas). No bloquean —
        # son el mejor reverso disponible — pero el admin tiene que verlos en el dry-run.
        unconfirmed = [
            {
                "seq": m.seq,
                "object_type": m.object_type,
                "object_name": m.object_name,
                "destructive": m.destructive,
            }
            for m in pending if m.down_sql and not m.down_confirmed
        ]
        inverses: list[tuple[int, str]] = []
        for m in pending:
            if not m.down_sql:
                continue
            # Un reverso puede ser multi-sentencia (DROP nuevo; CREATE viejo): se parte,
            # porque cada exec_driver_sql admite una sola sentencia.
            for sql in split_sql_statements(m.down_sql):
                inverses.append((m.seq, sql))
        return {
            "reconcilable": not unreversible,
            # Estado del medio: hay algo que deshacer, pero no todo. ``force`` procede.
            "reconcilable_with_force": bool(unreversible) and bool(inverses),
            "reason": (
                None
                if not unreversible
                else (
                    f"{len(unreversible)} de las {len(pending)} sentencias aplicadas no "
                    "tienen reverso conocido"
                    + (
                        ": con force=true se deshacen las demás y esos cambios quedan "
                        "aplicados en la BD"
                        if inverses
                        else ", y ninguna de las aplicadas lo tiene: no hay reversos que "
                        "ejecutar"
                    )
                )
            ),
            "count": len(inverses),
            "unreversible": unreversible,
            "unconfirmed": unconfirmed,
            "inverses": inverses,
        }

    def reconcile_partial(
        self,
        db_id: int,
        *,
        confirm_version: str,
        dry_run: bool = False,
        force: bool = False,
        admin: "dict | Actor | None" = None,
    ) -> dict:
        """
        Deshace las sentencias que SÍ se aplicaron de una migración que falló a mitad.

        Deja la BD igual a lo que el ledger de Alembic ya afirma (la versión parcial nunca
        se registró), así que NO toca la tabla de versión: es una compensación, no un
        ``downgrade``. Después de esto la BD queda en un estado conocido y el ``rollback``
        normal vuelve a estar disponible.

        Doble intención: ``confirm_version`` debe ser la versión parcialmente aplicada (el
        admin tiene que haber mirado el estado antes). ``force=true`` procede aunque haya
        sentencias SIN reverso — las saltea y las reporta: sin eso, una sola sentencia
        irreversible dejaría al admin sin salida automática.
        """
        session = self._session()
        try:
            md, server, model = self._load_context(session, db_id)
            specs = self._load_specs(session, model.id)
            self._verify_integrity(specs)  # se va a ejecutar DDL destructivo
            engine = EngineType(engine_value(server))
            db_name, server_id = md.name, md.server_id
            target = build_target(server)
        finally:
            session.close()

        incomplete = migration_progress.incomplete_progress_for_database(db_id, direction="up")
        if not incomplete:
            raise AppHttpException(
                message="Esta BD no tiene ninguna aplicación parcial que reconciliar.",
                status_code=409,
                context={"managed_database_id": db_id},
            )
        by_id = {s.id: s for s in specs}
        # La versión MÁS ALTA primero: es la última que se intentó aplicar.
        rows = sorted(
            incomplete,
            key=lambda r: version_sort_key(
                by_id[r["model_migration_id"]].version
                if r["model_migration_id"] in by_id else "0"
            ),
            reverse=True,
        )
        row = rows[0]
        spec = by_id.get(row["model_migration_id"])
        if spec is None:
            raise AppHttpException(
                message=(
                    "La migración parcialmente aplicada ya no existe en el blueprint: no "
                    "hay reversos que ejecutar. Reconcilie el estado a mano y use "
                    "'stamp?force=true'."
                ),
                status_code=409,
                context={"model_migration_id": row["model_migration_id"]},
            )
        if confirm_version != spec.version:
            raise AppHttpException(
                message=(
                    "Confirmación requerida: 'confirm_version' debe coincidir con la "
                    f"versión parcialmente aplicada ({spec.version})."
                ),
                status_code=422,
                context={
                    "managed_database_id": db_id,
                    "partial_version": spec.version,
                    "required": "confirm_version == partial_version",
                },
            )

        plan = self._reconcile_plan(spec, engine, row)
        if not plan["inverses"] and not plan["reconcilable"]:
            raise AppHttpException(
                message=f"No se puede reconciliar automáticamente: {plan['reason']}.",
                status_code=409,
                context={"managed_database_id": db_id, "version": spec.version},
                public_context={"reason": plan["reason"]},
            )
        if plan["unreversible"] and not force:
            raise AppHttpException(
                message=(
                    f"{len(plan['unreversible'])} de las sentencias ya aplicadas no tienen "
                    "reverso: reconciliar dejaría esos cambios en la BD. Revíselos y "
                    "reintente con force=true para deshacer el resto, o reconcilie a mano."
                ),
                status_code=409,
                context={"managed_database_id": db_id, "version": spec.version},
                public_context={"unreversible_statements": plan["unreversible"]},
            )

        base = {
            "managed_database_id": db_id,
            "database_name": db_name,
            "server_id": server_id,
            "version": spec.version,
            "applied_statements": row["last_statement_index"],
            "total_statements": row["total_statements"],
            "statements_to_undo": len(plan["inverses"]),
            "unreversible_statements": plan["unreversible"],
            "unconfirmed_reverses": plan["unconfirmed"],
        }
        if dry_run:
            return {
                **base,
                "dry_run": True,
                "statements": [{"seq": seq, "sql": sql} for seq, sql in plan["inverses"]],
            }

        # Auditoría fail-closed ANTES de tocar el motor.
        audit.record_intent(
            "migration.reconcile_partial",
            admin=admin,
            target_type="managed_database",
            target_id=db_id,
            server_id=server_id,
            detail=(
                f"reconciliar aplicación parcial de {spec.version}: deshacer "
                f"{len(plan['inverses'])} sentencia(s) de las "
                f"{row['last_statement_index']} aplicadas"
            ),
        )
        # Mismo criterio que ``stamp``: el runner ya abre conexión al destino, así que acá solo
        # se traduce el 404 opaco a un 409 que nombra la salida.
        with self._translating_unknown_database(
            target, db_id, db_name, server_id, op="reconciliar"
        ):
            results = self.runner.reconcile_partial(
                target,
                db_name=db_name,
                engine=engine,
                managed_db_id=db_id,
                spec=spec,
                inverses=plan["inverses"],
                total_statements=row["total_statements"],
            )
        failed = any(r.status == "failed" for r in results)
        remaining = migration_progress.get_progress(db_id, spec.id, "up")
        fully_reconciled = not failed and remaining is None
        if fully_reconciled:
            # El plano físico volvió a coincidir con el ledger: la BD sale de cuarentena.
            self._set_quarantine(db_id, failed=False, results=[])
        audit.record(
            "migration.reconcile_partial",
            status="error" if failed else "success",
            admin=admin,
            target_type="managed_database",
            target_id=db_id,
            server_id=server_id,
            touched_engine=True,
            detail=(
                f"{sum(1 for r in results if r.status == 'applied')}/"
                f"{len(plan['inverses'])} reverso(s) ejecutado(s) de {spec.version}"
                + (" (con fallo)" if failed else "")
                + (" — estado reconciliado" if fully_reconciled else "")
            ),
        )
        return {
            **base,
            "dry_run": False,
            "undone_count": sum(1 for r in results if r.status == "applied"),
            "failed": failed,
            "fully_reconciled": fully_reconciled,
            "remaining_applied_statements": (
                remaining.last_statement_index if remaining else 0
            ),
            "results": [
                {
                    "seq": r.index,
                    "status": r.status,
                    "error": r.error,
                    "error_code": r.error_code,
                    "execution_ms": r.execution_ms,
                }
                for r in results
            ],
        }

    def _static_batch_entries(
        self,
        protected: list[tuple[int, str, int, object]],
        specs: list[MigrationSpec],
        target_version: str | None,
        destructive_by_engine,
        targets: dict[int, tuple],
    ) -> list[tuple[int, int, str, int]]:
        """
        Entradas del subject del token del lote: por cada base protegida, las versiones destructivas
        HASTA el destino que tienen una aprobación vigente. No lee ninguna base destino, así que el
        subject es el mismo en el dry-run y en la ejecución mientras no cambie ninguna aprobación.
        """
        up_to_target = self.runner.compute_pending(None, specs, target_version)
        entries: list[tuple[int, int, str, int]] = []
        session = self._session()
        try:
            for db_id, _name, server_id, _status in protected:
                _target, engine = targets[server_id]
                destructive = destructive_by_engine(engine, up_to_target)
                hits = [spec for spec in up_to_target if spec.version in destructive]
                if not hits:
                    continue
                resolution = resolve(session, db_id, DIRECTION_UP, hits)
                entries.extend(
                    (db_id, entry.migration_id, entry.sql_hash, entry.approval_id)
                    for entry in resolution.approved
                )
        finally:
            session.close()
        return entries

    def _preflight_destructive_batch(
        self,
        model_id: int,
        *,
        slug: str,
        dbs: list[tuple[int, str, int, object]],
        targets: dict[int, tuple],
        env_policies: dict[int, tuple[bool, str]],
        specs: list[MigrationSpec],
        target_version: str | None,
        destructive_by_engine,
        admin,
        dry_run: bool,
        force: bool,
        confirm_token: str | None,
        confirm_phrase: str | None,
    ) -> _BatchPreflight:
        """
        Preflight TODO-O-NADA de ``apply_all`` sobre las bases protegidas (A-1).

        Si una sola versión destructiva pendiente de cualquier base no tiene aprobación vigente, o
        falta el token, la capacidad o el re-tipeo, no se ejecuta NADA en el lote: aplicar la mitad
        dejaría bases protegidas en versiones distintas, y revertir eso es otro incidente. Los
        rechazos por ítem de más abajo quedan solo como defensa ante carreras (re-chequeo TOCTOU).

        Lee la versión actual de cada base protegida para saber qué está pendiente: es una lectura
        al motor que el resto del lote hace igual en ``_run_apply``. Si esa lectura falla se asume
        pendiente todo hasta el destino (conservador: nunca se subestima lo destructivo).

        Una máquina no pasa por acá: no consume aprobaciones, así que cada ítem se rechaza solo con
        ``environment.destructive_blocked``.
        """
        protected = [
            row for row in dbs if env_policies.get(row[0]) and env_policies[row[0]][0]
        ]
        if not protected:
            return _BatchPreflight()
        is_human = self._is_human_actor(admin)
        up_to_target_specs = self.runner.compute_pending(None, specs, target_version)

        pending_hits_by_db: dict[int, list[MigrationSpec]] = {}
        for db_id, name, server_id, status in protected:
            target, engine = targets[server_id]
            destructive = destructive_by_engine(engine, up_to_target_specs)
            if not destructive:
                continue
            if status == ProvisionStatus.error and not force and not dry_run:
                continue
            try:
                current, database_exists = self._read_current_version_tolerant(
                    target, name, slug
                )
            except Exception:  # noqa: BLE001 - se asume lo peor, no se aborta el preflight
                current, database_exists = None, True
            if not database_exists:
                continue
            pending = self.runner.compute_pending(current, specs, target_version)
            hits = [spec for spec in pending if spec.version in destructive]
            if hits:
                pending_hits_by_db[db_id] = hits

        if not pending_hits_by_db:
            return _BatchPreflight()

        slugs = sorted({env_policies[db_id][1] for db_id in pending_hits_by_db})
        multi_environment = len(slugs) > 1
        if multi_environment and not dry_run and is_human:
            raise AppHttpException(
                message=(
                    "El lote abarca más de un entorno protegido con migraciones destructivas. "
                    "Filtre por entorno (environment_id) y confirme de a uno."
                ),
                status_code=422,
                public_context={
                    "code": CODE_BATCH_MULTI_ENVIRONMENT,
                    "environment_slugs": slugs,
                },
            )
        if not is_human and not dry_run:
            return _BatchPreflight()

        unapproved_databases: list[dict] = []
        executor_allowed = is_human
        session = self._session()
        try:
            for db_id, hits in pending_hits_by_db.items():
                resolution = resolve(session, db_id, DIRECTION_UP, hits)
                if not resolution.is_fully_approved:
                    unapproved_databases.append(
                        {"managed_database_id": db_id, "versions": list(resolution.unapproved)}
                    )
                if is_human and not can_at(
                    admin,
                    Capability.BLUEPRINTS_APPLY_DESTRUCTIVE,
                    database_target(db_id),
                ):
                    executor_allowed = False
        finally:
            session.close()
        all_approved = not unapproved_databases

        target_label = target_version or TARGET_LABEL_HEAD
        entries = self._static_batch_entries(
            protected, specs, target_version, destructive_by_engine, targets
        )
        subject = batch_subject(model_id, target_label, entries)
        batch_name = f"model:{model_id}"
        batch_slug = slugs[0] if len(slugs) == 1 else None

        if dry_run:
            token: str | None = None
            expires_at: datetime | None = None
            if is_human and executor_allowed and all_approved and not multi_environment:
                token, expires_at = issue_confirm_token(
                    OPERATION_APPLY_ALL_DESTRUCTIVE,
                    model_id,
                    batch_name,
                    DESTRUCTIVE_CONFIRM_TTL_SECONDS,
                    subject=subject,
                )
            return _BatchPreflight(
                confirmation={
                    "required": True,
                    "confirm_token": token,
                    "expires_at": expires_at,
                    "environment_slug": batch_slug,
                    "environment_slugs": slugs,
                    "entry_count": len(entries),
                    "executor_allowed": executor_allowed,
                    "all_approved": all_approved,
                    "unapproved_databases": unapproved_databases,
                }
            )

        if unapproved_databases:
            blocked_versions = sorted(
                {v for item in unapproved_databases for v in item["versions"]}
            )
            raise AppHttpException(
                message=(
                    f"El entorno '{batch_slug}' bloquea las migraciones destructivas y hay "
                    "versiones sin aprobación vigente en el lote. No se ejecutó nada."
                ),
                status_code=409,
                public_context={
                    "code": ecodes.CODE_DESTRUCTIVE_BLOCKED,
                    "environment_slug": batch_slug,
                    "blocked_databases": unapproved_databases,
                    "blocked_versions": blocked_versions,
                    "direction": DIRECTION_UP,
                },
            )
        for db_id in pending_hits_by_db:
            assert_at_with_code(
                admin,
                Capability.BLUEPRINTS_APPLY_DESTRUCTIVE,
                database_target(db_id),
                code=CODE_EXECUTOR_FORBIDDEN,
                message=(
                    "Ejecutar migraciones destructivas en un entorno protegido exige la "
                    "capacidad blueprints.apply_destructive sobre cada base del lote."
                ),
            )
        if not confirm_token:
            raise self._confirmation_required(
                "Ejecutar un lote destructivo exige el 'confirm_token' del dry-run y re-escribir "
                "el slug del entorno."
            )
        wrap_verify(
            confirm_token,
            OPERATION_APPLY_ALL_DESTRUCTIVE,
            model_id,
            batch_name,
            subject=subject,
        )
        if confirm_phrase != batch_slug:
            raise AppHttpException(
                message="Para confirmar hay que re-escribir el slug exacto del entorno.",
                status_code=422,
                public_context={"code": CODE_CONFIRM_PHRASE_MISMATCH},
            )
        return _BatchPreflight(confirmed=frozenset(entries))

    def apply_all(
        self,
        model_id: int,
        *,
        max_databases: int,
        database_ids: list[int] | None = None,
        environment_id: int | None = None,
        force: bool = False,
        dry_run: bool = False,
        on_failure: str = "auto",
        target_version: str | None = None,
        channel: str | None = None,
        admin: "dict | Actor | None" = None,
        confirm_token: str | None = None,
        confirm_phrase: str | None = None,
    ) -> dict:
        """
        Aplica las pendientes a TODAS las BDs del blueprint (síncrono, acotado).
        Continúa con las demás BDs aunque una falle. La versión en background es
        ``start_apply_all_job``: mismos tres pasos (``_prepare_apply_all``, ``_apply_all_item``,
        ``_finish_apply_all``), así que las dos devuelven el mismo ítem por base.

        ``target_version`` o ``channel`` (excluyentes) acotan el lote: ninguna BD avanza más
        allá de esa versión. Una BD que YA la superó no se toca ni se revierte: reporta cero
        pendientes, porque este endpoint solo avanza (retroceder es ``rollback``, con sus
        propias confirmaciones). Sin ninguno de los dos aplica hasta la última, como siempre.

        Optimización (evita trabajo N+1): carga y verifica ``specs`` UNA sola vez y
        cachea el ``ServerTarget`` por servidor (la credencial se descifra una vez por
        servidor, no por BD).

        **Lote destructivo.** Si alguna base de un entorno protegido tiene versiones destructivas
        pendientes, ``_preflight_destructive_batch`` decide ANTES de tocar la primera base: todo o
        nada, con un único ``confirm_token`` del dry-run y el slug del entorno como re-tipeo
        (``confirm_phrase``). Un lote que abarca más de un entorno protegido se rechaza (422).
        """
        plan = self._prepare_apply_all(
            model_id,
            max_databases=max_databases,
            database_ids=database_ids,
            environment_id=environment_id,
            force=force,
            dry_run=dry_run,
            on_failure=on_failure,
            target_version=target_version,
            channel=channel,
            admin=admin,
            confirm_token=confirm_token,
            confirm_phrase=confirm_phrase,
        )
        outcomes = [self._apply_all_item(plan, database, admin=admin) for database in plan.dbs]
        return self._finish_apply_all(plan, outcomes, admin=admin)

    def _prepare_apply_all(
        self,
        model_id: int,
        *,
        max_databases: int,
        database_ids: list[int] | None = None,
        environment_id: int | None = None,
        force: bool = False,
        dry_run: bool = False,
        on_failure: str = "auto",
        target_version: str | None = None,
        channel: str | None = None,
        admin: "dict | Actor | None" = None,
        confirm_token: str | None = None,
        confirm_phrase: str | None = None,
    ) -> _ApplyAllPlan:
        """
        Paso 1 de ``apply_all``: TODAS las validaciones del lote y el preflight destructivo.

        No toca ninguna base administrada (el preflight solo LEE). Si algo falla lanza la
        excepción y no se ejecutó nada; el job en background lo corre en el request, antes de
        crear su fila, para que un lote inválido nunca llegue al worker.
        """
        # Mismo guard que ``apply``: el parámetro llega ahora desde la ruta (antes se
        # aceptaba en la firma pero la ruta no lo exponía, así que siempre valía "auto" y el
        # selector del frontend no hacía nada). Se valida contra la MISMA tupla para que los
        # dos caminos no puedan divergir.
        if on_failure not in self._ON_FAILURE_MODES:
            raise AppHttpException(
                message="'on_failure' invalido.",
                status_code=422,
                context={"on_failure": on_failure, "allowed": list(self._ON_FAILURE_MODES)},
            )

        # Antes de abrir sesión o tocar nada: un destino ambiguo no debe costar ni una lectura.
        reject_channel_with_target_version(target_version, channel)

        resolved_channel: dict | None = None
        session = self._session()
        try:
            model = session.get(DatabaseModel, model_id)
            if model is None:
                raise AppHttpException(
                    message="Blueprint no encontrado.", status_code=404,
                    context={"model_id": model_id},
                )
            slug = model.slug
            specs = self._load_specs(session, model_id)
            self._guard_reviewed_baseline(session, model_id)
            # El gate de ``reviewed`` de la captura se evalúa por BD en ``_run_apply``, sobre
            # sus versiones pendientes reales: acá, a nivel de lote, una versión con captura
            # sin revisar que NINGUNA de estas BDs tiene pendiente frenaba el lote entero
            # (incluido el dry_run). El 409 sale igual, pero por BD y nombrando lo que esa BD
            # sí iba a ejecutar.
            total = (
                session.query(ManagedDatabase)
                .filter(ManagedDatabase.model_id == model_id)
                .count()
            )
            scoped = session.query(
                ManagedDatabase.id, ManagedDatabase.name,
                ManagedDatabase.server_id, ManagedDatabase.status,
                ManagedDatabase.environment_id,
            ).filter(ManagedDatabase.model_id == model_id)
            if environment_id is not None:
                # El filtro va ANTES del ``limit`` de abajo, para que el tope no se consuma con
                # BDs de otros entornos y "aplicá a desarrollo" no quede recortado por
                # producción.
                scoped = scoped.filter(ManagedDatabase.environment_id == environment_id)
            if database_ids:
                # Los ids que NO pertenecen al blueprint se rechazan explícitamente en vez de
                # dejar que el `IN` los ignore en silencio. No es cosmética: es la frontera
                # que impide aplicar las migraciones de un blueprint a una BD ajena pasando
                # su id, y el resto del gateway es fail-closed en todas partes.
                valid = {
                    r[0]
                    for r in session.query(ManagedDatabase.id)
                    .filter(
                        ManagedDatabase.model_id == model_id,
                        ManagedDatabase.id.in_(database_ids),
                    )
                    .all()
                }
                unknown = sorted(set(database_ids) - valid)
                if unknown:
                    raise AppHttpException(
                        message=(
                            "Hay BDs que no pertenecen a este blueprint: "
                            f"{', '.join(str(i) for i in unknown)}."
                        ),
                        status_code=422,
                        context={"model_id": model_id, "unknown_database_ids": unknown},
                        public_context={"unknown_database_ids": unknown},
                    )
                # Mismo criterio fail-closed para el cruce con el filtro de entorno: un id que
                # SÍ pertenece al blueprint pero NO al entorno pedido desaparecería en silencio
                # del lote, que es exactamente el recorte callado que el bloque de arriba evita
                # a propósito.
                if environment_id is not None:
                    in_env = {
                        r[0]
                        for r in session.query(ManagedDatabase.id)
                        .filter(
                            ManagedDatabase.id.in_(database_ids),
                            ManagedDatabase.environment_id == environment_id,
                        )
                        .all()
                    }
                    outside = sorted(valid - in_env)
                    if outside:
                        raise AppHttpException(
                            message=(
                                "Hay BDs que no pertenecen al entorno indicado: "
                                f"{', '.join(str(i) for i in outside)}."
                            ),
                            status_code=422,
                            public_context={
                                "code": ecodes.CODE_DATABASES_OUTSIDE,
                                "database_ids_outside": outside,
                            },
                            context={"environment_id": environment_id},
                        )
                scoped = scoped.filter(ManagedDatabase.id.in_(database_ids))
            # Coincidencias ANTES del recorte. ``total_databases`` cuenta todas las BDs del
            # blueprint e ignora los filtros (contrato existente, no se toca porque la SPA lo
            # imprime literal), así que sin este número "3 de 40 procesadas" no dice si sobraron
            # 37 o si en ese entorno solo había 3.
            candidates = scoped.order_by(ManagedDatabase.id.asc()).all()
            # Capa 2 POR ÍTEM, antes del tope: las BDs prohibidas no consumen ``max_databases`` ni
            # cuentan en ``processed``. Con ``database_ids`` explícitos una prohibida da 403 y no
            # corre nada; sin ellos se omiten y se reportan solo con su id.
            partition = partition_for_batch(
                admin=admin,
                capability=Capability.BLUEPRINTS_APPLY,
                points_fn=lambda: points_for_databases(candidates),
                explicit=bool(database_ids),
            )
            omitted: list[int] = []
            if partition is not None:
                omitted = list(partition.forbidden)
                allowed = set(partition.permitted)
                candidates = [r for r in candidates if r.id in allowed]
            matched = len(candidates)
            db_rows = candidates[:max_databases]
            dbs = [(r.id, r.name, r.server_id, r.status) for r in db_rows]
            # Política de entorno de TODO el lote en una query (anti-N+1), con valores planos:
            # la sesión se cierra unas líneas más abajo, antes del bucle.
            env_policies = self._env_policy_for(session, [d[0] for d in dbs])
            # ServerTarget + engine por servidor distinto (descifra credencial 1×/servidor).
            targets: dict[int, tuple] = {}
            for sid in {d[2] for d in dbs}:
                srv = get_server_or_404(session, sid)
                targets[sid] = (build_target(srv), EngineType(engine_value(srv)))
            # Guards por MOTOR presente en el lote. ``apply`` los corre para su única BD;
            # acá el lote puede ser HETEROGÉNEO (el mismo blueprint en servidores de motores
            # distintos), así que se validan una vez por motor distinto — antes se aplicaba
            # sin revisarlos y un blueprint atado a un motor solo se detectaba por el fallo
            # del propio motor, BD por BD.
            self._guard_gateway_internal_sql(specs)
            for _target, eng in {(None, e) for _t, e in targets.values()}:
                self._guard_cross_engine(session, model_id, eng)
                self._guard_untranslatable_sql(specs, eng)
            if channel is not None:
                # Resolución ESTRICTA y única para todo el lote: todas las BDs reciben el mismo
                # tope aunque el puntero se mueva mientras el lote corre. Si no hay una versión
                # inequívoca (409) no se aplica nada: no se cae a "hasta la última".
                resolution = resolve_channel_version(session, model, channel)
                target_version = resolution.version
                resolved_channel = resolution.as_summary()
        finally:
            session.close()

        if not specs:
            raise AppHttpException(
                message="El blueprint no tiene migraciones definidas.",
                status_code=422,
                context={"model_id": model_id},
            )
        self._assert_target_version_exists(specs, target_version, model_id)
        self._verify_integrity(specs)  # una sola vez para todo el lote

        # El set de versiones destructivas depende del MOTOR (por los overrides por motor), y
        # un lote puede ser heterogéneo. Se memoiza por motor para no re-analizar el mismo SQL
        # por cada BD; ``migration_facts.analyze`` ya cachea, esto además evita rearmar el dict.
        #
        # Se memoiza por (motor, versión) y se analiza solo el ALCANCE pedido: lo pendiente de
        # una base es siempre un subconjunto de lo que hay hasta el destino, así que nada de lo
        # que queda fuera puede ejecutarse en este lote. Sin alcance se analiza todo el blueprint.
        _destructive_reasons_cache: dict[tuple[EngineType, str], tuple[str, ...]] = {}

        def destructive_by_engine(
            eng: EngineType, scope: list[MigrationSpec] | None = None
        ) -> dict[str, tuple[str, ...]]:
            scoped_specs = specs if scope is None else scope
            not_analyzed_yet = [
                spec for spec in scoped_specs if (eng, spec.version) not in _destructive_reasons_cache
            ]
            if not_analyzed_yet:
                analyzed = self._destructive_versions(not_analyzed_yet, eng)
                for spec in not_analyzed_yet:
                    _destructive_reasons_cache[(eng, spec.version)] = analyzed.get(
                        spec.version, ()
                    )
            return {
                spec.version: _destructive_reasons_cache[(eng, spec.version)]
                for spec in scoped_specs
                if _destructive_reasons_cache[(eng, spec.version)]
            }

        up_to_target_specs = self.runner.compute_pending(None, specs, target_version)

        # Todo-o-nada ANTES de la primera base: si falla, no se ejecutó nada.
        preflight = self._preflight_destructive_batch(
            model_id,
            slug=slug,
            dbs=dbs,
            targets=targets,
            env_policies=env_policies,
            specs=specs,
            target_version=target_version,
            destructive_by_engine=destructive_by_engine,
            admin=admin,
            dry_run=dry_run,
            force=force,
            confirm_token=confirm_token,
            confirm_phrase=confirm_phrase,
        )

        return _ApplyAllPlan(
            model_id=model_id,
            slug=slug,
            specs=specs,
            total=total,
            matched=matched,
            dbs=dbs,
            omitted=omitted,
            env_policies=env_policies,
            targets=targets,
            environment_id=environment_id,
            target_version=target_version,
            resolved_channel=resolved_channel,
            dry_run=dry_run,
            force=force,
            on_failure=on_failure,
            destructive_by_engine=destructive_by_engine,
            up_to_target_specs=up_to_target_specs,
            preflight=preflight,
        )

    def _apply_all_item(
        self,
        plan: _ApplyAllPlan,
        database: tuple,
        *,
        admin: "dict | Actor | None",
    ) -> _ApplyAllItemOutcome:
        """
        Paso 2 de ``apply_all``: aplica (o simula) UNA base del plan y arma su ítem.

        Nunca lanza: un error de esta base queda en el ítem y el lote sigue con la próxima.
        ``admin`` tiene que ser el ``Actor`` vivo del request (también en el worker del job):
        un dict reconstruido no pasa ``can_at`` y las versiones destructivas se rechazarían.
        """
        db_id, name, server_id, status = database
        model_id = plan.model_id
        slug = plan.slug
        specs = plan.specs
        target_version = plan.target_version
        dry_run = plan.dry_run
        force = plan.force
        on_failure = plan.on_failure
        destructive_by_engine = plan.destructive_by_engine
        up_to_target_specs = plan.up_to_target_specs
        preflight = plan.preflight
        env_policies = plan.env_policies
        targets = plan.targets
        denied = False
        target, engine = targets[server_id]
        item = {
            "managed_database_id": db_id, "database_name": name,
            "server_id": server_id, "applied": [], "ok": False,
        }
        policy = env_policies.get(db_id)
        item["environment_slug"] = policy[1] if policy else None
        try:
            quarantined = status == ProvisionStatus.error
            self._guard_quarantine(db_id, quarantined, force, dry_run)
            if dry_run:
                plan = self._dry_run_plan(
                    db_id, name, server_id, target, slug, specs, target_version,
                    env_policy=policy,
                    destructive_for=lambda pending_specs, eng=engine: destructive_by_engine(
                        eng, pending_specs
                    ),
                    admin=admin,
                    issue_token=False,
                )
                item["ok"] = True
                item["pending_versions"] = plan["pending_versions"]
                item["dry_run"] = True
                item["blocked_by"] = plan["blocked_by"]
                item["destructive_plan"] = plan["destructive_plan"]
            else:
                out = self._run_apply(
                    db_id, db_name=name, server_id=server_id, target=target,
                    engine=engine, slug=slug, specs=specs, model_id=model_id,
                    up_to_version=target_version,
                    was_quarantined=quarantined, admin=admin,
                    on_failure=on_failure,
                    env_blocks_destructive=bool(policy and policy[0]),
                    env_slug=policy[1] if policy else None,
                    destructive_versions=(
                        destructive_by_engine(engine, up_to_target_specs)
                        if policy and policy[0]
                        else None
                    ),
                    batch_confirmed=preflight.confirmed,
                )
                item["ok"] = not out["failed"]
                item["applied"] = out["results"]
                # Paridad con el apply por BD: sin esto, tras un apply masivo no había
                # forma de saber en qué BDs quedaron capturas ni cómo llegar a ellas.
                item["captured_select_count"] = out.get("captured_select_count", 0)
                item["captured_versions"] = out.get("captured_versions", [])
                item["select_results_available"] = out.get(
                    "select_results_available", False
                )
        except AppHttpException as exc:
            item["error"] = exc.message
            # El código estructurado se copia APARTE de la prosa. Este ``except`` se
            # quedaba solo con ``exc.message`` y tiraba el ``public_context`` a la basura,
            # así que para los guards del bucle el canal habitual (``public_context`` de la
            # respuesta HTTP) no existe: la ruta responde 200 con los ítems adentro. Sin
            # este campo el cliente vuelve a matchear prosa con expresiones regulares.
            item["error_code"] = (exc.public_context or {}).get("code")
            if item["error_code"] == ecodes.CODE_DESTRUCTIVE_BLOCKED:
                item["blocked_by"] = list(
                    (exc.public_context or {}).get("blocked_versions") or []
                )
                denied = True
                audit.record(
                    "migration.environment_denied",
                    status="denied",
                    admin=admin,
                    target_type="managed_database",
                    target_id=db_id,
                    server_id=server_id,
                    touched_engine=False,
                    detail=(
                        f"guard=destructive environment={item['environment_slug']} "
                        f"versions={','.join(item['blocked_by'])}"
                    ),
                )
            elif item["error_code"] in (CODE_CONFIRMATION_REQUIRED, CODE_EXECUTOR_FORBIDDEN):
                # Defensa ante carreras: el preflight ya validó el lote, así que llegar acá
                # significa que las aprobaciones o la capacidad cambiaron durante la corrida.
                denied = True
                audit.record(
                    "migration.environment_denied",
                    status="denied",
                    admin=admin,
                    target_type="managed_database",
                    target_id=db_id,
                    server_id=server_id,
                    touched_engine=False,
                    detail=(
                        f"guard=destructive_confirmation code={item['error_code']} "
                        f"environment={item['environment_slug']}"
                    ),
                )
            elif item["error_code"] == ccodes.CODE_UNREVIEWED_CAPTURE:
                # Mismo criterio que el rechazo por entorno: un guard que frena una BD del
                # lote tiene que dejar rastro propio. La ruta responde 200, así que sin esto
                # el rechazo solo vive dentro del JSON de la respuesta.
                item["unreviewed_capture"] = list(
                    (exc.public_context or {}).get("unreviewed_capture") or []
                )
                audit.record(
                    "migration.capture_review_denied",
                    status="denied",
                    admin=admin,
                    target_type="managed_database",
                    target_id=db_id,
                    server_id=server_id,
                    touched_engine=False,
                    detail=(
                        "guard=unreviewed_capture "
                        f"versions={','.join(item['unreviewed_capture'])}"
                    ),
                )
        except Exception as exc:  # noqa: BLE001 — una BD no debe abortar el lote
            logger.warning("apply_all: error inesperado en BD %s: %s", db_id, exc,
                           exc_info=True)
            item["error"] = f"error inesperado: {type(exc).__name__}"
        return _ApplyAllItemOutcome(item=item, denied=denied)

    def _finish_apply_all(
        self,
        plan: _ApplyAllPlan,
        outcomes: list[_ApplyAllItemOutcome],
        *,
        admin: "dict | Actor | None",
    ) -> dict:
        """Paso 3 de ``apply_all``: agrega los omitidos, audita el lote y arma la respuesta."""
        items = [outcome.item for outcome in outcomes]
        denied_count = sum(1 for outcome in outcomes if outcome.denied)
        model_id = plan.model_id
        omitted = plan.omitted
        dbs = plan.dbs
        total = plan.total
        environment_id = plan.environment_id
        target_version = plan.target_version
        resolved_channel = plan.resolved_channel
        dry_run = plan.dry_run

        # Ítems omitidos por la capa 2: SOLO el id que mandó el cliente. Sin nombre, servidor ni
        # entorno, y sin tocar nada: son indistinguibles de un "no podés" genérico.
        items.extend(
            {"managed_database_id": i, "ok": False, "error_code": CODE_FORBIDDEN}
            for i in omitted
        )

        audit.record(
            "migration.apply_all", admin=admin, target_type="database_model",
            target_id=model_id, touched_engine=True,
            detail=f"{len(dbs)}/{total} BDs procesadas"
            + (f" (entorno {environment_id})" if environment_id is not None else "")
            + (f" hasta {target_version}" if target_version is not None else "")
            + (f" canal={resolved_channel['slug']}" if resolved_channel is not None else "")
            + (f" — {denied_count} denegadas por entorno" if denied_count else "")
            + (f" — {len(omitted)} omitidas por scope" if omitted else "")
            + (" (dry-run)" if dry_run else ""),
        )
        return {
            "model_id": model_id,
            "total_databases": total,
            "matched_databases": plan.matched,
            "processed": len(dbs),
            "target_version": target_version,
            "resolved_channel": resolved_channel,
            "destructive_confirmation": plan.preflight.confirmation,
            "results": items,
        }

    # ------------------------------------------------------------------ #
    # Historial (lectura)                                                 #
    # ------------------------------------------------------------------ #
    def history(self, db_id: int, *, limit: int, offset: int) -> tuple[list[dict], int]:
        """Historial de aplicaciones de migraciones de una BD (más reciente primero)."""
        session = self._session()
        try:
            if session.get(ManagedDatabase, db_id) is None:
                raise AppHttpException(
                    message="Base de datos gestionada no encontrada.",
                    status_code=404,
                    context={"managed_database_id": db_id},
                )
            q = (
                session.query(DatabaseMigrationHistory, ModelMigration.version)
                .outerjoin(
                    ModelMigration,
                    ModelMigration.id == DatabaseMigrationHistory.model_migration_id,
                )
                .filter(DatabaseMigrationHistory.managed_database_id == db_id)
            )
            total = q.count()
            rows = (
                q.order_by(
                    DatabaseMigrationHistory.applied_at.desc(),
                    DatabaseMigrationHistory.id.desc(),
                )
                .limit(limit)
                .offset(offset)
                .all()
            )
            items = []
            for h, version in rows:
                # Saneado al LEER, no solo al escribir: las filas previas a
                # ``engine_error_catalog`` guardaron el texto crudo del motor (con valores de
                # filas) y no se reescriben con una migración. ``from_text`` es idempotente,
                # así que sobre una fila ya saneada no cambia nada.
                pub = engine_error_catalog.from_text(h.error)
                items.append({
                    "id": h.id,
                    "managed_database_id": h.managed_database_id,
                    "model_migration_id": h.model_migration_id,
                    # La CONGELADA gana sobre la del join: el join resuelve la versión
                    # ACTUAL, que un renumerado pudo haber movido después del evento. El
                    # fallback cubre las filas previas a la columna.
                    "version": h.applied_version or version,
                    "applied_at": h.applied_at,
                    "status": h.status.value if hasattr(h.status, "value") else h.status,
                    "error": pub.message if pub else None,
                    "error_code": pub.code if pub else None,
                    "execution_ms": h.execution_ms,
                    "direction": h.direction,
                    "applied_checksum": h.applied_checksum,
                    "actor_type": h.actor_type,
                    "actor_id": h.actor_id,
                    "actor_username": h.actor_username,
                    "request_id": h.request_id,
                })
            return items, total
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    # Persistencia de resultados                                          #
    # ------------------------------------------------------------------ #
    def _record_history(
        self,
        db_id: int,
        results: list[MigrationResult],
        *,
        direction: str,
        specs: "list[MigrationSpec] | None" = None,
        admin: "dict | Actor | None" = None,
    ) -> None:
        """Escribe el historial del intento. ``direction`` es obligatorio a propósito.

        Este método se llama desde ``apply`` y desde ``rollback``, y durante mucho tiempo no
        registró de cuál: una fila ``applied`` de un rollback quedaba indistinguible de un
        apply. Hacer el parámetro OBLIGATORIO —y no un default ``"up"``— es lo que impide que
        un camino nuevo vuelva a escribir historial sin dirección y reintroduzca el agujero en
        silencio.

        ``applied_version`` y ``applied_checksum`` se congelan acá: la FK a ``model_migrations``
        resuelve la versión ACTUAL (que un renumerado mueve) y el checksum VIGENTE (que una
        edición recalcula), así que ninguno de los dos sirve para saber qué corrió realmente.
        """
        if not results:
            return
        checksums = {s.id: s.checksum for s in (specs or [])}
        actor_id, actor_username = identity_of(admin)
        actor_type = actor_type_of(admin)
        try:
            request_id = current_http_identifier.get()
        except LookupError:
            request_id = None
        session = self._session()
        try:
            for r in results:
                session.add(
                    DatabaseMigrationHistory(
                        managed_database_id=db_id,
                        model_migration_id=r.migration_id,
                        applied_at=r.applied_at,
                        status=MigrationStatus(r.status),
                        error=r.error,
                        execution_ms=r.execution_ms,
                        direction=direction,
                        applied_version=r.version,
                        applied_checksum=checksums.get(r.migration_id),
                        actor_type=actor_type,
                        actor_id=actor_id,
                        actor_username=actor_username,
                        request_id=request_id,
                    )
                )
            session.commit()
        finally:
            session.close()

    def _mirror_history(
        self,
        target,
        db_name: str,
        engine: EngineType,
        *,
        model_id: int,
        slug: str,
        results: list[MigrationResult],
        direction: str,
        specs: "list[MigrationSpec] | None" = None,
        admin: "dict | Actor | None" = None,
    ) -> None:
        """Replica el historial DENTRO de la BD destino (``_datum_migrations``). Fail-open.

        Es el espejo, no la autoridad: ``_record_history`` ya escribió la fila que manda, en
        la BD del gateway. Esto existe para que la base se explique a sí misma cuando el
        gateway no está — un backup restaurado en otro lado, o la base entregada al cliente.

        **No propaga nada.** ``migration_mirror.record`` ya es fail-open por dentro; acá se
        envuelve además la apertura de la conexión, que ocurre fuera de él. Un espejo que
        puede tumbar un apply deja de ser un espejo.
        """
        if not results:
            return
        from app.core.environments import MIGRATION_MIRROR_ENABLED

        if not MIGRATION_MIRROR_ENABLED:
            return
        from app.core.remote_engine import database_connection
        from app.services.db_admin import migration_mirror

        checksums = {s.id: s.checksum for s in (specs or [])}
        _, actor_username = identity_of(admin)
        try:
            request_id = current_http_identifier.get()
        except LookupError:
            request_id = None
        entries = [
            {
                "version": r.version,
                "direction": direction,
                "status": r.status,
                "checksum": checksums.get(r.migration_id),
                "applied_at": r.applied_at,
                "execution_ms": r.execution_ms,
                "actor": actor_username,
                "request_id": request_id,
            }
            for r in results
        ]
        try:
            with database_connection(target, db_name) as conn:
                # AUTOCOMMIT explícito: esto corre DESPUÉS de la migración, fuera de su
                # transacción, y no debe quedar colgado de nada que el runner haya dejado
                # abierto.
                migration_mirror.record(
                    conn.execution_options(isolation_level="AUTOCOMMIT"),
                    engine,
                    model_id=model_id,
                    blueprint_slug=slug,
                    entries=entries,
                )
        except Exception:  # noqa: BLE001 — fail-open, ver el docstring
            logger.exception(
                "espejo de migraciones: no se pudo abrir la conexión a %s. La migración NO "
                "se ve afectada.",
                db_name,
            )

    def _sync_model_version_from_engine(
        self, db_id: int, target, db_name: str, slug: str
    ) -> None:
        """
        Sincroniza model_version releyendo la FUENTE DE VERDAD: la tabla de versión
        que Alembic mantiene dentro de la BD destino (no la contabilidad local).
        """
        current = self.runner.get_current_version(target, db_name, slug)
        self._set_model_version(db_id, current)

    def _set_model_version(self, db_id: int, version: str | None) -> None:
        session = self._session()
        try:
            md = session.get(ManagedDatabase, db_id)
            if md is not None:
                md.model_version = version
                session.commit()
                # El mínimo del entorno puede haber cambiado: ver ``channel_pointer_sync``.
                sync_automatic_pointers_safely(session, md.model_id)
        finally:
            session.close()

    def _set_quarantine(
        self, db_id: int, failed: bool, results: list[MigrationResult]
    ) -> None:
        """
        ROB1 — marca/limpia la cuarentena de la BD según el desenlace del apply:
        - failed → status=error + nota con la versión que falló (posible estado parcial).
        - éxito tras haber estado en error → vuelve a active y limpia la nota.
        """
        session = self._session()
        try:
            md = session.get(ManagedDatabase, db_id)
            if md is None:
                return
            if failed:
                bad = next((r for r in results if r.status == "failed"), None)
                md.status = ProvisionStatus.error
                md.notes = (
                    f"Migración {bad.version if bad else '?'} falló; posible estado "
                    f"parcial. Inspeccione y reintente con force=true."
                )
                session.commit()
            elif md.status == ProvisionStatus.error:
                md.status = ProvisionStatus.active
                md.notes = None
                session.commit()
        finally:
            session.close()

    @staticmethod
    def _result_dict(r: MigrationResult) -> dict:
        return {
            "migration_id": r.migration_id,
            "version": r.version,
            "status": r.status,
            "error": r.error,
            "error_code": r.error_code,
            "execution_ms": r.execution_ms,
            # Checkpoint por sentencia: para que el frontend distinga un resume de un
            # intento desde cero, y sepa en qué sentencia murió (sin exponer SQL crudo).
            "resumed": r.resumed,
            "resumed_from_statement": r.resumed_from_statement,
            "statement_total": r.statement_total,
            "failed_at_statement_index": r.failed_at_statement_index,
        }
