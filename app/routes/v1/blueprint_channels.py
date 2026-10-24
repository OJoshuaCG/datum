"""
Endpoints de los PUNTEROS de canal de un blueprint (``/database-models/{id}/channels``).

Promover mueve qué versión sirve un canal a quien lo consume; no toca ningún motor destino.

``blueprints.promote`` es solo de ``owner`` y exige step-up: decidir qué versión sale por
``stable`` es una decisión de liberación, distinta de AUTORÍA (``blueprints.write``, que tiene
``operator``). Mismo guard de dos capas que el borrado de una versión: la capacidad se exige
en el entorno más protegido entre las BDs del blueprint.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from app.controllers.blueprint_channel_controller import BlueprintChannelController
from app.core.actor import Actor
from app.core.authz import BlueprintsRead, require_at
from app.core.limiter import limiter
from app.core.scope_targets import model
from app.schemas.blueprint_channel import (
    ChannelDatabaseStateOut,
    ChannelPointerEventOut,
    ChannelPromoteIn,
    ChannelPromoteOut,
    ChannelReferenceOut,
    ChannelResolutionOut,
)
from app.services.channel_resolution import ChannelResolution, DatabaseContribution
from app.services.capability_catalog import Capability
from app.utils.pagination import PaginationDep
from app.utils.response import ApiResponse, paginated, success

router = APIRouter(prefix="/database-models", tags=["Blueprint channels"])

BlueprintsPromoteModel = Annotated[
    Actor, Depends(require_at(Capability.BLUEPRINTS_PROMOTE, target=model))
]

_CHANNEL_SLUG_PATH = Path(..., min_length=1, max_length=60, description="Slug del canal.")
_CONFIRM_SLUG_DESC = (
    "Repetir el slug del canal. OBLIGATORIO solo para un RETROCESO (la versión destino es "
    "menor que la actual): mover un canal hacia atrás es una decisión de liberación."
)


def _database_state_out(contribution: DatabaseContribution) -> ChannelDatabaseStateOut:
    return ChannelDatabaseStateOut(
        managed_database_id=contribution.managed_database_id,
        database_name=contribution.database_name,
        current_version=contribution.current_version,
        readable=contribution.readable,
        counted=contribution.counted,
        ignored_reason=contribution.ignored_reason,
    )


def _reference_out(reference: ChannelResolution) -> ChannelReferenceOut:
    return ChannelReferenceOut(
        environment_slug=reference.environment_slug,
        resolved=reference.resolved,
        code=reference.error_code,
        version=reference.version,
        model_migration_id=reference.model_migration_id,
        source=reference.source,
        databases=[_database_state_out(item) for item in reference.databases],
    )


def _resolution_out(resolution: ChannelResolution) -> ChannelResolutionOut:
    return ChannelResolutionOut(
        channel_slug=resolution.channel_slug,
        channel_name=resolution.channel_name,
        resolution_mode=resolution.mode,
        environment_slug=resolution.environment_slug,
        resolved=resolution.resolved,
        code=resolution.error_code,
        version=resolution.version,
        model_migration_id=resolution.model_migration_id,
        source=resolution.source,
        databases=[_database_state_out(item) for item in resolution.databases],
        derived_reference=(
            _reference_out(resolution.derived_reference)
            if resolution.derived_reference is not None
            else None
        ),
    )


@router.get(
    "/{model_id}/channels",
    response_model=ApiResponse[list[ChannelResolutionOut]],
)
def list_channels(actor: BlueprintsRead, model_id: int):
    """
    Cada canal con la versión que sirve para este blueprint.

    Los cálculos ``derived`` salen de la CACHÉ del inventario (``source=inventory_cache``): es
    una referencia para pantalla, no un dato para decidir. Para el valor en vivo, usar
    ``GET /{model_id}/channels/{channel_slug}/resolve``. Un canal que no resuelve aparece con
    ``resolved=false`` y su ``code``; nunca hace fallar el listado.
    """
    resolutions = BlueprintChannelController().list_channels(model_id)
    return success(data=[_resolution_out(item) for item in resolutions])


@router.get(
    "/{model_id}/channels/{channel_slug}/resolve",
    response_model=ApiResponse[ChannelResolutionOut],
)
# 20/minute: puede abrir una conexión por cada base del entorno (lectura en vivo), así que es
# más caro que un GET común pero lo dispara una pantalla abierta, no un lote.
@limiter.limit("20/minute")
def resolve_channel(
    request: Request,
    actor: BlueprintsRead,
    model_id: int,
    channel_slug: str = _CHANNEL_SLUG_PATH,
):
    """
    Lectura EN VIVO y TOLERANTE de un canal. Siempre 200 si el canal existe: cuando no resuelve,
    ``resolved=false`` + ``code`` (``release_channel.no_released_version``,
    ``release_channel.derived_unreadable``, ``release_channel.derived_no_scope``…) y, por base,
    ``readable``. En un canal manual o latest con entorno, ``derived_reference`` trae el mínimo
    vivo del entorno junto al puntero. Los consumidores NO usan este endpoint: usan el
    resolver estricto, que sí responde 409.
    """
    resolution = BlueprintChannelController().resolve(model_id, channel_slug)
    return success(data=_resolution_out(resolution))


@router.post(
    "/{model_id}/channels/{channel_slug}/promote",
    response_model=ApiResponse[ChannelPromoteOut],
)
def promote_channel(
    actor: BlueprintsPromoteModel,
    model_id: int,
    payload: ChannelPromoteIn,
    channel_slug: str = _CHANNEL_SLUG_PATH,
    confirm_slug: str | None = Query(None, max_length=60, description=_CONFIRM_SLUG_DESC),
):
    """
    Apunta el canal manual a una versión REVISADA del blueprint.

    - Canal que no es manual: 409 ``release_channel.not_manual``.
    - Versión sin revisar: 409 ``release_channel.version_unreviewed``.
    - Retroceso sin ``confirm_slug``: 409 ``release_channel.promote_backward_requires_confirm``.
    - Versión inexistente: 422 ``release_channel.version_not_found``; de otro blueprint:
      422 ``release_channel.version_not_in_blueprint``.
    - Misma versión que la actual: 200 con ``changed=false`` y sin evento.
    """
    result = BlueprintChannelController().promote(
        model_id,
        channel_slug,
        version=payload.version,
        model_migration_id=payload.model_migration_id,
        note=payload.note,
        confirm_slug=confirm_slug,
        admin=actor,
    )
    return success(data=result, message="Canal actualizado.")


@router.get(
    "/{model_id}/channels/{channel_slug}/history",
    response_model=ApiResponse[list[ChannelPointerEventOut]],
)
def channel_history(
    actor: BlueprintsRead,
    model_id: int,
    pagination: PaginationDep,
    channel_slug: str = _CHANNEL_SLUG_PATH,
):
    """
    Bitácora del puntero, del evento más reciente al más antiguo. Incluye los movimientos
    AUTOMÁTICOS (``version_deleted``) que provoca borrar la versión apuntada.
    """
    items, total = BlueprintChannelController().history(
        model_id, channel_slug, limit=pagination.size, offset=pagination.offset
    )
    return paginated(items, total=total, pagination=pagination)
