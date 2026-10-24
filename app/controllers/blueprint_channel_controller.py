"""
Controller de los PUNTEROS de canal de un blueprint: promover, retroceder y su historial.

Opera solo sobre la BD de metadatos del gateway: mover un puntero NO toca ningún motor destino.
Lo que cambia es qué versión sirve el canal a quien lo consuma.

Reglas que este archivo tiene que preservar:

1. **Se bloquea la fila del ``DatabaseModel`` antes de leer el puntero.** Es el mismo lock que
   toma ``ModelMigrationController._apply_renumber``: serializa una promoción contra el borrado
   de la versión a la que apunta. Sin él, una promoción podría apuntar a una fila que se borra
   en la misma ventana y dejar un puntero colgando.
2. **Solo versiones REVISADAS.** Un canal es lo que consumen las integraciones; una versión sin
   revisar (un baseline de snapshot, una captura de SELECT) no puede salir por ahí.
3. **Retroceder exige repetir el slug del canal (``confirm_slug``).** Mover ``stable`` hacia
   atrás es una decisión de liberación, no un clic; el gesto es el mismo que borrar un canal.
4. **Mismo puntero = no-op.** Responder 200 ``changed=false`` sin evento mantiene la bitácora
   limpia ante un reintento del cliente.
5. **El puntero tiene que pertenecer al blueprint.** La FK apunta a ``model_migrations.id`` a
   secas (una FK compuesta es imposible con ``SET NULL`` sobre una columna NOT NULL), así que
   la coherencia ``migración.model_id == puntero.database_model_id`` la hace cumplir esta capa.

RESOLVER (``resolve_version``)
------------------------------
Es el ÚNICO lugar que traduce "canal + blueprint" a una versión. Dos usos con garantías
opuestas, y por eso hay dos modos:

- ``strict=True`` (aprovisionar, apply-all, integración): lee en VIVO y FALLA CERRADO con 409
  ante cualquier cosa que no sea una versión inequívoca. Nunca usa la caché del inventario:
  decidir qué se aplica sobre un dato posiblemente viejo es justo el error que el canal evita.
- ``strict=False`` (pantalla de lectura): lee en vivo pero NO lanza; devuelve ``resolved=false``
  con el código, para que el operador vea POR QUÉ un canal no resuelve en lugar de un error.

El listado por blueprint (``list_channels``) es el tercer caso: usa ``model_version`` de la
caché para no abrir una conexión por base en un GET de lista, y lo rotula con su origen.
"""

from typing import TYPE_CHECKING

from app.controllers.model_migration_controller import ModelMigrationController
from app.core.database import Database
from app.core.environments import DB_HOST, DB_NAME, DB_PASS, DB_PORT, DB_USER
from app.core.logger import get_logger
from app.exceptions import AppHttpException
from app.models.database_model import DatabaseModel
from app.models.enums import ProvisionStatus
from app.models.environment import Environment
from app.models.managed_database import ManagedDatabase
from app.models.model_migration import ModelMigration
from app.models.release_channel import (
    BlueprintChannelPointer,
    BlueprintChannelPointerEvent,
    ReleaseChannel,
)
from app.services import audit
from app.services import release_channel_catalog as channel_codes
from app.services import channel_resolution as resolution_codes
from app.services.blueprint_channel_pointers import build_pointer_event
from app.services.channel_resolution import (
    ChannelResolution,
    DatabaseVersionState,
    evaluate_derived,
)
from app.services.db_admin.migration_integrity import version_sort_key

if TYPE_CHECKING:
    from app.core.actor import Actor

logger = get_logger(__name__)


