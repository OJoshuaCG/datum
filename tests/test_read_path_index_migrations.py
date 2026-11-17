"""
Los índices de lectura que crean las migraciones ``a8c2e4f6b1d9``, ``b9d3f5a7c2e1`` y
``c4e6a8b0d2f3`` tienen que estar declarados TAMBIÉN en el ``__table_args__`` del modelo, con
las mismas columnas en el mismo orden.

Si falta en el modelo, ``alembic revision --autogenerate`` propone borrarlo, y un esquema creado
con ``Base.metadata.create_all`` (tests, dev rápido) no lo tiene. Si las columnas difieren, el
orden de paginación que el índice debía resolver vuelve a ser un filesort.
"""

from pathlib import Path

import pytest

import app.models  # noqa: F401 — registra todos los modelos en Base.metadata
from app.models.base import Base

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"

# Model files whose ``__table_args__`` these cases pin: app/models/audit_log.py,
# app/models/clone_job.py, app/models/export_job.py, app/models/schema_comparison_item.py,
# plus the query_executions, migration_statement_progress and gateway_sessions models.
# (revisión, tabla, índice, columnas en orden)
_EXPECTED_INDEXES = [
    ("a8c2e4f6b1d9", "query_executions", "ix_qe_server_db_id", ["server_id", "database_name", "id"]),
    ("a8c2e4f6b1d9", "migration_statement_progress", "ix_msp_migration_direction",
     ["model_migration_id", "direction"]),
    ("b9d3f5a7c2e1", "gateway_sessions", "ix_gs_last_seen_at", ["last_seen_at"]),
    ("c4e6a8b0d2f3", "audit_log", "ix_audit_log_status_id", ["status", "id"]),
    ("c4e6a8b0d2f3", "audit_log", "ix_audit_log_actor_type_id", ["actor_type", "id"]),
    ("c4e6a8b0d2f3", "audit_log", "ix_audit_log_admin_username_id", ["admin_username", "id"]),
    ("c4e6a8b0d2f3", "clone_jobs", "ix_clone_jobs_created_at_id", ["created_at", "id"]),
    ("c4e6a8b0d2f3", "clone_job_items", "ix_clone_job_items_job_seq", ["job_id", "seq"]),
    ("c4e6a8b0d2f3", "export_job_items", "ix_export_job_items_job_seq", ["job_id", "seq"]),
    ("c4e6a8b0d2f3", "schema_comparison_items", "ix_schema_comparison_items_cmp_seq",
     ["comparison_id", "seq"]),
]


_MIGRATION_FILES = {
    "a8c2e4f6b1d9": "20261117_1100_a8c2e4f6b1d9_read_path_indexes.py",
    "b9d3f5a7c2e1": "20261117_1200_b9d3f5a7c2e1_gateway_sessions_last_seen_index.py",
    "c4e6a8b0d2f3": "20261117_1600_c4e6a8b0d2f3_listing_composite_indexes.py",
}


def _migration_source(revision: str) -> str:
    return (_VERSIONS / _MIGRATION_FILES[revision]).read_text(encoding="utf-8")


@pytest.mark.parametrize(("revision", "table", "index_name", "columns"), _EXPECTED_INDEXES)
def test_the_migration_creates_the_index(revision, table, index_name, columns):
    source = _migration_source(revision)

    assert f'"{index_name}"' in source
    assert f'"{table}"' in source


@pytest.mark.parametrize(("revision", "table", "index_name", "columns"), _EXPECTED_INDEXES)
def test_the_model_declares_the_same_index(revision, table, index_name, columns):
    indexes = {index.name: index for index in Base.metadata.tables[table].indexes}

    assert index_name in indexes, f"{table}.{index_name} falta en __table_args__ del modelo"
    assert [column.name for column in indexes[index_name].columns] == columns
    assert not indexes[index_name].unique


@pytest.mark.parametrize("index_name", [name for _, _, name, _ in _EXPECTED_INDEXES])
def test_index_names_fit_the_engine_limit(index_name):
    # PostgreSQL trunca a 63 y MySQL rechaza más de 64: el nombre explícito no puede pasarse.
    assert len(index_name) <= 63
