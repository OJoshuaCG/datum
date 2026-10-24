"""
Schemas Pydantic de los CANALES DE LIBERACIÓN de blueprints.

El modo de resolución se valida acá con un ``Literal`` y NO con un código propio: un modo
inválido es un 422 estándar de Pydantic. El vocabulario sale de ``RELEASE_CHANNEL_MODES`` para
que haya una sola lista (la del CHECK de la BD).

``slug`` se normaliza (recorte y minúsculas) y se valida con un patrón cerrado acá y no en el
motor: MySQL/MariaDB comparan case-insensitive y PostgreSQL no, y el slug es la identidad que
citan las integraciones y que se repite como gesto de confirmación del borrado. Mismo criterio
que ``app/schemas/environment.py``.

``slug`` NO está en ``ReleaseChannelUpdate``: es inmutable. Renombrarlo rompería a toda
integración que ya lo cita (``?channel=stable``) sin que nada falle al hacerlo.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.release_channel_catalog import RELEASE_CHANNEL_MODES

_NAME_DESC = "Nombre legible del canal (único). Se recortan los espacios de los extremos."
_SLUG_DESC = (
    "Identificador estable en minúsculas (a-z, 0-9 y guiones), único e inmutable. Es lo que "
    "citan las integraciones y lo que hay que repetir en 'confirm_slug' para borrar un canal "
    "que ya tiene punteros."
)
_MODE_DESC = (
    f"Cómo se resuelve la versión del canal. Uno de: {' | '.join(RELEASE_CHANNEL_MODES)}. "
    "manual = puntero movido por una persona; derived = mínima versión viva en el entorno "
    "enlazado (exige environment_id); latest = punta del blueprint."
)
_ENVIRONMENT_DESC = (
    "Entorno enlazado al canal. Obligatorio en modo derived. Mientras un canal lo use, el "
    "entorno no se puede borrar. Enviar null en el PATCH lo desvincula."
)
_SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{0,58}[a-z0-9]$|^[a-z0-9]$"

ResolutionMode = Literal["manual", "derived", "latest"]


def _clean_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("El nombre no puede ser solo espacios.")
    return cleaned


class ReleaseChannelCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60, description=_NAME_DESC)
    slug: str = Field(..., min_length=1, max_length=60, description=_SLUG_DESC)
    resolution_mode: ResolutionMode = Field("manual", description=_MODE_DESC)
    environment_id: int | None = Field(None, ge=1, description=_ENVIRONMENT_DESC)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        return _clean_name(value)

    @field_validator("slug")
    @classmethod
    def _normalize_slug(cls, value: str) -> str:
        normalized_slug = value.strip().lower()
        if not re.match(_SLUG_PATTERN, normalized_slug):
            raise ValueError(
                "El slug debe ser minúsculas, dígitos y guiones, empezando y terminando en "
                "letra o dígito (p. ej. 'stable', 'release-candidate')."
            )
        return normalized_slug


class ReleaseChannelUpdate(BaseModel):
    """
    PATCH parcial: el controller usa ``exclude_unset``, así que un campo no enviado no cambia y
    ``environment_id: null`` sí desvincula el entorno. ``slug`` no se puede editar (ver el
    docstring del módulo).
    """

    name: str | None = Field(None, min_length=1, max_length=60, description=_NAME_DESC)
    resolution_mode: ResolutionMode | None = Field(None, description=_MODE_DESC)
    environment_id: int | None = Field(None, ge=1, description=_ENVIRONMENT_DESC)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        return _clean_name(value) if value is not None else None


class ReleaseChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    resolution_mode: str
    environment_id: int | None = None
    pointer_count: int = Field(
        0,
        description=(
            "Punteros de blueprint que apuntan a este canal. Si es mayor que cero, borrar el "
            "canal exige 'confirm_slug' porque los punteros y su historial se pierden."
        ),
    )
    created_at: datetime
    updated_at: datetime
