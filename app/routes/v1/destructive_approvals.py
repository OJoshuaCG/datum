"""
Aprobaciones de migraciones destructivas: listar versiones, aprobar (por base o por blueprint) y
revocar.

Una migración destructiva no se ejecuta sobre un entorno que bloquea lo destructivo hasta que una
SEGUNDA persona la aprueba. Estos endpoints solo escriben metadatos del gateway: ejecutar con la
aprobación en mano es el apply/rollback de siempre (``/managed-databases`` y
``/database-models/.../migrations``).

``blueprints.approve_destructive`` es solo de ``owner`` y exige step-up, salvo REVOCAR: revocar
frena una operación destructiva y nunca puede costar más que lanzarla (misma excepción que las
cancelaciones, ver ``STEP_UP_EXEMPT`` en ``scripts/check_route_capabilities.py``). Revocar NO tiene
camino por ``access.admin``: esa capacidad no es operativa por invariante del catálogo.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.controllers.destructive_approval_controller import DestructiveApprovalController
from app.core.actor import Actor
from app.core.authz import BlueprintsRead, require_at
from app.core.scope import ScopeTarget
from app.core.scope_targets import database, destructive_approval, model
from app.schemas.destructive_approval import (
    DestructiveApprovalBatchCreate,
    DestructiveApprovalBatchOut,
    DestructiveApprovalCreate,
    DestructiveApprovalOut,
    DestructiveApprovalRevoke,
    DestructiveVersionOut,
)
from app.services.capability_catalog import Capability
from app.utils.response import ApiResponse, success

router = APIRouter(tags=["Destructive approvals"])

#: Aprobar para TODO el blueprint: la capa 2 se evalúa en el entorno más protegido entre sus bases,
#: y el controller además la exige en cada entorno bloqueante (cubre las bases futuras).
ApproveDestructiveOnModel = Annotated[
    Actor, Depends(require_at(Capability.BLUEPRINTS_APPROVE_DESTRUCTIVE, target=model))
]
ApproveDestructiveOnDatabase = Annotated[
    Actor, Depends(require_at(Capability.BLUEPRINTS_APPROVE_DESTRUCTIVE, target=database))
]


def _is_approver_of_record(actor: Actor, target: ScopeTarget) -> bool:
    (approval_id,) = target.params
    return DestructiveApprovalController.is_approver_of_record(actor, approval_id)


#: ``step_up=False``: revocar es una cancelación. Las capas 1 y 2 valen para todos MENOS para quien
#: firmó la aprobación (enmienda A-2): ese puede revocarla aunque ya no tenga la capacidad.
RevokeDestructiveApproval = Annotated[
    Actor,
    Depends(
        require_at(
            Capability.BLUEPRINTS_APPROVE_DESTRUCTIVE,
            target=destructive_approval,
            holder_of_record=_is_approver_of_record,
            step_up=False,
        )
    ),
]

_VERSION_PATH = Path(..., pattern=r"^\d{4,10}$", description="Versión de la migración: 0001, 0002…")


@router.get(
    "/database-models/{model_id}/destructive-versions",
    response_model=ApiResponse[list[DestructiveVersionOut]],
)
def list_destructive_versions(
    actor: BlueprintsRead,
    model_id: int,
    managed_database_id: int | None = Query(
        None,
        ge=1,
        description=(
            "Evalúa 'approval_status' para ESTA base (aprobaciones de su alcance y del "
            "blueprint). Sin ella solo cuentan las de alcance blueprint."
        ),
    ),
):
    """
    Versiones del blueprint cuyo SQL es destructivo, una fila por dirección (``up``/``down``),
    con el hash actual (el ``expected_sql_hash`` para aprobar), la autoría, el estado
    (``approved`` | ``unapproved`` | ``revoked`` | ``stale_hash``) y el historial de aprobaciones.

    Errores: 404 ``destructive_approval.not_found``; 422 ``destructive_approval.scope_invalid``
    (la base no usa este blueprint).
    """
    versions = DestructiveApprovalController().list_versions(
        model_id, managed_database_id=managed_database_id
    )
    return success(data=versions)


@router.post(
    "/database-models/{model_id}/migrations/{version}/destructive-approvals",
    response_model=ApiResponse[DestructiveApprovalOut],
    status_code=201,
)
def approve_destructive_for_blueprint(
    actor: ApproveDestructiveOnModel,
    model_id: int,
    payload: DestructiveApprovalCreate,
    version: str = _VERSION_PATH,
):
    """
    Aprueba la versión para TODAS las bases del blueprint que viven en entornos protegidos,
    incluidas las que se creen después. Exige ``blueprints.approve_destructive`` en cada entorno
    que bloquea lo destructivo.

    Aprobar de nuevo después de editar el SQL reemplaza la aprobación anterior.
    Errores: 403 ``access.forbidden`` / ``destructive_approval.machine_actor``; 404
    ``destructive_approval.not_found``; 409 ``access.self_approval_forbidden`` (creador o último
    editor) / ``destructive_approval.sql_changed`` / ``destructive_approval.duplicate``; 422
    ``destructive_approval.not_destructive`` / ``destructive_approval.down_sql_missing``.
    """
    approval = DestructiveApprovalController().approve(
        model_id,
        version,
        direction=payload.direction,
        expected_sql_hash=payload.expected_sql_hash,
        reason=payload.reason,
        actor=actor,
    )
    return success(data=approval, message="Migración destructiva aprobada para el blueprint.")


@router.post(
    "/managed-databases/{db_id}/migrations/{version}/destructive-approvals",
    response_model=ApiResponse[DestructiveApprovalOut],
    status_code=201,
)
def approve_destructive_for_database(
    actor: ApproveDestructiveOnDatabase,
    db_id: int,
    payload: DestructiveApprovalCreate,
    version: str = _VERSION_PATH,
):
    """
    Aprueba la versión SOLO para esta base. Mismas reglas y errores que la aprobación por
    blueprint, más 422 ``destructive_approval.scope_invalid`` si la base no tiene blueprint.
    """
    controller = DestructiveApprovalController()
    model_id = controller.model_id_of_database(db_id)
    approval = controller.approve(
        model_id,
        version,
        direction=payload.direction,
        expected_sql_hash=payload.expected_sql_hash,
        reason=payload.reason,
        actor=actor,
        managed_database_id=db_id,
    )
    return success(data=approval, message="Migración destructiva aprobada para la base.")


@router.post(
    "/database-models/{model_id}/destructive-approvals/batch",
    response_model=ApiResponse[DestructiveApprovalBatchOut],
)
def approve_destructive_batch_for_blueprint(
    actor: ApproveDestructiveOnModel,
    model_id: int,
    payload: DestructiveApprovalBatchCreate,
):
    """
    Aprueba varias versiones del blueprint de una vez, con alcance blueprint. Resultado PARCIAL:
    responde 200 con un resultado por ítem (``approved`` | ``failed``); lo que falla no frena lo
    demás. Cada ítem sigue las reglas de la aprobación individual (cuatro ojos, hash, step-up).

    Errores de la petición entera: 403 ``access.forbidden`` / ``destructive_approval.machine_actor``;
    422 de validación (lote vacío, más de 100 ítems, versión repetida). Los errores por ítem llevan
    los mismos códigos que la aprobación individual.
    """
    result = DestructiveApprovalController().approve_batch(
        model_id,
        [item.model_dump() for item in payload.items],
        reason=payload.reason,
        actor=actor,
    )
    return success(data=result, message="Lote de aprobaciones procesado para el blueprint.")


@router.post(
    "/managed-databases/{db_id}/destructive-approvals/batch",
    response_model=ApiResponse[DestructiveApprovalBatchOut],
)
def approve_destructive_batch_for_database(
    actor: ApproveDestructiveOnDatabase,
    db_id: int,
    payload: DestructiveApprovalBatchCreate,
):
    """Igual que el lote por blueprint, pero con alcance SOLO a esta base."""
    controller = DestructiveApprovalController()
    model_id = controller.model_id_of_database(db_id)
    result = controller.approve_batch(
        model_id,
        [item.model_dump() for item in payload.items],
        reason=payload.reason,
        actor=actor,
        managed_database_id=db_id,
    )
    return success(data=result, message="Lote de aprobaciones procesado para la base.")


@router.post(
    "/destructive-approvals/{approval_id}/revoke",
    response_model=ApiResponse[DestructiveApprovalOut],
)
def revoke_destructive_approval(
    actor: RevokeDestructiveApproval,
    approval_id: int,
    payload: DestructiveApprovalRevoke,
):
    """
    Revoca una aprobación: el próximo apply/rollback de esa versión vuelve a ser 409
    ``environment.destructive_blocked``. No toca lo ya aplicado. No exige step-up.

    Idempotente: revocar una aprobación ya revocada o reemplazada responde 200 con la fila tal
    cual (mismo revocador y fecha). Errores: 403 ``access.forbidden``; 404
    ``destructive_approval.not_found``.
    """
    approval = DestructiveApprovalController().revoke(
        approval_id, reason=payload.reason, actor=actor
    )
    return success(data=approval, message="Aprobación revocada.")
