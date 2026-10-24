"""
Catálogo de CANALES DE LIBERACIÓN: vocabularios cerrados, códigos de error, filas de seed y
siembra de arranque.

Un canal (``stable``, ``development``…) es un nombre global al que cada blueprint apunta con un
puntero a UNA de sus versiones. Este módulo es el "contrato estable" del catálogo y, igual que
``environment_catalog``, junta los códigos de error (que el frontend consume) y el seed (que
define el punto de partida) porque los dos se rompen en silencio si alguien los escribe inline.

--------------------------------------------------------------------------------
1. MODOS DE RESOLUCIÓN — vocabulario cerrado, NO un ENUM de la BD
--------------------------------------------------------------------------------
``manual`` (el puntero lo mueve una persona con ``blueprints.promote``), ``derived`` (la versión
mínima que hoy corre en las bases vivas del entorno enlazado) y ``latest`` (la punta del
blueprint). Cada modo es código del resolver, así que agregar uno exige un deploy de todas
formas: una fila de lookup no habilitaría nada que un ``INSERT`` no pueda romper. La BD lo hace
cumplir con un ``CHECK`` declarativo (ver ``app/models/release_channel.py``), no con ``ENUM``
(agregar un valor a un ``ENUM`` es un ``ALTER TABLE`` y no admite atributos).

Este módulo NO importa los modelos al nivel del módulo: son los modelos los que importan estas
constantes para armar sus ``CHECK``, y el import cruzado sería circular. Los modelos que hacen
falta para sembrar se importan dentro de ``seed_release_channels``.

--------------------------------------------------------------------------------
2. CÓDIGOS DE ERROR — viajan en ``public_context``, NO en ``context``
--------------------------------------------------------------------------------
``context`` se expone únicamente en development (``app/exceptions/HandlerExceptions.py``);
``public_context`` viaja siempre y el frontend lee ``detail.public_context.code``. Un código en
``context`` no existe en producción. Mismo vocabulario cerrado que ``environment_catalog``.

Mapeo contra los nombres de la especificación (el modo inválido NO tiene código propio: lo
rechaza la validación de Pydantic con un 422 estándar):

    release_channel_not_found            → ``release_channel.not_found``
    release_channel_slug_conflict        → ``release_channel.slug_taken``
    promote_backward_requires_confirm    → ``release_channel.promote_backward_requires_confirm``
    promote_version_not_reviewed         → ``release_channel.version_unreviewed``
    promote_mode_not_manual              → ``release_channel.not_manual``
    promote_version_mismatch             → ``release_channel.version_not_in_blueprint``
    channel_pointer_unset                → ``release_channel.no_released_version``
    channel_derived_unresolvable         → ``release_channel.derived_unreadable``
    channel_derived_no_scope             → ``release_channel.derived_no_scope``
    channel_and_target_version_exclusive → ``release_channel.target_conflict``

--------------------------------------------------------------------------------
3. SEED — se siembra SOLO SI LA TABLA ESTÁ VACÍA
--------------------------------------------------------------------------------
Mismo criterio que ``seed_environments`` y por las mismas razones: la fila es política. Un seed
que "agrega lo que falta" resucitaría un canal que el operador borró a propósito (con sus
punteros ya perdidos por el borrado en cascada) en cada reinicio. Si hay al menos una fila, el
seed no toca nada.

Las filas se declaran literalmente en la migración TAMBIÉN, y
``tests/test_release_channel_catalog.py`` compara las dos listas: es lo único que impide que un
gateway provisionado por Alembic y uno provisionado por ``Base.metadata.create_all`` queden con
canales distintos.
"""

from app.core.database import Database
from app.core.logger import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Modos de resolución                                                          #
# --------------------------------------------------------------------------- #
MODE_MANUAL = "manual"
MODE_DERIVED = "derived"
MODE_LATEST = "latest"

RELEASE_CHANNEL_MODES: tuple[str, ...] = (MODE_MANUAL, MODE_DERIVED, MODE_LATEST)

# --------------------------------------------------------------------------- #
# Tipos de evento del historial de punteros                                    #
# --------------------------------------------------------------------------- #
#: Avance (o primer valor) hecho por una persona con ``blueprints.promote``.
EVENT_PROMOTE = "promote"
#: Retroceso confirmado explícitamente por una persona.
EVENT_DEMOTE = "demote"
#: Movimiento AUTOMÁTICO: la versión apuntada se borró y el puntero se movió con ella.
EVENT_VERSION_DELETED = "version_deleted"

POINTER_EVENT_TYPES: tuple[str, ...] = (EVENT_PROMOTE, EVENT_DEMOTE, EVENT_VERSION_DELETED)

