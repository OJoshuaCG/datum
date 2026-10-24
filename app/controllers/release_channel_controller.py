"""
Controller de CANALES DE LIBERACIÓN de blueprints.

CRUD sobre la BD de metadatos del gateway: NO toca ningún motor destino. El movimiento de los
punteros (promover / retroceder) y la resolución de versión viven en servicios aparte; este
controller solo administra el catálogo de canales.

Reglas que este archivo tiene que preservar:

1. **El borrado de un canal arrastra a mano sus punteros y su bitácora.** Las FK son
   ``ON DELETE CASCADE``, pero los tests corren en SQLite sin ``PRAGMA foreign_keys=ON``, así
   que confiar solo en el motor dejaría huérfanos donde más se ejerce. Ver ``delete_channel``.
2. **Borrar un canal con punteros exige ``confirm_slug``.** Se pierde el historial de qué
   versión sirvió cada blueprint: es irreversible sobre N filas a la vez.
3. **Un canal ``derived`` necesita entorno, evaluado sobre el estado RESULTANTE.** Mirar solo
   el payload deja pasar "cambiar a derived" sobre un canal sin entorno y "desvincular el
   entorno" de un canal ya derived. Ver ``_guard_resulting_state``.
"""

from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.core.database import Database
from app.core.environments import DB_HOST, DB_NAME, DB_PASS, DB_PORT, DB_USER
from app.exceptions import AppHttpException
from app.models.environment import Environment
from app.models.release_channel import (
    BlueprintChannelPointer,
    BlueprintChannelPointerEvent,
    ReleaseChannel,
)
from app.services import audit
from app.services import environment_catalog as environment_codes
from app.services import release_channel_catalog as channel_codes

if TYPE_CHECKING:
    from app.core.actor import Actor


