"""
Mantenimiento AUTOMÁTICO de los punteros de los canales ``latest`` y ``derived``.

Un canal sirve siempre lo que dice su puntero, sea cual sea su modo. En ``manual`` lo mueve una
persona (``promote``); en ``latest`` y ``derived`` lo mueve este módulo cada vez que cambia algo
de lo que depende:

- ``latest``: la versión REVISADA más nueva del blueprint.
- ``derived``: la versión mínima que registra el inventario (``ManagedDatabase.model_version``)
  entre las bases del blueprint en el entorno del canal.

REGLA QUE NO SE NEGOCIA: el puntero solo cae sobre versiones REVISADAS. Si la nueva punta (o el
nuevo mínimo) no está aprobada, el puntero se queda donde estaba; cuando alguien la aprueba, el
gancho de aprobación vuelve a llamar a ``sync_automatic_pointers`` y entonces sí se mueve. Es el
mismo invariante que impone ``promote`` al canal manual.

Tampoco calcula con datos parciales: si alguna base del entorno no tiene versión legible, el
puntero no se mueve (``evaluate_derived`` ya se niega a dar un mínimo parcial).

Los movimientos dejan evento en la bitácora con los tipos existentes (``promote`` si avanza o es
el primer valor, ``demote`` si retrocede), ``actor_type`` del sistema y una nota ``automático``.
No se agregaron tipos de evento nuevos a propósito: ``event_type`` tiene un CHECK en la BD y
ampliarlo exige una migración sin DDL transaccional para un dato que la nota ya distingue.
"""

from app.core.logger import get_logger
from app.models.database_model import DatabaseModel
from app.models.model_migration import ModelMigration
from app.models.release_channel import BlueprintChannelPointer, ReleaseChannel
from app.services import release_channel_catalog as channel_codes
from app.services.blueprint_channel_pointers import build_pointer_event
from app.services.channel_resolution import evaluate_derived
from app.services.db_admin.migration_integrity import version_sort_key

logger = get_logger(__name__)

AUTOMATIC_MOVE_NOTE_PREFIX = "automático"


class _SystemActor:
    """Identidad de los movimientos automáticos: no hay una persona detrás."""

    kind = "system"
    id = None
    username = None


SYSTEM_ACTOR = _SystemActor()


def _latest_reviewed_migration(session, model_id: int) -> ModelMigration | None:
    reviewed_rows = (
        session.query(ModelMigration)
        .filter(ModelMigration.model_id == model_id, ModelMigration.reviewed.is_(True))
        .all()
    )
    if not reviewed_rows:
        return None
    return max(reviewed_rows, key=lambda row: version_sort_key(row.version))


def _derived_minimum_migration(
    session, model_id: int, channel: ReleaseChannel
) -> ModelMigration | None:
    if channel.environment_id is None:
        return None
    # Import perezoso: el controller importa este módulo desde sus ganchos.
    from app.controllers.blueprint_channel_controller import BlueprintChannelController

    states = BlueprintChannelController._cached_states(session, model_id, channel.environment_id)
    migrations = session.query(ModelMigration).filter(ModelMigration.model_id == model_id).all()
    migration_by_version = {row.version: row for row in migrations}
    outcome = evaluate_derived(states, {row.version: row.id for row in migrations})
    if outcome.error_code is not None or outcome.version is None:
        return None
    return migration_by_version.get(outcome.version)


def _automatic_target(session, model_id: int, channel: ReleaseChannel) -> ModelMigration | None:
    if channel.resolution_mode == channel_codes.MODE_LATEST:
        candidate = _latest_reviewed_migration(session, model_id)
    else:
        candidate = _derived_minimum_migration(session, model_id, channel)
    if candidate is None or not candidate.reviewed:
        return None
    return candidate


def sync_automatic_pointers(session, model_id: int) -> list[dict]:
    """
    Recalcula y mueve los punteros ``latest``/``derived`` de UN blueprint. Hace ``flush`` pero NO
    ``commit``: lo hace quien llama, en la misma transacción que el cambio que lo disparó.

    Devuelve los movimientos hechos (``channel_slug``, ``from_version``, ``to_version``).
    Idempotente: si nada cambió, no escribe nada.
    """
    if session.get(DatabaseModel, model_id) is None:
        return []
    automatic_channels = (
        session.query(ReleaseChannel)
        .filter(ReleaseChannel.resolution_mode != channel_codes.MODE_MANUAL)
        .order_by(ReleaseChannel.id.asc())
        .all()
    )
    moves: list[dict] = []
    for channel in automatic_channels:
        target = _automatic_target(session, model_id, channel)
        if target is None:
            continue
        pointer = session.get(
            BlueprintChannelPointer,
            {"database_model_id": model_id, "release_channel_id": channel.id},
        )
        current = (
            session.get(ModelMigration, pointer.model_migration_id)
            if pointer is not None and pointer.model_migration_id is not None
            else None
        )
        if current is not None and current.id == target.id:
            continue
        if pointer is None:
            pointer = BlueprintChannelPointer(
                database_model_id=model_id, release_channel_id=channel.id
            )
            session.add(pointer)
        pointer.model_migration_id = target.id

        is_backward = current is not None and version_sort_key(target.version) < version_sort_key(
            current.version
        )
        session.add(
            build_pointer_event(
                database_model_id=model_id,
                release_channel_id=channel.id,
                event_type=(
                    channel_codes.EVENT_DEMOTE if is_backward else channel_codes.EVENT_PROMOTE
                ),
                from_migration=current,
                to_migration=target,
                note=f"{AUTOMATIC_MOVE_NOTE_PREFIX} ({channel.resolution_mode})",
                actor=SYSTEM_ACTOR,
            )
        )
        moves.append(
            {
                "channel_slug": channel.slug,
                "from_version": current.version if current is not None else None,
                "to_version": target.version,
            }
        )
    if moves:
        session.flush()
    return moves


def sync_automatic_pointers_safely(session, model_id: int | None) -> None:
    """
    Gancho para los flujos que ya tienen su propia transacción: nunca rompe la operación que lo
    llama. El puntero es un dato derivado; si esta pasada falla, la próxima (otro gancho o el
    reconciliado de arranque) lo corrige, mientras que tumbar un ``apply`` o una aprobación por
    esto sería peor que un puntero atrasado. Hace su propio ``commit``.
    """
    if model_id is None:
        return
    try:
        sync_automatic_pointers(session, model_id)
        session.commit()
    except Exception:  # noqa: BLE001 — ver docstring
        session.rollback()
        logger.exception("No se pudo sincronizar los punteros automáticos del blueprint %s.", model_id)


def reconcile_all_automatic_pointers() -> None:
    """
    Pasada de arranque: deja al día los punteros automáticos de TODOS los blueprints. Es el
    backfill de los canales ``latest``/``derived`` que ya existían sin puntero, y la red de
    seguridad de los ganchos (cambios hechos fuera del gateway, un gancho que falló).
    """
    from app.core.database import Database

    session = Database().get_declarative_base_session()
    try:
        model_ids = [row[0] for row in session.query(DatabaseModel.id).all()]
        for model_id in model_ids:
            sync_automatic_pointers_safely(session, model_id)
    except Exception:  # noqa: BLE001 — el reconciliado no debe tumbar el arranque
        session.rollback()
        logger.exception("No se pudo reconciliar los punteros automáticos de canales.")
    finally:
        session.close()