class BlueprintChannelController:
    def __init__(self):
        self.db = Database(DB_NAME, DB_USER, DB_PASS, DB_HOST, DB_PORT)

    def _session(self):
        return self.db.get_declarative_base_session()

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _model_or_404(session, model_id: int, *, for_update: bool = False) -> DatabaseModel:
        query = session.query(DatabaseModel).filter(DatabaseModel.id == model_id)
        if for_update:
            # SQLite ignora FOR UPDATE: en los tests no excluye, pero en MySQL/PostgreSQL sí.
            query = query.with_for_update()
        model = query.first()
        if model is None:
            raise AppHttpException(
                message="Blueprint no encontrado.",
                status_code=404,
                context={"model_id": model_id},
            )
        return model

    @staticmethod
    def _channel_by_slug_or_404(session, channel_slug: str) -> ReleaseChannel:
        """
        El slug se compara en Python: MySQL/MariaDB comparan case-insensitive y PostgreSQL no,
        y el slug es la identidad que el catálogo trata como exacta.
        """
        normalized_slug = channel_slug.strip().lower()
        candidates = session.query(ReleaseChannel).filter(ReleaseChannel.slug == normalized_slug).all()
        for candidate in candidates:
            if candidate.slug == normalized_slug:
                return candidate
        raise AppHttpException(
            message="Canal de liberación no encontrado.",
            status_code=404,
            public_context={"code": channel_codes.CODE_NOT_FOUND},
            context={"channel_slug": channel_slug},
        )

    @staticmethod
    def _target_migration(
        session, model_id: int, *, version: str | None, model_migration_id: int | None
    ) -> ModelMigration:
        if model_migration_id is not None:
            migration = session.get(ModelMigration, model_migration_id)
            if migration is None:
                raise AppHttpException(
                    message="La versión indicada no existe.",
                    status_code=422,
                    public_context={"code": channel_codes.CODE_VERSION_NOT_FOUND},
                    context={"model_migration_id": model_migration_id},
                )
            if migration.model_id != model_id:
                raise AppHttpException(
                    message="La versión indicada pertenece a otro blueprint.",
                    status_code=422,
                    public_context={"code": channel_codes.CODE_VERSION_NOT_IN_BLUEPRINT},
                    context={"model_id": model_id, "model_migration_id": model_migration_id},
                )
            return migration

        migration = (
            session.query(ModelMigration)
            .filter(ModelMigration.model_id == model_id, ModelMigration.version == version)
            .first()
        )
        if migration is None:
            raise AppHttpException(
                message=f"El blueprint no tiene la versión {version}.",
                status_code=422,
                public_context={"code": channel_codes.CODE_VERSION_NOT_FOUND},
                context={"model_id": model_id, "version": version},
            )
        return migration

    @staticmethod
    def _current_migration(
        session, pointer: BlueprintChannelPointer | None, model_id: int
    ) -> ModelMigration | None:
        """
        La versión a la que apunta hoy el puntero, o ``None`` si no hay puntero o está en NULL.

        Una fila apuntada que no existe o es de otro blueprint es un estado que ningún camino
        legítimo produce: se corta con 409 en vez de decidir "adelante o atrás" contra datos
        que no se pueden creer.
        """
        if pointer is None or pointer.model_migration_id is None:
            return None
        current = session.get(ModelMigration, pointer.model_migration_id)
        if current is None or current.model_id != model_id:
            raise AppHttpException(
                message="El puntero del canal apunta a una versión que no pertenece al blueprint.",
                status_code=409,
                public_context={"code": channel_codes.CODE_POINTER_INCONSISTENT},
                context={
                    "model_id": model_id,
                    "release_channel_id": pointer.release_channel_id,
                    "model_migration_id": pointer.model_migration_id,
                },
            )
        return current

    # ------------------------------------------------------------------ #
    # Promover                                                           #
    # ------------------------------------------------------------------ #
    def promote(
        self,
        model_id: int,
        channel_slug: str,
        *,
        version: str | None = None,
        model_migration_id: int | None = None,
        note: str | None = None,
        confirm_slug: str | None = None,
        admin: "dict | Actor | None" = None,
    ) -> dict:
        session = self._session()
        try:
            self._model_or_404(session, model_id, for_update=True)
            channel = self._channel_by_slug_or_404(session, channel_slug)

            if channel.resolution_mode != channel_codes.MODE_MANUAL:
                raise AppHttpException(
                    message=(
                        f"El canal '{channel.slug}' es '{channel.resolution_mode}': su versión "
                        "se calcula sola y no se promueve a mano."
                    ),
                    status_code=409,
                    public_context={
                        "code": channel_codes.CODE_NOT_MANUAL,
                        "resolution_mode": channel.resolution_mode,
                    },
                    context={"release_channel_id": channel.id},
                )

            target = self._target_migration(
                session, model_id, version=version, model_migration_id=model_migration_id
            )
            if not target.reviewed:
                raise AppHttpException(
                    message=(
                        f"La versión {target.version} no está revisada: solo se promueven "
                        "versiones aprobadas (PATCH reviewed=true)."
                    ),
                    status_code=409,
                    public_context={
                        "code": channel_codes.CODE_VERSION_UNREVIEWED,
                        "version": target.version,
                    },
                    context={"model_id": model_id, "model_migration_id": target.id},
                )

            pointer = session.get(
                BlueprintChannelPointer,
                {"database_model_id": model_id, "release_channel_id": channel.id},
            )
            current = self._current_migration(session, pointer, model_id)

            if current is not None and current.id == target.id:
                return {
                    "channel_slug": channel.slug,
                    "changed": False,
                    "event_type": None,
                    "from_version": current.version,
                    "to_version": target.version,
                    "model_migration_id": target.id,
                }

            is_backward = current is not None and version_sort_key(target.version) < version_sort_key(
                current.version
            )
            if is_backward:
                typed_slug = (confirm_slug or "").strip().lower()
                if typed_slug != channel.slug:
                    raise AppHttpException(
                        message=(
                            f"Mover '{channel.slug}' de {current.version} a {target.version} "
                            f"es un retroceso. Repetí el slug '{channel.slug}' en "
                            "'confirm_slug' para confirmarlo."
                        ),
                        status_code=409,
                        public_context={
                            "code": channel_codes.CODE_PROMOTE_BACKWARD_REQUIRES_CONFIRM,
                            "expected_slug": channel.slug,
                            "from_version": current.version,
                            "to_version": target.version,
                        },
                        context={"model_id": model_id, "release_channel_id": channel.id},
                    )

            if pointer is None:
                pointer = BlueprintChannelPointer(
                    database_model_id=model_id, release_channel_id=channel.id
                )
                session.add(pointer)
            pointer.model_migration_id = target.id

            event_type = (
                channel_codes.EVENT_DEMOTE if is_backward else channel_codes.EVENT_PROMOTE
            )
            session.add(
                build_pointer_event(
                    database_model_id=model_id,
                    release_channel_id=channel.id,
                    event_type=event_type,
                    from_migration=current,
                    to_migration=target,
                    note=note,
                    actor=admin,
                )
            )
            session.commit()

            from_label = current.version if current is not None else None
            audit.record(
                f"release_channel.{event_type}",
                admin=admin,
                target_type="database_model",
                target_id=model_id,
                touched_engine=False,
                detail=(
                    f"canal={channel.slug} {from_label or 'sin versión'}→{target.version}"
                ),
            )
            return {
                "channel_slug": channel.slug,
                "changed": True,
                "event_type": event_type,
                "from_version": from_label,
                "to_version": target.version,
                "model_migration_id": target.id,
            }
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    # Historial                                                          #
    # ------------------------------------------------------------------ #
    def history(
        self, model_id: int, channel_slug: str, *, limit: int, offset: int
    ) -> tuple[list[dict], int]:
        """
        Eventos del puntero, del MÁS RECIENTE al más antiguo (la pregunta habitual es "qué
        cambió hace poco"). Incluye los movimientos automáticos por borrado de versión.
        """
        session = self._session()
        try:
            self._model_or_404(session, model_id)
            channel = self._channel_by_slug_or_404(session, channel_slug)
            query = session.query(BlueprintChannelPointerEvent).filter(
                BlueprintChannelPointerEvent.database_model_id == model_id,
                BlueprintChannelPointerEvent.release_channel_id == channel.id,
            )
            total = query.count()
            # (id desc): el id es monótono y único, así que el orden es estable.
            rows = (
                query.order_by(BlueprintChannelPointerEvent.id.desc())
                .limit(limit)
                .offset(offset)
                .all()
            )
            items = [
                {
                    "id": row.id,
                    "event_type": row.event_type,
                    "from_migration_id": row.from_migration_id,
                    "to_migration_id": row.to_migration_id,
                    "from_version": row.from_version,
                    "to_version": row.to_version,
                    "note": row.note,
                    "actor_admin_id": row.actor_admin_id,
                    "actor_username": row.actor_username,
                    "actor_type": row.actor_type,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
            return items, total
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    # Resolver                                                           #
    # ------------------------------------------------------------------ #
    def resolve_version(
        self, session, model: DatabaseModel, channel_slug: str, *, strict: bool
    ) -> ChannelResolution:
        """
        Resuelve el canal a una versión del blueprint leyendo en VIVO.

        ``strict=True`` lanza 409 (``release_channel.*``) si no hay una versión inequívoca;
        ``strict=False`` devuelve el mismo resultado con ``version=None`` y el código, y añade
        la referencia ``derived`` para los canales con entorno. Un canal inexistente es 404 en
        los dos modos.
        """
        channel = self._channel_by_slug_or_404(session, channel_slug)
        resolution = self._resolve_channel(
            session, model, channel, live=True, include_reference=not strict
        )
        if strict:
            self._raise_if_unresolved(resolution, model.id, channel.id)
        return resolution

    @staticmethod
    def _raise_if_unresolved(resolution: ChannelResolution, model_id: int, channel_id: int) -> None:
        if resolution.resolved:
            return
        raise AppHttpException(
            message=resolution.error_message or "El canal no resuelve a una versión.",
            status_code=409,
            public_context={"code": resolution.error_code, **resolution.error_context},
            context={"model_id": model_id, "release_channel_id": channel_id},
        )

    @staticmethod
    def _environment_slug(session, environment_id: int | None) -> str | None:
        if environment_id is None:
            return None
        return session.query(Environment.slug).filter(Environment.id == environment_id).scalar()

    @staticmethod
    def _fail(
        resolution: ChannelResolution, code: str, message: str, **public_extras
    ) -> ChannelResolution:
        resolution.error_code = code
        resolution.error_message = message
        resolution.error_context = public_extras
        return resolution

    def _resolve_channel(
        self,
        session,
        model: DatabaseModel,
        channel: ReleaseChannel,
        *,
        live: bool,
        include_reference: bool,
    ) -> ChannelResolution:
        """Nunca lanza por un resultado de negocio: el llamador decide si lo eleva a 409."""
        resolution = ChannelResolution(
            channel_slug=channel.slug,
            channel_name=channel.name,
            mode=channel.resolution_mode,
            environment_slug=self._environment_slug(session, channel.environment_id),
        )

        if channel.resolution_mode == channel_codes.MODE_MANUAL:
            self._resolve_manual(session, model, channel, resolution)
        elif channel.resolution_mode == channel_codes.MODE_LATEST:
            self._resolve_latest(session, model, resolution)
        else:
            self._resolve_derived(session, model, channel, resolution, live=live)

        wants_reference = (
            include_reference
            and channel.resolution_mode != channel_codes.MODE_DERIVED
            and channel.environment_id is not None
        )
        if wants_reference:
            # Objeto aparte: la referencia nunca pisa el valor que el canal sirve de verdad.
            reference = ChannelResolution(
                channel_slug=channel.slug,
                channel_name=channel.name,
                mode=channel_codes.MODE_DERIVED,
                environment_slug=resolution.environment_slug,
            )
            self._resolve_derived(session, model, channel, reference, live=live)
            resolution.derived_reference = reference
        return resolution

    def _resolve_manual(
        self, session, model: DatabaseModel, channel: ReleaseChannel, resolution: ChannelResolution
    ) -> None:
        pointer = session.get(
            BlueprintChannelPointer,
            {"database_model_id": model.id, "release_channel_id": channel.id},
        )
        if pointer is None or pointer.model_migration_id is None:
            self._fail(
                resolution,
                channel_codes.CODE_NO_RELEASED_VERSION,
                f"El canal '{channel.slug}' todavía no tiene una versión liberada para este "
                "blueprint.",
            )
            return
        pointed = session.get(ModelMigration, pointer.model_migration_id)
        if pointed is None or pointed.model_id != model.id:
            self._fail(
                resolution,
                channel_codes.CODE_POINTER_INCONSISTENT,
                "El puntero del canal apunta a una versión que no pertenece al blueprint.",
            )
            return
        resolution.version = pointed.version
        resolution.model_migration_id = pointed.id
        resolution.source = resolution_codes.SOURCE_POINTER

    def _resolve_latest(
        self, session, model: DatabaseModel, resolution: ChannelResolution
    ) -> None:
        """
        La punta INCLUYE versiones sin revisar. Es el comportamiento que ya tiene el
        aprovisionamiento sin ``target_version``, y las guardas de "revisada" siguen aplicando
        en quien consume: el canal no las reemplaza ni las relaja.
        """
        migrations = session.query(ModelMigration).filter(ModelMigration.model_id == model.id).all()
        if not migrations:
            self._fail(
                resolution,
                channel_codes.CODE_NO_RELEASED_VERSION,
                "El blueprint todavía no tiene versiones.",
            )
            return
        tip = max(migrations, key=lambda row: version_sort_key(row.version))
        resolution.version = tip.version
        resolution.model_migration_id = tip.id
        resolution.source = resolution_codes.SOURCE_TIP

    def _resolve_derived(
        self,
        session,
        model: DatabaseModel,
        channel: ReleaseChannel,
        resolution: ChannelResolution,
        *,
        live: bool,
    ) -> None:
        if channel.environment_id is None:
            self._fail(
                resolution,
                channel_codes.CODE_DERIVED_NO_SCOPE,
                f"El canal '{channel.slug}' no tiene un entorno enlazado: no hay bases que mirar.",
            )
            return

        migration_id_by_version = {
            row.version: row.id
            for row in session.query(ModelMigration).filter(ModelMigration.model_id == model.id)
        }
        if live:
            states = self._live_states(session, model, channel.environment_id)
            source = resolution_codes.SOURCE_LIVE
        else:
            states = self._cached_states(session, model.id, channel.environment_id)
            source = resolution_codes.SOURCE_INVENTORY_CACHE

        outcome = evaluate_derived(states, migration_id_by_version)
        resolution.databases = outcome.contributions
        resolution.source = source

        if outcome.error_code == channel_codes.CODE_DERIVED_UNREADABLE:
            self._fail(
                resolution,
                channel_codes.CODE_DERIVED_UNREADABLE,
                "No se pudo determinar la versión de alguna base del entorno: no se calcula un "
                "mínimo parcial. Indicá una versión explícita.",
                managed_database_ids=outcome.unreadable_database_ids,
            )
            return
        if outcome.error_code == channel_codes.CODE_DERIVED_NO_SCOPE:
            self._fail(
                resolution,
                channel_codes.CODE_DERIVED_NO_SCOPE,
                f"Ninguna base del blueprint en el entorno del canal '{channel.slug}' está en "
                "una versión: no hay mínimo que calcular.",
            )
            return
        resolution.version = outcome.version
        resolution.model_migration_id = outcome.model_migration_id

    @staticmethod
    def _quarantined_database_ids(session, model_id: int, environment_id: int) -> set[int]:
        """
        Cuarentena = ``status=error`` sobre una base que sí existe: la versión que muestra es la
        de una migración fallida a medias, no un estado liberado.
        """
        rows = (
            session.query(ManagedDatabase.id)
            .filter(
                ManagedDatabase.model_id == model_id,
                ManagedDatabase.environment_id == environment_id,
                ManagedDatabase.status == ProvisionStatus.error,
            )
            .all()
        )
        return {row[0] for row in rows}

    @classmethod
    def _live_states(
        cls, session, model: DatabaseModel, environment_id: int
    ) -> list[DatabaseVersionState]:
        # Las bases en cuarentena no cuentan para el mínimo: se le pide al lector que no abra
        # conexión a ellas, porque su versión se descartaría igual.
        quarantined_ids = cls._quarantined_database_ids(session, model.id, environment_id)
        live_states = ModelMigrationController()._blueprint_database_states(
            session, model, environment_id=environment_id, skip_live_read_ids=quarantined_ids
        )
        return [
            DatabaseVersionState(
                managed_database_id=state["managed_database_id"],
                database_name=state["database_name"],
                current_version=state["current_version"],
                database_exists=state["database_exists"],
                readable=state["readable"],
                quarantined=state["managed_database_id"] in quarantined_ids,
            )
            for state in live_states
        ]

    @staticmethod
    def _cached_states(
        session, model_id: int, environment_id: int
    ) -> list[DatabaseVersionState]:
        """
        Estados a partir de ``ManagedDatabase.model_version``, sin tocar ningún motor. Solo
        sirve para referencia: la caché no distingue "la base ya no existe" de "existe", así
        que supone que existe y deja que ``at_base`` (sin versión) filtre las nunca posicionadas.
        """
        rows = (
            session.query(ManagedDatabase)
            .filter(
                ManagedDatabase.model_id == model_id,
                ManagedDatabase.environment_id == environment_id,
            )
            .order_by(ManagedDatabase.id)
            .all()
        )
        return [
            DatabaseVersionState(
                managed_database_id=row.id,
                database_name=row.name,
                current_version=row.model_version,
                database_exists=True,
                readable=True,
                quarantined=row.status == ProvisionStatus.error,
            )
            for row in rows
        ]

    # ------------------------------------------------------------------ #
    # Lecturas para la pantalla                                          #
    # ------------------------------------------------------------------ #
    def list_channels(self, model_id: int) -> list[ChannelResolution]:
        """
        Todos los canales con la versión que sirven para este blueprint. Usa la CACHÉ del
        inventario para los cálculos ``derived`` (``source=inventory_cache``): un GET de lista
        no abre una conexión por base. El detalle en vivo es ``resolve``.
        """
        session = self._session()
        try:
            model = self._model_or_404(session, model_id)
            channels = session.query(ReleaseChannel).order_by(ReleaseChannel.id.asc()).all()
            return [
                self._resolve_channel(session, model, channel, live=False, include_reference=True)
                for channel in channels
            ]
        finally:
            session.close()

    def resolve(self, model_id: int, channel_slug: str) -> ChannelResolution:
        """Lectura TOLERANTE y EN VIVO de un canal: 200 aunque no resuelva (``resolved=false``)."""
        session = self._session()
        try:
            model = self._model_or_404(session, model_id)
            return self.resolve_version(session, model, channel_slug, strict=False)
        finally:
            session.close()


def resolve_channel_version(session, model: DatabaseModel, channel_slug: str) -> ChannelResolution:
    """
    Punto de entrada ESTRICTO para los consumidores (aprovisionar, apply-all, integración).

    Lee en vivo y lanza 409 ``release_channel.*`` si no hay versión inequívoca; 404 si el canal
    no existe. Quien lo llama debe tratar ``version`` como el único destino y no caer a otro
    valor ante el error: el canal existe justamente para no adivinar.
    """
    return BlueprintChannelController().resolve_version(session, model, channel_slug, strict=True)


def reject_channel_with_target_version(target_version: str | None, channel: str | None) -> None:
    """
    Un destino se pide por versión O por canal, nunca por los dos.

    Aceptar ambos obligaría a elegir en silencio cuál manda, y los dos casos posibles son malos:
    si gana la versión, quien pidió ``channel=stable`` cree haber aplicado lo liberado; si gana
    el canal, quien fijó una versión explícita (la salida de emergencia ante un canal que no
    resuelve) la ve pisada. Se valida en la frontera de cada consumidor, ANTES de tocar el
    motor o resolver nada.
    """
    if target_version is None or channel is None:
        return
    raise AppHttpException(
        message="Indicá 'target_version' o 'channel', no los dos: son destinos excluyentes.",
        status_code=422,
        public_context={"code": channel_codes.CODE_TARGET_CONFLICT},
        context={"target_version": target_version, "channel": channel},
    )
