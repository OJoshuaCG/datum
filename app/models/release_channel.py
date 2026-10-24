"""
Modelos de los CANALES DE LIBERACIÓN de blueprints.

Un canal (``stable``, ``development``…) es un nombre global; cada blueprint tiene, por canal,
un PUNTERO a una de sus versiones (``model_migrations``). Tres tablas:

* ``release_channels``: el catálogo de canales (política del operador).
* ``blueprint_channel_pointers``: a qué versión apunta cada blueprint en cada canal.
* ``blueprint_channel_pointer_events``: bitácora append-only de los movimientos del puntero.

POR QUÉ ``resolution_mode`` ES UN CHECK Y NO UN ENUM
----------------------------------------------------
Los modos son código del resolver; agregar uno exige un deploy. Un ``ENUM`` de MySQL se altera
con ``ALTER TABLE`` y no existe en SQLite (donde corren los tests), mientras que un ``CHECK``
declarativo se comporta igual en los tres motores.

POR QUÉ EL CHECK "derived necesita entorno" CONVIVE CON ``ondelete=RESTRICT``
-----------------------------------------------------------------------------
MySQL prohíbe un ``CHECK`` sobre una columna que participa de una FK con ``CASCADE`` o
``SET NULL``. La FK al entorno es ``RESTRICT`` a propósito: borrar un entorno enlazado a un
canal no debe dejar un canal ``derived`` huérfano, así que se rechaza (409
``environment.has_release_channels``) en vez de anular el enlace.

POR QUÉ LA BITÁCORA NO TIENE FK A ``model_migrations``
------------------------------------------------------
Es evidencia: tiene que sobrevivir al borrado de la versión que menciona (el evento
``version_deleted`` es justamente el rastro de ese borrado). Por eso ``from_migration_id`` y
``to_migration_id`` son enteros sueltos y las versiones se copian como texto
(``from_version`` / ``to_version``).
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.services.release_channel_catalog import (
    MODE_DERIVED,
    MODE_MANUAL,
    POINTER_EVENT_TYPES,
    RELEASE_CHANNEL_MODES,
)

CHANNEL_NAME_COLUMN_LENGTH = 60
CHANNEL_SLUG_COLUMN_LENGTH = 60
RESOLUTION_MODE_COLUMN_LENGTH = 16
EVENT_TYPE_COLUMN_LENGTH = 16
VERSION_COLUMN_LENGTH = 10
EVENT_NOTE_COLUMN_LENGTH = 500
ACTOR_USERNAME_COLUMN_LENGTH = 128
ACTOR_TYPE_COLUMN_LENGTH = 16


def _quoted_values(values: tuple[str, ...]) -> str:
    """Arma la lista ``'a', 'b'`` de un ``IN (...)`` desde un vocabulario cerrado propio."""
    return ", ".join(f"'{value}'" for value in values)


class ReleaseChannel(Base, TimestampMixin):
    __tablename__ = "release_channels"
    __table_args__ = (
        CheckConstraint(
            f"resolution_mode IN ({_quoted_values(RELEASE_CHANNEL_MODES)})",
            name="resolution_mode",
        ),
        CheckConstraint(
            f"resolution_mode <> '{MODE_DERIVED}' OR environment_id IS NOT NULL",
            name="derived_needs_environment",
        ),
        {
            "comment": (
                "Canales de liberación de blueprints (stable, development...). Política del "
                "operador: el seed solo corre con la tabla vacía"
            )
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="ID único del canal"
    )

    name: Mapped[str] = mapped_column(
        String(CHANNEL_NAME_COLUMN_LENGTH),
        nullable=False,
        unique=True,
        index=True,
        comment="Nombre visible del canal. Único",
    )

    slug: Mapped[str] = mapped_column(
        String(CHANNEL_SLUG_COLUMN_LENGTH),
        nullable=False,
        unique=True,
        index=True,
        comment="Identificador estable del canal que citan las integraciones. Inmutable tras crearlo",
    )

    resolution_mode: Mapped[str] = mapped_column(
        String(RESOLUTION_MODE_COLUMN_LENGTH),
        nullable=False,
        default=MODE_MANUAL,
        server_default=MODE_MANUAL,
        comment=(
            "Cómo se resuelve la versión del canal: manual (puntero movido por una persona), "
            "derived (mínima versión viva en el entorno enlazado) o latest (punta del blueprint)"
        ),
    )

    environment_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("environments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        comment=(
            "Entorno enlazado. Obligatorio en modo derived. RESTRICT: no se puede borrar un "
            "entorno mientras un canal lo use"
        ),
    )

    def __repr__(self) -> str:
        return f"<ReleaseChannel(slug='{self.slug}', mode='{self.resolution_mode}')>"


class BlueprintChannelPointer(Base, TimestampMixin):
    __tablename__ = "blueprint_channel_pointers"
    __table_args__ = (
        {
            "comment": (
                "Puntero de un blueprint en un canal: la versión que ese canal sirve para ese "
                "blueprint"
            )
        },
    )

    database_model_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("database_models.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Blueprint (database_models.id). Si se borra, el puntero se borra",
    )

    release_channel_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("release_channels.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
        comment="Canal. Si se borra, el puntero se borra",
    )

    model_migration_id: Mapped[int | None] = mapped_column(
        Integer,
        # SET NULL y no CASCADE: borrar una versión no debe borrar el puntero, que queda "sin
        # versión" y se reubica en la capa de servicio con su evento de bitácora.
        ForeignKey("model_migrations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Versión apuntada (model_migrations.id). NULL = el canal aún no tiene versión",
    )

    def __repr__(self) -> str:
        return (
            f"<BlueprintChannelPointer(model={self.database_model_id}, "
            f"channel={self.release_channel_id}, migration={self.model_migration_id})>"
        )


class BlueprintChannelPointerEvent(Base):
    __tablename__ = "blueprint_channel_pointer_events"
    __table_args__ = (
        CheckConstraint(
            f"event_type IN ({_quoted_values(POINTER_EVENT_TYPES)})",
            name="event_type",
        ),
        Index(
            "ix_blueprint_channel_pointer_events_model_channel_id",
            "database_model_id",
            "release_channel_id",
            "id",
        ),
        {
            "comment": (
                "Bitácora append-only de los movimientos de un puntero de canal. Sin FK a las "
                "versiones: sobrevive a su borrado"
            )
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="ID único del evento"
    )

    database_model_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("database_models.id", ondelete="CASCADE"),
        nullable=False,
        comment="Blueprint del puntero",
    )

    release_channel_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("release_channels.id", ondelete="CASCADE"),
        nullable=False,
        comment="Canal del puntero",
    )

    event_type: Mapped[str] = mapped_column(
        String(EVENT_TYPE_COLUMN_LENGTH),
        nullable=False,
        comment="promote (avance), demote (retroceso confirmado) o version_deleted (movimiento automático)",
    )

    from_migration_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="model_migrations.id previo. Sin FK: evidencia"
    )
    to_migration_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="model_migrations.id nuevo. Sin FK: evidencia"
    )
    from_version: Mapped[str | None] = mapped_column(
        String(VERSION_COLUMN_LENGTH), nullable=True, comment="Versión previa, copiada como texto"
    )
    to_version: Mapped[str | None] = mapped_column(
        String(VERSION_COLUMN_LENGTH), nullable=True, comment="Versión nueva, copiada como texto"
    )

    note: Mapped[str | None] = mapped_column(
        String(EVENT_NOTE_COLUMN_LENGTH), nullable=True, comment="Motivo libre del movimiento"
    )

    actor_admin_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="Usuario del gateway que movió el puntero. Sin FK"
    )
    actor_username: Mapped[str] = mapped_column(
        String(ACTOR_USERNAME_COLUMN_LENGTH),
        nullable=False,
        comment="Nombre del actor al momento del evento (copiado: sobrevive al usuario)",
    )
    actor_type: Mapped[str] = mapped_column(
        String(ACTOR_TYPE_COLUMN_LENGTH),
        nullable=False,
        comment="Tipo de actor: sesión de usuario, token de integración o sistema",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        comment="Momento del evento (UTC)",
    )

    def __repr__(self) -> str:
        return (
            f"<BlueprintChannelPointerEvent(model={self.database_model_id}, "
            f"channel={self.release_channel_id}, type='{self.event_type}')>"
        )
