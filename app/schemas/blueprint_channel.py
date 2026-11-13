"""
Schemas Pydantic de los PUNTEROS de canal de un blueprint: promover y su historial.

El destino de la promoción se indica por ``version`` (el número que ve el operador) o por
``model_migration_id`` (la fila). Existe el segundo porque la versión sola no distingue "no
existe" de "es de otro blueprint": con el id se puede decir cuál de las dos pasó, que es lo que
separa ``release_channel.version_not_found`` de ``release_channel.version_not_in_blueprint``.
"""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

_VERSION_PATTERN = r"^\d{4,10}$"
_NOTE_MAX_LENGTH = 500


class ChannelPromoteIn(BaseModel):
    version: str | None = Field(
        None,
        pattern=_VERSION_PATTERN,
        description="Versión del blueprint a la que apuntará el canal: 0001, 0002…",
    )
    model_migration_id: int | None = Field(
        None,
        ge=1,
        description=(
            "Alternativa a 'version': id de la fila de la versión. Si pertenece a OTRO "
            "blueprint se responde 422 release_channel.version_not_in_blueprint."
        ),
    )
    note: str | None = Field(
        None,
        max_length=_NOTE_MAX_LENGTH,
        description="Motivo del movimiento. Queda en la bitácora del puntero.",
    )

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "ChannelPromoteIn":
        has_version = self.version is not None
        has_migration_id = self.model_migration_id is not None
        if has_version == has_migration_id:
            raise ValueError("Indica exactamente uno de 'version' o 'model_migration_id'.")
        return self


class ChannelPromoteOut(BaseModel):
    channel_slug: str
    changed: bool = Field(
        description="False = el canal ya apuntaba a esa versión: no se escribió nada ni hay evento."
    )
    event_type: str | None = Field(
        None, description="promote o demote. Null cuando 'changed' es false."
    )
    from_version: str | None = None
    to_version: str
    model_migration_id: int


class ChannelPointerEventOut(BaseModel):
    id: int
    event_type: str = Field(description="promote | demote | version_deleted")
    from_migration_id: int | None = None
    to_migration_id: int | None = None
    from_version: str | None = None
    to_version: str | None = None
    note: str | None = None
    actor_admin_id: int | None = None
    actor_username: str
    actor_type: str
    created_at: datetime


class ChannelDatabaseStateOut(BaseModel):
    managed_database_id: int
    database_name: str
    current_version: str | None = Field(
        None, description="Versión leída del motor (o de la caché del inventario en el listado)."
    )
    readable: bool = Field(
        description="False = no se pudo leer la versión de esta base (detalle solo en el log)."
    )
    counted: bool = Field(description="True = esta base entró en el cálculo del mínimo.")
    ignored_reason: str | None = Field(
        None,
        description=(
            "Por qué no cuenta: quarantined | database_missing | at_base. Null si cuenta."
        ),
    )


class ChannelReferenceOut(BaseModel):
    """
    Versión calculada por el mínimo de las bases VIVAS de un entorno. Es una REFERENCIA para
    el operador: en un canal manual no decide nada, solo se muestra junto al puntero.
    """

    environment_slug: str | None = None
    resolved: bool
    code: str | None = Field(
        None,
        description=(
            "Código release_channel.* de por qué no se pudo resolver "
            "(derived_unreadable | derived_no_scope)."
        ),
    )
    version: str | None = None
    model_migration_id: int | None = None
    source: str | None = Field(None, description="live | inventory_cache")
    databases: list[ChannelDatabaseStateOut] = Field(default_factory=list)


class ChannelResolutionOut(BaseModel):
    """
    Qué versión sirve un canal para un blueprint.

    ``resolved=false`` NO es un error HTTP: el endpoint de lectura siempre responde 200 y dice
    en ``code`` por qué no hay versión (puntero sin definir, base ilegible…). Los consumidores
    (aprovisionar, apply-all, integración) usan el resolver ESTRICTO, que sí responde 409.
    """

    channel_slug: str
    channel_name: str
    resolution_mode: str
    environment_slug: str | None = None
    resolved: bool
    code: str | None = None
    version: str | None = None
    model_migration_id: int | None = None
    source: str | None = Field(None, description="pointer | tip | live | inventory_cache")
    databases: list[ChannelDatabaseStateOut] = Field(
        default_factory=list, description="Solo en canales derived."
    )
    derived_reference: ChannelReferenceOut | None = Field(
        None,
        description=(
            "Mínimo de las bases vivas del entorno enlazado, junto al puntero de cualquier "
            "canal que tenga entorno. Es una referencia: lo que sirve el canal es el puntero."
        ),
    )


class ResolvedChannelOut(BaseModel):
    """
    Qué versión sirvió un canal en una operación que lo consumió (alta, apply, apply-all). Queda
    en la respuesta porque el canal es un puntero que se mueve: sin este dato, después de la
    operación nadie sabría qué versión significaba 'stable' en ese momento.
    """

    slug: str
    mode: str
    version: str
    source: str | None = Field(
        None, description="Origen del valor: 'pointer', 'tip' (latest) o 'live' (derived)."
    )
