"""
Endpoints de CANALES DE LIBERACIÓN de blueprints.

CRUD sobre el catálogo de canales (``stable``, ``development``…). No toca ningún motor destino.
El movimiento de los punteros de cada blueprint es otra superficie.

Leer exige ``release_channels.read`` (cualquier rol del gateway). Escribir exige
``release_channels.write``, que solo tiene ``security_officer``: cada canal decide qué versión
de un blueprint consumen las integraciones, así que quien administra el acceso no debe poder
reconfigurarlo (mismo criterio que ``environments.write``).

Se pagina como ``/environments``: el helper del frontend que consume listados exige el bloque
``pagination``.
"""

from fastapi import APIRouter, Query

from app.controllers.release_channel_controller import ReleaseChannelController
from app.core.authz import ReleaseChannelsRead, ReleaseChannelsWrite
from app.schemas.release_channel import (
    ReleaseChannelCreate,
    ReleaseChannelOut,
    ReleaseChannelUpdate,
)
from app.utils.pagination import PaginationDep
from app.utils.response import ApiResponse, empty, paginated, success

router = APIRouter(prefix="/release-channels", tags=["Release channels"])

_CONFIRM_SLUG_DESC = (
    "Repetir el slug del canal. OBLIGATORIO solo si el canal ya tiene punteros de blueprint: "
    "borrarlo pierde el historial de qué versión servía cada uno."
)


@router.get("", response_model=ApiResponse[list[ReleaseChannelOut]])
def list_release_channels(actor: ReleaseChannelsRead, pagination: PaginationDep):
    """Canales ordenados por id (orden de creación)."""
    items, total = ReleaseChannelController().list_channels(
        limit=pagination.size, offset=pagination.offset
    )
    return paginated(items, total=total, pagination=pagination)


@router.post("", response_model=ApiResponse[ReleaseChannelOut], status_code=201)
def create_release_channel(actor: ReleaseChannelsWrite, payload: ReleaseChannelCreate):
    created = ReleaseChannelController().create_channel(payload.model_dump(), admin=actor)
    return success(data=created, message="Canal de liberación creado.")


@router.get("/{channel_id}", response_model=ApiResponse[ReleaseChannelOut])
def get_release_channel(actor: ReleaseChannelsRead, channel_id: int):
    return success(data=ReleaseChannelController().get_channel(channel_id))


@router.patch("/{channel_id}", response_model=ApiResponse[ReleaseChannelOut])
def update_release_channel(
    actor: ReleaseChannelsWrite, channel_id: int, payload: ReleaseChannelUpdate
):
    """
    PATCH parcial. ``slug`` no se puede editar (es la identidad que citan las integraciones).

    Un canal en modo ``derived`` sin entorno resultante devuelve 422
    ``release_channel.environment_required``.
    """
    updated = ReleaseChannelController().update_channel(
        channel_id, payload.model_dump(exclude_unset=True), admin=actor
    )
    return success(data=updated, message="Canal de liberación actualizado.")


@router.delete("/{channel_id}", response_model=ApiResponse[None])
def delete_release_channel(
    actor: ReleaseChannelsWrite,
    channel_id: int,
    confirm_slug: str | None = Query(None, max_length=60, description=_CONFIRM_SLUG_DESC),
):
    """
    Borra el canal junto con sus punteros y su bitácora. Con punteros existentes exige
    ``confirm_slug`` → 422 ``release_channel.confirmation_required``.
    """
    ReleaseChannelController().delete_channel(channel_id, confirm_slug=confirm_slug, admin=actor)
    return empty(message="Canal de liberación borrado.")