class ReleaseChannelController:
    def __init__(self):
        self.db = Database(DB_NAME, DB_USER, DB_PASS, DB_HOST, DB_PORT)

    def _session(self):
        return self.db.get_declarative_base_session()

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _serialize(channel: ReleaseChannel, *, pointer_count: int = 0) -> dict:
        return {
            "id": channel.id,
            "name": channel.name,
            "slug": channel.slug,
            "resolution_mode": channel.resolution_mode,
            "environment_id": channel.environment_id,
            "pointer_count": pointer_count,
            "created_at": channel.created_at,
            "updated_at": channel.updated_at,
        }

    @staticmethod
    def _get_or_404(session, channel_id: int) -> ReleaseChannel:
        channel = session.get(ReleaseChannel, channel_id)
        if channel is None:
            raise AppHttpException(
                message="Canal de liberación no encontrado.",
                status_code=404,
                public_context={"code": channel_codes.CODE_NOT_FOUND},
                context={"release_channel_id": channel_id},
            )
        return channel

    @staticmethod
    def _pointer_counts_for(session, channel_ids: list[int]) -> dict[int, int]:
        """Punteros por canal en UNA query para toda la página (no una query por fila)."""
        if not channel_ids:
            return {}
        rows = (
            session.query(
                BlueprintChannelPointer.release_channel_id,
                func.count(BlueprintChannelPointer.database_model_id),
            )
            .filter(BlueprintChannelPointer.release_channel_id.in_(channel_ids))
            .group_by(BlueprintChannelPointer.release_channel_id)
            .all()
        )
        return {channel_id: count for channel_id, count in rows}

    @staticmethod
    def _require_existing_environment(session, environment_id: int | None) -> None:
        """
        El entorno enviado tiene que existir. Es un 422 (input inválido), no un 404: el recurso
        de la URL es el canal, y un 404 aquí se leería como "el canal no existe".
        """
        if environment_id is None:
            return
        environment_row = session.query(Environment.id).filter(Environment.id == environment_id).first()
        if environment_row is None:
            raise AppHttpException(
                message="El entorno indicado no existe.",
                status_code=422,
                public_context={"code": environment_codes.CODE_NOT_FOUND},
                context={"environment_id": environment_id},
            )

    @staticmethod
    def _guard_resulting_state(channel: ReleaseChannel) -> None:
        """Valida el estado RESULTANTE: un canal derived sin entorno no tiene de dónde derivar."""
        derived_without_environment = (
            channel.resolution_mode == channel_codes.MODE_DERIVED and channel.environment_id is None
        )
        if derived_without_environment:
            raise AppHttpException(
                message="Un canal en modo 'derived' necesita un entorno enlazado (environment_id).",
                status_code=422,
                public_context={"code": channel_codes.CODE_ENVIRONMENT_REQUIRED},
                context={"release_channel_id": channel.id},
            )

    @staticmethod
    def _raise_duplicate(
        session, *, name: str | None, slug: str | None, exclude_id: int | None = None
    ) -> None:
        """
        409 con un código POR COLUMNA, para que la SPA sepa qué input marcar. Se compara en
        Python (no con ``WHERE``) porque la collation del motor decide si ``Stable`` y
        ``stable`` chocan. El ``IntegrityError`` del llamador cubre la carrera con el INSERT.
        """
        query = session.query(ReleaseChannel.id, ReleaseChannel.name, ReleaseChannel.slug)
        if exclude_id is not None:
            query = query.filter(ReleaseChannel.id != exclude_id)
        for _channel_id, existing_name, existing_slug in query.all():
            if name is not None and existing_name == name:
                raise AppHttpException(
                    message=f"Ya existe un canal con el nombre '{name}'.",
                    status_code=409,
                    public_context={"code": channel_codes.CODE_NAME_TAKEN},
                    context={"name": name},
                )
            if slug is not None and existing_slug == slug:
                raise AppHttpException(
                    message=f"Ya existe un canal con el slug '{slug}'.",
                    status_code=409,
                    public_context={"code": channel_codes.CODE_SLUG_TAKEN},
                    context={"slug": slug},
                )

    # ------------------------------------------------------------------ #
    # Lectura                                                            #
    # ------------------------------------------------------------------ #
    def list_channels(self, *, limit: int, offset: int) -> tuple[list[dict], int]:
        session = self._session()
        try:
            query = session.query(ReleaseChannel)
            total = query.count()
            # (id): el orden de creación; estable porque el id es único.
            rows = query.order_by(ReleaseChannel.id.asc()).limit(limit).offset(offset).all()
            pointer_counts = self._pointer_counts_for(session, [row.id for row in rows])
            return [
                self._serialize(row, pointer_count=pointer_counts.get(row.id, 0)) for row in rows
            ], total
        finally:
            session.close()

    def get_channel(self, channel_id: int) -> dict:
        session = self._session()
        try:
            channel = self._get_or_404(session, channel_id)
            pointer_counts = self._pointer_counts_for(session, [channel.id])
            return self._serialize(channel, pointer_count=pointer_counts.get(channel.id, 0))
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    # Escritura                                                          #
    # ------------------------------------------------------------------ #
    def create_channel(self, data: dict, *, admin: "dict | Actor | None" = None) -> dict:
        session = self._session()
        try:
            self._raise_duplicate(session, name=data["name"], slug=data["slug"])
            self._require_existing_environment(session, data.get("environment_id"))
            channel = ReleaseChannel(**data)
            self._guard_resulting_state(channel)
            session.add(channel)
            session.commit()
            session.refresh(channel)
            audit.record(
                "release_channel.create",
                admin=admin,
                target_type="release_channel",
                target_id=channel.id,
                touched_engine=False,
                detail=(
                    f"slug={channel.slug} mode={channel.resolution_mode} "
                    f"environment_id={channel.environment_id}"
                ),
            )
            return self._serialize(channel, pointer_count=0)
        except IntegrityError:
            # Red de la carrera entre el pre-chequeo y el INSERT: sin esto sale un 500.
            session.rollback()
            raise AppHttpException(
                message="Ya existe un canal con ese nombre o slug.",
                status_code=409,
                public_context={"code": channel_codes.CODE_SLUG_TAKEN},
                context={"name": data.get("name"), "slug": data.get("slug")},
            ) from None
        finally:
            session.close()

    def update_channel(
        self, channel_id: int, data: dict, *, admin: "dict | Actor | None" = None
    ) -> dict:
        session = self._session()
        try:
            channel = self._get_or_404(session, channel_id)
            if "name" in data and data["name"] is not None:
                self._raise_duplicate(session, name=data["name"], slug=None, exclude_id=channel.id)
            if "environment_id" in data:
                self._require_existing_environment(session, data["environment_id"])

            before = {
                "name": channel.name,
                "resolution_mode": channel.resolution_mode,
                "environment_id": channel.environment_id,
            }
            for field in ("name", "resolution_mode", "environment_id"):
                if field not in data:
                    continue
                # ``name`` y ``resolution_mode`` no admiten null: un null explícito es "no
                # cambiar" y no un intento de vaciar una columna NOT NULL.
                if data[field] is None and field != "environment_id":
                    continue
                setattr(channel, field, data[field])
            self._guard_resulting_state(channel)
            session.commit()
            session.refresh(channel)

            after = {
                "name": channel.name,
                "resolution_mode": channel.resolution_mode,
                "environment_id": channel.environment_id,
            }
            changed = {key: (before[key], after[key]) for key in before if before[key] != after[key]}
            changes_detail = " ".join(f"{key}:{old}->{new}" for key, (old, new) in changed.items())
            audit.record(
                "release_channel.update",
                admin=admin,
                target_type="release_channel",
                target_id=channel.id,
                touched_engine=False,
                detail=f"slug={channel.slug} {changes_detail or 'sin cambios efectivos'}",
            )
            pointer_counts = self._pointer_counts_for(session, [channel.id])
            return self._serialize(channel, pointer_count=pointer_counts.get(channel.id, 0))
        except IntegrityError:
            session.rollback()
            raise AppHttpException(
                message="Ya existe un canal con ese nombre.",
                status_code=409,
                public_context={"code": channel_codes.CODE_NAME_TAKEN},
                context={"name": data.get("name")},
            ) from None
        finally:
            session.close()

    def delete_channel(
        self, channel_id: int, *, confirm_slug: str | None = None,
        admin: "dict | Actor | None" = None,
    ) -> None:
        """
        Borra un canal junto con sus punteros y su bitácora.

        Con punteros existentes exige repetir el slug en ``confirm_slug`` (422
        ``release_channel.confirmation_required``): se pierde qué versión servía cada
        blueprint en ese canal, y eso no se reconstruye. Sin punteros no hay nada que perder y
        no se pide confirmación.

        Los punteros y los eventos se borran ACÁ, explícitamente, y no se delegan al
        ``ON DELETE CASCADE``: los tests corren sobre SQLite sin claves foráneas y la
        migración no puede garantizar el orden en todos los motores. Orden: eventos,
        punteros, canal.
        """
        session = self._session()
        try:
            channel = self._get_or_404(session, channel_id)
            pointer_counts = self._pointer_counts_for(session, [channel.id])
            pointer_count = pointer_counts.get(channel.id, 0)
            if pointer_count:
                typed_slug = (confirm_slug or "").strip().lower()
                if typed_slug != channel.slug:
                    raise AppHttpException(
                        message=(
                            f"El canal '{channel.slug}' tiene {pointer_count} puntero(s) de "
                            f"blueprint. Repetí el slug '{channel.slug}' en 'confirm_slug' "
                            "para borrarlo junto con su historial."
                        ),
                        status_code=422,
                        public_context={
                            "code": channel_codes.CODE_CONFIRMATION_REQUIRED,
                            "expected_slug": channel.slug,
                            "pointer_count": pointer_count,
                        },
                        context={"release_channel_id": channel.id},
                    )
            slug = channel.slug
            session.query(BlueprintChannelPointerEvent).filter(
                BlueprintChannelPointerEvent.release_channel_id == channel.id
            ).delete(synchronize_session=False)
            session.query(BlueprintChannelPointer).filter(
                BlueprintChannelPointer.release_channel_id == channel.id
            ).delete(synchronize_session=False)
            session.delete(channel)
            session.commit()
            audit.record(
                "release_channel.delete",
                admin=admin,
                target_type="release_channel",
                target_id=channel_id,
                touched_engine=False,
                detail=f"slug={slug} pointers_deleted={pointer_count}",
            )
        finally:
            session.close()
