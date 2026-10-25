"""migration_destructive_approvals y el último editor del SQL de model_migrations

Revision ID: e4f6a8c0b2d3
Revises: d2f4a6c8e0b1

UNA sola revisión para la tabla de aprobaciones y las dos columnas de ``model_migrations``,
porque son una sola decisión: la regla de cuatro ojos (el aprobador no puede ser el autor NI el
último editor del SQL) necesita las dos mitades. Sin ``last_sql_editor_*`` el aprobador podría ser
quien reescribió el SQL.

- ``migration_destructive_approvals``: una fila por (versión, dirección, alcance). Unicidad de la
  aprobación VIGENTE con ``live_key`` (1 / NULL) más ``scope_key``: un índice único parcial no es
  portable y un NULL dentro del ``UNIQUE`` lo volvería inefectivo. FK ``CASCADE`` a la versión, al
  blueprint y a la base. Los snapshots de autor, editor y actores van SIN FK: son evidencia y
  sobreviven al borrado del usuario. NO hay ``CHECK`` sobre ``managed_database_id``: MySQL lo
  prohíbe sobre una columna con FK ``CASCADE`` (el controller valida esa consistencia).
- ``model_migrations.last_sql_editor_admin_id`` / ``last_sql_editor_username``: NULL = editor
  desconocido. **No hay backfill**: atribuir una edición que nunca se registró sería inventar un
  editor, y la aprobación de una versión así queda marcada ``author_unknown``.

**Idempotente paso a paso** (MySQL/MariaDB no tienen DDL transaccional: una migración que muere
a mitad deja aplicado lo que ya corrió con ``alembic_version`` atrasado). La tabla, cada columna
y cada índice se crean solo si faltan, con un inspector NUEVO en cada consulta. Las FK y las PK se
dejan SIN nombre: los nombres de la convención superan el límite del motor (64 en MySQL, 63 en
PostgreSQL) y se guardan truncados con un hash, así que nombrarlos acá divergiría del modelo.

**Downgrade** introspectivo: borra solo lo que existe y NUNCA nombra una constraint a mano. Bajar
esto **borra todas las aprobaciones** (las migraciones destructivas vuelven a quedar bloqueadas en
los entornos protegidos hasta que se vuelvan a aprobar) y el rastro del último editor.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4f6a8c0b2d3"
down_revision: Union[str, None] = "d2f4a6c8e0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APPROVALS_TABLE = "migration_destructive_approvals"
_MIGRATIONS_TABLE = "model_migrations"

_DIRECTION_LENGTH = 4
_SCOPE_LENGTH = 16
_SCOPE_KEY_LENGTH = 32
_SQL_HASH_LENGTH = 64
_HASH_BASIS_LENGTH = 16
_VERSION_LABEL_LENGTH = 10
_SUMMARY_LENGTH = 500
_REASON_LENGTH = 500
_ACTOR_USERNAME_LENGTH = 128

# Literales del vocabulario cerrado (``destructive_approval_catalog``). Se declaran acá y no se
# importan, para que la migración no cambie si el servicio evoluciona: una migración ya aplicada
# describe el esquema de ESE momento.
_HASH_BASIS_RAW_SOURCE_V1 = "raw_source_v1"


def _tables(bind) -> set[str]:
    # Inspector NUEVO en cada llamada: cachea, y entre pasos de esta migración el esquema cambia.
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(table)}


def _create_index_if_missing(
    bind, index_name: str, table: str, columns: list[str], *, unique: bool = False
) -> None:
    if index_name not in _indexes(bind, table):
        op.create_index(index_name, table, columns, unique=unique)


def _actor_snapshot_columns() -> list[sa.Column]:
    """Autor, editor y aprobador copiados como texto: sobreviven al borrado del usuario."""
    return [
        sa.Column(
            "author_admin_id",
            sa.Integer(),
            nullable=True,
            comment="Creador de la versión al aprobar (sin FK). NULL = desconocido",
        ),
        sa.Column(
            "author_username",
            sa.String(length=_ACTOR_USERNAME_LENGTH),
            nullable=True,
            comment="Nombre del creador de la versión al aprobar",
        ),
        sa.Column(
            "editor_admin_id",
            sa.Integer(),
            nullable=True,
            comment="Último editor del SQL al aprobar (sin FK). NULL = desconocido",
        ),
        sa.Column(
            "editor_username",
            sa.String(length=_ACTOR_USERNAME_LENGTH),
            nullable=True,
            comment="Nombre del último editor del SQL al aprobar",
        ),
    ]


def _create_approvals_table() -> None:
    op.create_table(
        _APPROVALS_TABLE,
        sa.Column(
            "id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
            comment="ID único de la aprobación",
        ),
        sa.Column(
            "model_migration_id",
            sa.Integer(),
            nullable=False,
            comment=(
                "Versión aprobada. Se identifica por id y no por número de versión: borrar una "
                "versión renumera las siguientes. CASCADE: sin la versión no hay nada que aprobar"
            ),
        ),
        sa.Column(
            "database_model_id",
            sa.Integer(),
            nullable=False,
            comment=(
                "Blueprint de la versión (desnormalizado para listar las aprobaciones de un "
                "blueprint)"
            ),
        ),
        sa.Column(
            "direction",
            sa.String(length=_DIRECTION_LENGTH),
            nullable=False,
            comment="Dirección aprobada: up (aplicar) o down (rollback). Una no cubre a la otra",
        ),
        sa.Column(
            "scope",
            sa.String(length=_SCOPE_LENGTH),
            nullable=False,
            comment=(
                "Alcance: database (una base) o blueprint (todas las bases del blueprint en "
                "entornos protegidos, incluidas las futuras)"
            ),
        ),
        sa.Column(
            "managed_database_id",
            sa.Integer(),
            nullable=True,
            comment="Base cubierta cuando scope=database; NULL cuando scope=blueprint",
        ),
        sa.Column(
            "scope_key",
            sa.String(length=_SCOPE_KEY_LENGTH),
            nullable=False,
            comment=(
                "'blueprint' o 'database:{id}'. Existe porque managed_database_id es NULL en el "
                "alcance blueprint y un NULL dentro del UNIQUE lo dejaría sin efecto"
            ),
        ),
        sa.Column(
            "sql_hash",
            sa.CHAR(length=_SQL_HASH_LENGTH),
            nullable=False,
            comment=(
                "SHA-256 del SQL fuente aprobado (ver hash_basis). La aprobación solo vale "
                "mientras el SQL actual produzca el mismo hash"
            ),
        ),
        sa.Column(
            "hash_basis",
            sa.String(length=_HASH_BASIS_LENGTH),
            server_default=_HASH_BASIS_RAW_SOURCE_V1,
            nullable=False,
            comment=(
                "Cómo se calculó sql_hash. Una base distinta de la vigente fuerza una "
                "re-aprobación en vez de comparar hashes de cosas distintas"
            ),
        ),
        sa.Column(
            "version_label",
            sa.String(length=_VERSION_LABEL_LENGTH),
            nullable=False,
            comment="Número de versión al aprobar ('0003'), copiado como texto para el rastro",
        ),
        sa.Column(
            "destructive_summary",
            sa.String(length=_SUMMARY_LENGTH),
            nullable=True,
            comment="Resumen de qué destruye el SQL, mostrado al aprobador",
        ),
        *_actor_snapshot_columns(),
        sa.Column(
            "author_unknown",
            sa.Boolean(),
            server_default="0",
            nullable=False,
            comment=(
                "True si el creador o el último editor eran desconocidos al aprobar (versiones "
                "anteriores a esa trazabilidad): la regla de cuatro ojos no pudo verificarse "
                "del todo"
            ),
        ),
        sa.Column(
            "reason",
            sa.String(length=_REASON_LENGTH),
            nullable=False,
            comment="Motivo declarado por el aprobador (10 a 500 caracteres)",
        ),
        sa.Column(
            "approved_by_admin_id",
            sa.Integer(),
            nullable=False,
            comment="Usuario del gateway que aprobó (sin FK)",
        ),
        sa.Column(
            "approved_by_username",
            sa.String(length=_ACTOR_USERNAME_LENGTH),
            nullable=False,
            comment="Nombre del aprobador al momento de aprobar",
        ),
        sa.Column(
            "live_key",
            sa.SmallInteger(),
            nullable=True,
            comment=(
                "1 si la aprobación está vigente, NULL si fue revocada o reemplazada; base del "
                "UNIQUE"
            ),
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(),
            nullable=True,
            comment="Fecha y hora (UTC) de la revocación o del reemplazo",
        ),
        sa.Column(
            "revoked_by_admin_id",
            sa.Integer(),
            nullable=True,
            comment="Quien revocó (sin FK). NULL si sigue vigente",
        ),
        sa.Column(
            "revoked_by_username",
            sa.String(length=_ACTOR_USERNAME_LENGTH),
            nullable=True,
            comment="Nombre de quien revocó",
        ),
        sa.Column(
            "revoke_reason",
            sa.String(length=_REASON_LENGTH),
            nullable=True,
            comment="Motivo de la revocación; 'superseded' si la reemplazó una aprobación nueva",
        ),
        sa.Column(
            "last_executed_at",
            sa.DateTime(),
            nullable=True,
            comment="Última vez (UTC) que una ejecución consumió esta aprobación",
        ),
        sa.Column(
            "last_executed_by_admin_id",
            sa.Integer(),
            nullable=True,
            comment="Usuario que ejecutó por última vez (sin FK)",
        ),
        sa.Column(
            "last_executed_by_username",
            sa.String(length=_ACTOR_USERNAME_LENGTH),
            nullable=True,
            comment="Nombre de quien ejecutó por última vez",
        ),
        sa.Column(
            "execution_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="Cuántas ejecuciones consumieron esta aprobación",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
            comment="Fecha y hora de creación del registro",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
            comment="Fecha y hora de última actualización del registro",
        ),
        sa.ForeignKeyConstraint(["model_migration_id"], ["model_migrations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["database_model_id"], ["database_models.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["managed_database_id"], ["managed_databases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "model_migration_id", "direction", "scope_key", "live_key", name="uq_mda_live"
        ),
        sa.CheckConstraint(
            "direction IN ('up', 'down')",
            name="ck_migration_destructive_approvals_direction",
        ),
        sa.CheckConstraint(
            "scope IN ('database', 'blueprint')",
            name="ck_migration_destructive_approvals_scope",
        ),
        # `live_key IS NOT NULL` explícito: con NULL, `live_key = 1` da NULL y un CHECK que
        # evalúa a NULL PASA, así que una fila vigente sin `live_key` se saltearía el UNIQUE.
        sa.CheckConstraint(
            "(revoked_at IS NULL AND live_key IS NOT NULL AND live_key = 1) "
            "OR (revoked_at IS NOT NULL AND live_key IS NULL)",
            name="ck_migration_destructive_approvals_live_key_revoked",
        ),
        comment=(
            "Aprobaciones de una segunda persona para ejecutar migraciones destructivas en "
            "entornos protegidos. Una fila por versión, dirección y alcance"
        ),
    )


def _last_sql_editor_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "last_sql_editor_admin_id",
            sa.Integer(),
            nullable=True,
            comment=(
                "Persona que editó por última vez el SQL de subida o de bajada (sin FK). "
                "NULL = desconocido (versión anterior a esta columna)"
            ),
        ),
        sa.Column(
            "last_sql_editor_username",
            sa.String(length=_ACTOR_USERNAME_LENGTH),
            nullable=True,
            comment="Nombre del último editor del SQL. NULL = desconocido (versión anterior)",
        ),
    ]


def upgrade() -> None:
    bind = op.get_bind()

    if _APPROVALS_TABLE not in _tables(bind):
        _create_approvals_table()
    _create_index_if_missing(
        bind, "ix_mda_model_id", _APPROVALS_TABLE, ["database_model_id", "id"]
    )
    _create_index_if_missing(bind, "ix_mda_managed_db", _APPROVALS_TABLE, ["managed_database_id"])

    for column in _last_sql_editor_columns():
        # Inspector nuevo por columna: la primera alta cambia el esquema que ve la segunda.
        if column.name not in _columns(bind, _MIGRATIONS_TABLE):
            op.add_column(_MIGRATIONS_TABLE, column)


def downgrade() -> None:
    bind = op.get_bind()

    if _APPROVALS_TABLE in _tables(bind):
        op.drop_table(_APPROVALS_TABLE)

    for column in reversed(_last_sql_editor_columns()):
        if column.name in _columns(bind, _MIGRATIONS_TABLE):
            op.drop_column(_MIGRATIONS_TABLE, column.name)