# --------------------------------------------------------------------------- #
# Vocabulario cerrado de códigos de error                                      #
# --------------------------------------------------------------------------- #
CODE_NOT_FOUND = "release_channel.not_found"
CODE_NAME_TAKEN = "release_channel.name_taken"
CODE_SLUG_TAKEN = "release_channel.slug_taken"
CODE_ENVIRONMENT_REQUIRED = "release_channel.environment_required"
CODE_CONFIRMATION_REQUIRED = "release_channel.confirmation_required"
CODE_NOT_MANUAL = "release_channel.not_manual"
CODE_VERSION_NOT_FOUND = "release_channel.version_not_found"
CODE_VERSION_NOT_IN_BLUEPRINT = "release_channel.version_not_in_blueprint"
CODE_VERSION_UNREVIEWED = "release_channel.version_unreviewed"
CODE_PROMOTE_BACKWARD_REQUIRES_CONFIRM = "release_channel.promote_backward_requires_confirm"
CODE_NO_RELEASED_VERSION = "release_channel.no_released_version"
CODE_DERIVED_UNREADABLE = "release_channel.derived_unreadable"
CODE_DERIVED_NO_SCOPE = "release_channel.derived_no_scope"
CODE_POINTER_INCONSISTENT = "release_channel.pointer_inconsistent"
CODE_TARGET_CONFLICT = "release_channel.target_conflict"

ERROR_CODES = frozenset(
    {
        CODE_NOT_FOUND,
        CODE_NAME_TAKEN,
        CODE_SLUG_TAKEN,
        CODE_ENVIRONMENT_REQUIRED,
        CODE_CONFIRMATION_REQUIRED,
        CODE_NOT_MANUAL,
        CODE_VERSION_NOT_FOUND,
        CODE_VERSION_NOT_IN_BLUEPRINT,
        CODE_VERSION_UNREVIEWED,
        CODE_PROMOTE_BACKWARD_REQUIRES_CONFIRM,
        CODE_NO_RELEASED_VERSION,
        CODE_DERIVED_UNREADABLE,
        CODE_DERIVED_NO_SCOPE,
        CODE_POINTER_INCONSISTENT,
        CODE_TARGET_CONFLICT,
    }
)


# --------------------------------------------------------------------------- #
# Seed                                                                         #
# --------------------------------------------------------------------------- #
def release_channel_seed_rows() -> list[dict]:
    """
    Filas iniciales: ``stable`` (manual) y ``development`` (latest). **Tienen que coincidir**
    con ``_SEED_ROWS`` de la migración ``d2f4a6c8e0b1``; un test compara las dos listas.

    El enlace a un entorno se declara por SLUG (``environment_slug``) y no por id: el id lo
    asigna cada instalación. ``stable`` apunta a ``production`` si ese entorno existe, y queda
    sin entorno si no (el operador lo enlaza después; un canal manual no lo necesita para
    resolver). ``development`` sigue la punta del blueprint, así que no tiene entorno.
    """
    return [
        {
            "name": "Estable",
            "slug": "stable",
            "resolution_mode": MODE_MANUAL,
            "environment_slug": "production",
        },
        {
            "name": "Desarrollo",
            "slug": "development",
            "resolution_mode": MODE_LATEST,
            "environment_slug": None,
        },
    ]


def seed_release_channels() -> None:
    """
    Siembra los canales iniciales **solo si la tabla está vacía**.

    Corre DESPUÉS de ``seed_environments()`` (ver ``main.py``): necesita ``production`` ya
    sembrado para enlazar ``stable``. El slug se compara en Python y no con
    ``WHERE slug = :x``: MySQL/MariaDB comparan case-insensitive y PostgreSQL no, y el slug
    es lo que el catálogo trata como identidad.
    """
    from app.models.environment import Environment
    from app.models.release_channel import ReleaseChannel

    session = Database().get_declarative_base_session()
    try:
        if session.query(ReleaseChannel.id).first() is not None:
            return  # ya configurado: es del operador, no se toca
        environment_id_by_slug = {
            slug: environment_id
            for environment_id, slug in session.query(Environment.id, Environment.slug).all()
        }
        for row in release_channel_seed_rows():
            environment_slug = row["environment_slug"]
            linked_environment_id = (
                environment_id_by_slug.get(environment_slug)
                if environment_slug is not None
                else None
            )
            session.add(
                ReleaseChannel(
                    name=row["name"],
                    slug=row["slug"],
                    resolution_mode=row["resolution_mode"],
                    environment_id=linked_environment_id,
                )
            )
        session.commit()
        logger.info(
            "Catálogo de canales de liberación sembrado: %d filas (tabla vacía).",
            len(release_channel_seed_rows()),
        )
    except Exception:  # noqa: BLE001 — el seeding no debe tumbar el arranque
        session.rollback()
        logger.exception("No se pudo sembrar el catálogo de canales de liberación.")
    finally:
        session.close()
