"""
Operaciones compartidas sobre los PUNTEROS de canal de un blueprint.

Las usan dos controllers que no pueden importarse entre sí: el que PROMUEVE un puntero
(``BlueprintChannelController``) y el que BORRA la versión a la que apunta
(``ModelMigrationController``). Tener la regla en un solo lugar es lo que evita que "a qué versión
cae un puntero" tenga dos respuestas.

REGLA DEL DESTINO DE UN PUNTERO HUÉRFANO
----------------------------------------
Si se borra la versión apuntada, el puntero pasa a la versión REVISADA más cercana por debajo, o a
NULL si no hay ninguna. No a "la anterior" a secas: el invariante del catálogo es que un puntero
solo apunta a versiones revisadas (``promote`` lo exige), y caer sobre una versión sin revisar lo
rompería en silencio, justo en el canal que sirve a producción.
"""

from app.core.actor import actor_type_of, identity_of
from app.models.model_migration import ModelMigration
from app.models.release_channel import (
    BlueprintChannelPointer,
    BlueprintChannelPointerEvent,
    ReleaseChannel,
)
from app.services.db_admin.migration_integrity import version_sort_key
from app.services.release_channel_catalog import MODE_MANUAL

#: Nombre que se copia al evento cuando no hay un actor identificable. ``actor_username`` es
#: NOT NULL porque la bitácora es evidencia: un evento sin autor no debe poder existir.
SYSTEM_ACTOR_USERNAME = "sistema"


def nearest_lower_reviewed_migration(
    session, model_id: int, deleted_version: str
) -> ModelMigration | None:
    """
    La versión revisada más cercana POR DEBAJO de ``deleted_version`` en el blueprint, o ``None``.

    El orden es el numérico del módulo (``version_sort_key``), no el lexicográfico. La versión
    que se borra queda excluida por la desigualdad estricta, así que no hace falta sacarla de la
    lista.
    """
    cut = version_sort_key(deleted_version)
    candidates = (
        session.query(ModelMigration)
        .filter(ModelMigration.model_id == model_id, ModelMigration.reviewed.is_(True))
        .all()
    )
    lower_candidates = [row for row in candidates if version_sort_key(row.version) < cut]
    if not lower_candidates:
        return None
    return max(lower_candidates, key=lambda row: version_sort_key(row.version))


def plan_channel_moves(session, migration: ModelMigration) -> list[dict]:
    """
    Qué punteros MANUALES apuntan a ``migration`` y adónde se moverían si se borrara. Solo lectura.

    Los canales ``latest`` y ``derived`` quedan afuera a propósito: su puntero lo recalcula
    ``channel_pointer_sync`` solo, así que pedirle a la persona que confirme un movimiento que
    nadie decide sería ruido (borrar la punta de cualquier blueprint exigiría un token).

    Ordenado por id de canal: el resultado entra en la huella del ``confirm_token`` y tiene que
    ser determinista.
    """
    pointer_rows = (
        session.query(BlueprintChannelPointer, ReleaseChannel)
        .join(
            ReleaseChannel,
            ReleaseChannel.id == BlueprintChannelPointer.release_channel_id,
        )
        .filter(
            BlueprintChannelPointer.database_model_id == migration.model_id,
            BlueprintChannelPointer.model_migration_id == migration.id,
            ReleaseChannel.resolution_mode == MODE_MANUAL,
        )
        .order_by(ReleaseChannel.id.asc())
        .all()
    )
    if not pointer_rows:
        return []

    destination = nearest_lower_reviewed_migration(session, migration.model_id, migration.version)
    return [
        {
            "release_channel_id": channel.id,
            "channel_slug": channel.slug,
            "from_version": migration.version,
            "to_version": destination.version if destination is not None else None,
            "to_migration_id": destination.id if destination is not None else None,
        }
        for _pointer, channel in pointer_rows
    ]


def build_pointer_event(
    *,
    database_model_id: int,
    release_channel_id: int,
    event_type: str,
    from_migration: ModelMigration | None,
    to_migration: ModelMigration | None,
    note: str | None,
    actor,
) -> BlueprintChannelPointerEvent:
    """
    Arma (sin persistir) un evento de la bitácora. Las versiones se copian como texto: la
    bitácora sobrevive al borrado y al renumerado de las filas que menciona.
    """
    actor_id, actor_username = identity_of(actor)
    return BlueprintChannelPointerEvent(
        database_model_id=database_model_id,
        release_channel_id=release_channel_id,
        event_type=event_type,
        from_migration_id=from_migration.id if from_migration is not None else None,
        to_migration_id=to_migration.id if to_migration is not None else None,
        from_version=from_migration.version if from_migration is not None else None,
        to_version=to_migration.version if to_migration is not None else None,
        note=note,
        actor_admin_id=actor_id,
        actor_username=actor_username or SYSTEM_ACTOR_USERNAME,
        actor_type=actor_type_of(actor),
    )
