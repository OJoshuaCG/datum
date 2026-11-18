"""
Ejecutor de las revisiones de blueprint: ``upgrade``, ``downgrade`` y ``stamp``.

Dos modos, elegidos por ``MIGRATION_EXECUTOR`` (``app/core/environments.py``):

- ``alembic`` (default): ``alembic.command.*`` a través del env.py compartido
  (``migrations/_shared/env.py``). ``command.*`` instala el ``EnvironmentContext`` en el
  proxy GLOBAL de módulo ``alembic.context`` y las operaciones en el proxy global
  ``alembic.op``; dos hilos a la vez se pisarían el contexto. Por eso cada llamada espera
  en ``_ALEMBIC_LOCK``, una única cola para todo el servicio (con ``WORKERS=1``, para todo
  el despliegue): un ALTER de 10 minutos sobre la base A hace esperar 10 minutos a la B.

- ``native``: arma el MISMO ``MigrationContext`` de Alembic que arma el env.py, pero como
  instancia local, sin pasar por ningún proxy global, así que no necesita el lock. Las
  bases distintas se migran en paralelo; la MISMA base sigue serializada por su advisory
  lock (``GET_LOCK`` / ``pg_advisory_lock``), que el runner toma antes de llamar acá.

Por qué ``native`` deja la tabla de versión idéntica a ``alembic``: no reimplementa la
contabilidad. ``MigrationContext.run_migrations`` (con su ``HeadMaintainer``) es quien
crea la tabla de versión y emite el INSERT/UPDATE/DELETE de ``version_num`` en los dos
modos; lo único que cambia es quién construye el contexto. Las opciones son las que fija
el env.py compartido (``version_table``, ``transaction_per_migration=True``,
``target_metadata=None``) más las que ``alembic.command`` le pasa al ``EnvironmentContext``
(``fn``, ``script``, ``purge``). Si una se cambia en un lado, hay que cambiarla en el otro:
el test de paridad (``tests/test_migration_executor.py``) es el que lo detecta.

El único punto donde el código generado toca un global es ``op.get_bind()``
(``MigrationRunner._render_revision``). En ``native`` cada módulo de revisión de la corrida
recibe su propio ``op``, un objeto que solo sabe devolver la conexión de esa corrida. Es
seguro porque Alembic carga cada archivo de revisión como un módulo nuevo que NO registra
en ``sys.modules`` (``alembic.util.pyfiles.load_module_py``): dos corridas en paralelo nunca
comparten el módulo.

Dependencia interna: ``ScriptDirectory._upgrade_revs/_downgrade_revs/_stamp_revs`` son
privados. Son exactamente los que usa ``alembic.command`` (verificado en Alembic 1.18.4);
una versión nueva de Alembic que los cambie rompe el test de paridad, no la producción en
silencio.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from types import ModuleType
from typing import Any

from alembic import command
from alembic import op as alembic_op_proxy
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import to_list, to_tuple
from sqlalchemy.engine import Connection

from app.core.environments import (
    MIGRATION_EXECUTOR,
    MIGRATION_EXECUTOR_ALEMBIC,
    MIGRATION_EXECUTOR_NATIVE,
)

# Cola global de ``alembic.command``: ver el docstring del módulo. Solo la toma el modo
# ``alembic``.
_ALEMBIC_LOCK = threading.Lock()

_DEFAULT_VERSION_TABLE = "alembic_version"


class RevisionBindOperations:
    """``op`` de los módulos de revisión de UNA corrida ``native``.

    Reemplaza al proxy global ``alembic.op``. El codegen solo usa ``op.get_bind()``; si un
    módulo de revisión llamara cualquier otra operación, el ``AttributeError`` es deliberado:
    mejor fallar que caer de nuevo en el proxy global compartido entre hilos.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get_bind(self) -> Connection:
        return self._connection


def upgrade(cfg: Config, revision: str, executor: str | None = None) -> None:
    """Lleva la base a ``revision`` (absoluta, como la usa ``MigrationRunner._apply_one``)."""
    if _resolve_executor(executor) == MIGRATION_EXECUTOR_ALEMBIC:
        with _ALEMBIC_LOCK:
            command.upgrade(cfg, revision)
        return

    def plan_upgrade_steps(script: ScriptDirectory, heads: Any) -> Any:
        return script._upgrade_revs(revision, heads)

    _run_native(cfg, plan_upgrade_steps, purge=False)


