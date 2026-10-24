"""release_channels, blueprint_channel_pointers y blueprint_channel_pointer_events (+ seed)

Revision ID: d2f4a6c8e0b1
Revises: b7d9f1a3c5e8

UNA sola revisión para las tres tablas y el seed, porque son una sola decisión: un canal sin
tabla de punteros no sirve ninguna versión, y los punteros sin su bitácora mueven versiones sin
dejar rastro.

- ``release_channels``: catálogo de canales. ``resolution_mode`` es un VARCHAR con CHECK (no un
  ENUM: agregar un modo es un deploy de código de todas formas). Un segundo CHECK exige entorno
  en modo ``derived``; convive con la FK ``RESTRICT`` al entorno porque MySQL solo prohíbe el
  CHECK sobre columnas de FKs con ``CASCADE`` / ``SET NULL``.
- ``blueprint_channel_pointers``: PK compuesta (blueprint, canal). FK ``CASCADE`` a los dos
  lados y ``SET NULL`` a ``model_migrations`` (borrar una versión no borra el puntero).
- ``blueprint_channel_pointer_events``: bitácora append-only. ``from_migration_id`` /
  ``to_migration_id`` y los actores SIN FK: es evidencia y sobrevive al borrado de lo que cita.
- Seed ``stable`` (manual, enlazado a ``production`` si existe) y ``development`` (latest),
  **solo si ``release_channels`` está vacía**: es política del operador y un seed que "agrega lo
  que falta" resucitaría un canal borrado a propósito.

**Idempotente paso a paso** (MySQL/MariaDB no tienen DDL transaccional: una migración que muere
a mitad deja aplicado lo que ya corrió con ``alembic_version`` atrasado). Cada tabla e índice se
crea solo si falta, con un inspector NUEVO en cada consulta. Las FK y las PK se dejan SIN nombre:
los nombres de la convención superan el límite del motor y se guardan truncados con un hash, así
que nombrarlos acá divergería del modelo.

**Downgrade** introspectivo: borra solo lo que existe, en orden de dependencia (bitácora,
punteros, canales), y NUNCA nombra una constraint a mano. Bajar esto **borra los punteros y su
historial**: los canales dejan de servir versión hasta que se vuelvan a promover.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2f4a6c8e0b1"
down_revision: Union[str, None] = "b7d9f1a3c5e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CHANNELS_TABLE = "release_channels"
_POINTERS_TABLE = "blueprint_channel_pointers"
_EVENTS_TABLE = "blueprint_channel_pointer_events"

_NAME_LENGTH = 60
_SLUG_LENGTH = 60
_MODE_LENGTH = 16
_EVENT_TYPE_LENGTH = 16
_VERSION_LENGTH = 10
_NOTE_LENGTH = 500
_ACTOR_USERNAME_LENGTH = 128
_ACTOR_TYPE_LENGTH = 16

# Espejo de ``release_channel_catalog.release_channel_seed_rows()``. Se declara literal (sin
# importar el servicio) para que la migración no cambie si el servicio evoluciona; el test
# ``tests/test_release_channel_catalog.py`` compara las dos listas.
_SEED_ROWS = [
    {
        "name": "Estable",
        "slug": "stable",
        "resolution_mode": "manual",
        "environment_slug": "production",
    },
    {
        "name": "Desarrollo",
        "slug": "development",
        "resolution_mode": "latest",
        "environment_slug": None,
    },
]


def _tables(bind) -> set[str]:
    # Inspector NUEVO en cada llamada: cachea, y entre pasos de esta migración el esquema cambia.
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(table)}


def _create_index_if_missing(
    bind, index_name: str, table: str, columns: list[str], *, unique: bool = False
) -> None:
    if index_name not in _indexes(bind, table):
        op.create_index(index_name, table, columns, unique=unique)


def _created_at_column() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(),
        server_default=sa.text("(CURRENT_TIMESTAMP)"),
        nullable=False,
        comment="Fecha y hora de creación del registro",
    )


def _updated_at_column() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(),
        server_default=sa.text("(CURRENT_TIMESTAMP)"),
        nullable=False,
        comment="Fecha y hora de última actualización del registro",
    )


def _create_channels_table() -> None:
    op.create_table(
        _CHANNELS_TABLE,
        sa.Column(
            "id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
            comment="ID único del canal",
        ),
        sa.Column(
            "name",
            sa.String(length=_NAME_LENGTH),
            nullable=False,
            comment="Nombre visible del canal. Único",
        ),
        sa.Column(
            "slug",
            sa.String(length=_SLUG_LENGTH),
            nullable=False,
            comment=(
                "Identificador estable del canal que citan las integraciones. Inmutable tras "
                "crearlo"
            ),
        ),
        sa.Column(
            "resolution_mode",
            sa.String(length=_MODE_LENGTH),
            server_default="manual",
            nullable=False,
            comment=(
                "Cómo se resuelve la versión del canal: manual (puntero movido por una "
                "persona), derived (mínima versión viva en el entorno enlazado) o latest "
                "(punta del blueprint)"
            ),
        ),
        sa.Column(
            "environment_id",
            sa.Integer(),
            nullable=True,
            comment=(
                "Entorno enlazado. Obligatorio en modo derived. RESTRICT: no se puede borrar "
                "un entorno mientras un canal lo use"
            ),
        ),
        _created_at_column(),
        _updated_at_column(),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "resolution_mode IN ('manual', 'derived', 'latest')",
            name="ck_release_channels_resolution_mode",
        ),
        sa.CheckConstraint(
            "resolution_mode <> 'derived' OR environment_id IS NOT NULL",
            name="ck_release_channels_derived_needs_environment",
        ),
        comment=(
            "Canales de liberación de blueprints (stable, development...). Política del "
            "operador: el seed solo corre con la tabla vacía"
        ),
    )


def _create_pointers_table() -> None:
    op.create_table(
        _POINTERS_TABLE,
        sa.Column(
            "database_model_id",
            sa.Integer(),
            nullable=False,
            comment="Blueprint (database_models.id). Si se borra, el puntero se borra",
        ),
        sa.Column(
            "release_channel_id",
            sa.Integer(),
            nullable=False,
            comment="Canal. Si se borra, el puntero se borra",
        ),
        sa.Column(
            "model_migration_id",
            sa.Integer(),
            nullable=True,
            comment=(
                "Versión apuntada (model_migrations.id). NULL = el canal aún no tiene versión"
            ),
        ),
        _created_at_column(),
        _updated_at_column(),
        sa.ForeignKeyConstraint(["database_model_id"], ["database_models.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_channel_id"], ["release_channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_migration_id"], ["model_migrations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("database_model_id", "release_channel_id"),
        comment=(
            "Puntero de un blueprint en un canal: la versión que ese canal sirve para ese "
            "blueprint"
        ),
    )


def _create_events_table() -> None:
    op.create_table(
        _EVENTS_TABLE,
        sa.Column(
            "id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
            comment="ID único del evento",
        ),
        sa.Column(
            "database_model_id",
            sa.Integer(),
            nullable=False,
            comment="Blueprint del puntero",
        ),
        sa.Column(
            "release_channel_id",
            sa.Integer(),
            nullable=False,
            comment="Canal del puntero",
        ),
        sa.Column(
            "event_type",
            sa.String(length=_EVENT_TYPE_LENGTH),
            nullable=False,
            comment=(
                "promote (avance), demote (retroceso confirmado) o version_deleted "
                "(movimiento automático)"
            ),
        ),
        sa.Column(
            "from_migration_id",
            sa.Integer(),
            nullable=True,
            comment="model_migrations.id previo. Sin FK: evidencia",
        ),
        sa.Column(
            "to_migration_id",
            sa.Integer(),
            nullable=True,
            comment="model_migrations.id nuevo. Sin FK: evidencia",
        ),
        sa.Column(
            "from_version",
            sa.String(length=_VERSION_LENGTH),
            nullable=True,
            comment="Versión previa, copiada como texto",
        ),
        sa.Column(
            "to_version",
            sa.String(length=_VERSION_LENGTH),
            nullable=True,
            comment="Versión nueva, copiada como texto",
        ),
        sa.Column(
            "note",
            sa.String(length=_NOTE_LENGTH),
            nullable=True,
            comment="Motivo libre del movimiento",
        ),
        sa.Column(
            "actor_admin_id",
            sa.Integer(),
            nullable=True,
            comment="Usuario del gateway que movió el puntero. Sin FK",
        ),
        sa.Column(
            "actor_username",
            sa.String(length=_ACTOR_USERNAME_LENGTH),
            nullable=False,
            comment="Nombre del actor al momento del evento (copiado: sobrevive al usuario)",
        ),
        sa.Column(
            "actor_type",
            sa.String(length=_ACTOR_TYPE_LENGTH),
            nullable=False,
            comment="Tipo de actor: sesión de usuario, token de integración o sistema",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
            comment="Momento del evento (UTC)",
        ),
        sa.ForeignKeyConstraint(["database_model_id"], ["database_models.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_channel_id"], ["release_channels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "event_type IN ('promote', 'demote', 'version_deleted')",
            name="ck_blueprint_channel_pointer_events_event_type",
        ),
        comment=(
            "Bitácora append-only de los movimientos de un puntero de canal. Sin FK a las "
            "versiones: sobrevive a su borrado"
        ),
    )


def _seed_channels_if_empty(bind) -> None:
    """Siembra solo con la tabla vacía: si hay una fila, es del operador y no se toca."""
    existing_row = bind.execute(sa.text(f"SELECT 1 FROM {_CHANNELS_TABLE} LIMIT 1")).first()
    if existing_row is not None:
        return

    channels_table = sa.table(
        _CHANNELS_TABLE,
        sa.column("name", sa.String),
        sa.column("slug", sa.String),
        sa.column("resolution_mode", sa.String),
        sa.column("environment_id", sa.Integer),
    )
    # Se compara en Python, igual que ``seed_release_channels``: ``WHERE slug = :slug`` es
    # insensible a mayúsculas en MySQL y sensible en PostgreSQL, y el seed tiene que enlazar el
    # mismo entorno en los dos motores.
    environment_id_by_slug = {
        environment_slug: environment_id
        for environment_id, environment_slug in bind.execute(
            sa.text("SELECT id, slug FROM environments")
        )
    }
    rows_to_insert = []
    for seed_row in _SEED_ROWS:
        linked_environment_id = None
        environment_slug = seed_row["environment_slug"]
        if environment_slug is not None:
            linked_environment_id = environment_id_by_slug.get(environment_slug)
        rows_to_insert.append(
            {
                "name": seed_row["name"],
                "slug": seed_row["slug"],
                "resolution_mode": seed_row["resolution_mode"],
                "environment_id": linked_environment_id,
            }
        )
    op.bulk_insert(channels_table, rows_to_insert)


def upgrade() -> None:
    bind = op.get_bind()

    if _CHANNELS_TABLE not in _tables(bind):
        _create_channels_table()
    _create_index_if_missing(bind, "ix_release_channels_name", _CHANNELS_TABLE, ["name"], unique=True)
    _create_index_if_missing(bind, "ix_release_channels_slug", _CHANNELS_TABLE, ["slug"], unique=True)
    _create_index_if_missing(
        bind, "ix_release_channels_environment_id", _CHANNELS_TABLE, ["environment_id"]
    )

    if _POINTERS_TABLE not in _tables(bind):
        _create_pointers_table()
    _create_index_if_missing(
        bind,
        "ix_blueprint_channel_pointers_release_channel_id",
        _POINTERS_TABLE,
        ["release_channel_id"],
    )
    _create_index_if_missing(
        bind,
        "ix_blueprint_channel_pointers_model_migration_id",
        _POINTERS_TABLE,
        ["model_migration_id"],
    )

    if _EVENTS_TABLE not in _tables(bind):
        _create_events_table()
    _create_index_if_missing(
        bind,
        "ix_blueprint_channel_pointer_events_model_channel_id",
        _EVENTS_TABLE,
        ["database_model_id", "release_channel_id", "id"],
    )

    _seed_channels_if_empty(bind)


def downgrade() -> None:
    bind = op.get_bind()
    # Orden de dependencia: la bitácora y los punteros referencian al canal.
    for table_name in (_EVENTS_TABLE, _POINTERS_TABLE, _CHANNELS_TABLE):
        if table_name in _tables(bind):
            op.drop_table(table_name)
