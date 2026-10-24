"""
Resolución de la versión de un CANAL de liberación: tipos del resultado y la regla del modo
``derived``, sin I/O.

La lectura (BD de metadatos y, para ``derived``, el motor de cada base) la hace
``BlueprintChannelController.resolve_version``; acá vive solo la DECISIÓN, para que se pueda
probar sin motor y para que el listado (que lee la caché del inventario) y el resolver estricto
(que lee en vivo) apliquen exactamente la misma regla sobre estados de distinta procedencia.

REGLA DEL MODO ``derived``
--------------------------
La versión del canal es la MÍNIMA que hoy corre en las bases vivas del entorno enlazado. Cuenta
una base solo si el gateway la posicionó alguna vez y hoy existe en el motor:

- cuarentena (``status=error``): su versión es la de una migración fallida a medias, no la de un
  estado que alguien haya liberado. Se excluye.
- la base no existe en el motor: no está en ninguna versión.
- sin versión (en base / nunca posicionada): no está en ninguna versión.

Si una base que SÍ cuenta no se puede leer, o está en una versión que el blueprint no conoce, el
resultado es ``derived_unreadable`` y NO se calcula un mínimo: con una base muda, el mínimo de
las demás podría estar por encima de la verdadera, y el consumidor (aprovisionar, aplicar en
lote) avanzaría sobre una base que no se pudo ver. Falla cerrado.
"""

from dataclasses import dataclass, field

from app.services import release_channel_catalog as channel_codes
from app.services.db_admin.migration_integrity import version_sort_key

# --------------------------------------------------------------------------- #
# Origen del valor resuelto                                                    #
# --------------------------------------------------------------------------- #
#: Puntero guardado de un canal ``manual``.
SOURCE_POINTER = "pointer"
#: Punta del blueprint (canal ``latest``).
SOURCE_TIP = "tip"
#: Mínimo leído EN VIVO del motor. Es el único origen que consumen el aprovisionamiento, el
#: apply-all y la integración.
SOURCE_LIVE = "live"
#: Mínimo calculado con ``ManagedDatabase.model_version``. Solo para listados de referencia:
#: la caché puede estar desactualizada si alguien movió la base fuera del gateway.
SOURCE_INVENTORY_CACHE = "inventory_cache"

# --------------------------------------------------------------------------- #
# Por qué una base no cuenta para el mínimo                                    #
# --------------------------------------------------------------------------- #
IGNORED_QUARANTINED = "quarantined"
IGNORED_DATABASE_MISSING = "database_missing"
IGNORED_AT_BASE = "at_base"


@dataclass(frozen=True)
class DatabaseVersionState:
    """Lo que el cálculo ``derived`` necesita saber de UNA base, venga de donde venga."""

    managed_database_id: int
    database_name: str
    current_version: str | None
    database_exists: bool
    readable: bool
    quarantined: bool


@dataclass(frozen=True)
class DatabaseContribution:
    """Cómo participó una base en el cálculo, para mostrarlo al operador."""

    managed_database_id: int
    database_name: str
    current_version: str | None
    readable: bool
    counted: bool
    ignored_reason: str | None


@dataclass(frozen=True)
class DerivedOutcome:
    version: str | None
    model_migration_id: int | None
    error_code: str | None
    unreadable_database_ids: list[int]
    contributions: list[DatabaseContribution]


@dataclass
class ChannelResolution:
    """
    Resultado de resolver un canal contra un blueprint.

    ``version`` es ``None`` cuando no se pudo resolver; en ese caso ``error_code`` dice por qué
    (vocabulario de ``release_channel_catalog``). El resolver estricto convierte ese estado en
    una excepción; el tolerante lo devuelve tal cual para que una pantalla pueda mostrarlo.
    """

    channel_slug: str
    channel_name: str
    mode: str
    environment_slug: str | None
    version: str | None = None
    model_migration_id: int | None = None
    source: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_context: dict = field(default_factory=dict)
    databases: list[DatabaseContribution] = field(default_factory=list)
    derived_reference: "ChannelResolution | None" = None

    @property
    def resolved(self) -> bool:
        return self.version is not None and self.error_code is None

    def as_summary(self) -> dict[str, str | None]:
        """
        Resumen ``{slug, mode, version, source}`` que una operación que CONSUMIÓ el canal (alta,
        apply, apply-all) devuelve y audita: fija qué versión significaba el canal en esa corrida,
        porque el puntero se puede mover después. Su forma es ``ResolvedChannelOut``.
        """
        return {
            "slug": self.channel_slug,
            "mode": self.mode,
            "version": self.version,
            "source": self.source,
        }


def evaluate_derived(
    states: list[DatabaseVersionState],
    model_migration_id_by_version: dict[str, int],
) -> DerivedOutcome:
    """
    Aplica la regla ``derived`` a los estados.

    ``model_migration_id_by_version`` son las versiones que el blueprint CONOCE: una base
    parada en una que no está acá no se puede traducir a una fila y cuenta como ilegible.
    """
    contributions: list[DatabaseContribution] = []
    unreadable_ids: list[int] = []
    counted_versions: list[str] = []

    for state in states:
        ignored_reason: str | None = None
        counted = False

        if state.quarantined:
            ignored_reason = IGNORED_QUARANTINED
        elif not state.database_exists:
            ignored_reason = IGNORED_DATABASE_MISSING
        elif not state.readable:
            counted = True
            unreadable_ids.append(state.managed_database_id)
        elif state.current_version is None:
            ignored_reason = IGNORED_AT_BASE
        elif state.current_version not in model_migration_id_by_version:
            counted = True
            unreadable_ids.append(state.managed_database_id)
        else:
            counted = True
            counted_versions.append(state.current_version)

        contributions.append(
            DatabaseContribution(
                managed_database_id=state.managed_database_id,
                database_name=state.database_name,
                current_version=state.current_version,
                readable=state.readable,
                counted=counted,
                ignored_reason=ignored_reason,
            )
        )

    if unreadable_ids:
        return DerivedOutcome(
            version=None,
            model_migration_id=None,
            error_code=channel_codes.CODE_DERIVED_UNREADABLE,
            unreadable_database_ids=unreadable_ids,
            contributions=contributions,
        )

    if not counted_versions:
        return DerivedOutcome(
            version=None,
            model_migration_id=None,
            error_code=channel_codes.CODE_DERIVED_NO_SCOPE,
            unreadable_database_ids=[],
            contributions=contributions,
        )

    # Orden numérico (``version_sort_key``): '0010' > '0009' aunque el texto diga lo contrario.
    minimum_version = min(counted_versions, key=version_sort_key)
    return DerivedOutcome(
        version=minimum_version,
        model_migration_id=model_migration_id_by_version[minimum_version],
        error_code=None,
        unreadable_database_ids=[],
        contributions=contributions,
    )