def downgrade(cfg: Config, revision: str, executor: str | None = None) -> None:
    """Baja la base a ``revision``. ``"-1"`` se resuelve contra la versión GRABADA en la base,
    igual que en ``alembic.command.downgrade``."""
    if _resolve_executor(executor) == MIGRATION_EXECUTOR_ALEMBIC:
        with _ALEMBIC_LOCK:
            command.downgrade(cfg, revision)
        return

    def plan_downgrade_steps(script: ScriptDirectory, heads: Any) -> Any:
        return script._downgrade_revs(revision, heads)

    _run_native(cfg, plan_downgrade_steps, purge=False)


def stamp(
    cfg: Config, revision: str, purge: bool = False, executor: str | None = None
) -> None:
    """Mueve el puntero de versión sin ejecutar DDL.

    Sin ``purge``, el puntero ACTUAL tiene que existir en la cadena de revisiones (si no,
    ``CommandError``, igual que ``alembic.command.stamp``); con ``purge``, se borra la tabla
    de versión antes de grabar el nuevo.
    """
    if _resolve_executor(executor) == MIGRATION_EXECUTOR_ALEMBIC:
        with _ALEMBIC_LOCK:
            command.stamp(cfg, revision, purge=purge)
        return

    destination_revisions = to_tuple(to_list(revision))

    def plan_stamp_steps(script: ScriptDirectory, heads: Any) -> Any:
        return script._stamp_revs(destination_revisions, heads)

    _run_native(cfg, plan_stamp_steps, purge=purge)


def _resolve_executor(executor: str | None) -> str:
    resolved = MIGRATION_EXECUTOR if executor is None else executor
    if resolved not in (MIGRATION_EXECUTOR_ALEMBIC, MIGRATION_EXECUTOR_NATIVE):
        raise ValueError(f"Ejecutor de migraciones desconocido: {resolved!r}")
    return resolved


def _run_native(
    cfg: Config,
    plan_steps: Callable[[ScriptDirectory, Any], Any],
    *,
    purge: bool,
) -> None:
    """Corre ``MigrationContext.run_migrations`` sin ``EnvironmentContext`` ni proxies.

    Replica ``run_migrations_online`` de ``migrations/_shared/env.py`` paso a paso: misma
    conexión inyectada, mismas opciones y el mismo ``begin_transaction`` exterior (con
    ``transaction_per_migration=True`` es un no-op; cada revisión abre la suya, y en
    PostgreSQL su DDL y su cambio de versión confirman juntos).
    """
    connection = cfg.attributes.get("connection")
    if connection is None:
        raise RuntimeError("MigrationRunner debe inyectar 'connection' en config.attributes.")
    version_table = cfg.attributes.get("version_table", _DEFAULT_VERSION_TABLE)

    script = ScriptDirectory.from_config(cfg)
    _bind_revision_modules(script, connection)

    def migrations_fn(heads: Any, _context: MigrationContext) -> Any:
        return plan_steps(script, heads)

    migration_context = MigrationContext.configure(
        connection=connection,
        opts={
            "script": script,
            "fn": migrations_fn,
            "purge": purge,
            "as_sql": False,
            "version_table": version_table,
            "transaction_per_migration": True,
            "target_metadata": None,
        },
    )
    with migration_context.begin_transaction():
        migration_context.run_migrations()


def _bind_revision_modules(script: ScriptDirectory, connection: Connection) -> None:
    """Le da a cada módulo de revisión de ESTA corrida un ``op`` propio.

    Solo reemplaza el ``op`` que es el proxy global de Alembic: un módulo que no lo importó
    (no los genera el codegen hoy) queda intacto.
    """
    bind_operations = RevisionBindOperations(connection)
    for revision_script in script.walk_revisions():
        revision_module: ModuleType = revision_script.module
        if getattr(revision_module, "op", None) is alembic_op_proxy:
            revision_module.op = bind_operations
