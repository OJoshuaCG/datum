"""
Veredicto de destructividad PRECALCULADO por versión de blueprint y dirección.

Decidir si el SQL de una versión es destructivo cuesta un parseo con sqlglot por versión,
dirección y motor. ``list_versions`` lo hacía en cada lectura: con un blueprint de 139 versiones
son cientos de análisis distintos contra una ``lru_cache`` de 128 entradas, así que la caché nunca
acertaba. Este módulo guarda el resultado en ``model_migration_destructive_verdicts`` cuando la
versión se crea o se edita, y la lectura solo lo consulta.

Es un DATO DERIVADO, nunca una fuente de verdad. La migración (su SQL y su manifiesto) sigue
mandando, y una fila solo se usa si su ``fingerprint`` coincide con el que producen HOY esos mismos
datos. Cualquier cosa que cambie el veredicto (SQL, overrides, manifiesto, versión de sqlglot,
versión del analizador) cambia la huella, la fila queda inválida y el llamador recalcula. Por eso la
corrección NO depende de haber enganchado todos los puntos de escritura: uno que se escape solo
deja la lectura lenta, nunca equivocada.

Alcance de la garantía: esto acelera el LISTADO. El guard que bloquea ``apply``/``rollback`` en
entornos protegidos (``ManagedMigrationController._destructive_versions`` y
``destructive_approval_policy.destructive_down_versions``) sigue analizando el SQL vigente y no
lee esta tabla; un veredicto guardado desactualizado jamás puede dejar pasar una ejecución.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence

import sqlglot
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.model_migration_destructive_verdict import ModelMigrationDestructiveVerdict
from app.services.db_admin.migrations import MigrationSpec
from app.services.destructive_approval_catalog import DIRECTION_DOWN, DIRECTION_UP

#: Versión de las REGLAS que deciden "destructivo" (``migration_facts``, ``destructive_reasons``,
#: ``effective_down_sql``). Súbela cuando cambies cualquiera de ellas: sin eso, los veredictos
#: guardados con la regla anterior siguen pareciendo válidos y el listado mostraría una
#: clasificación que el guard ya no comparte. La versión de sqlglot ya entra sola en la huella.
ANALYSIS_VERSION = "1"

#: Marca distinta de la cadena vacía, para que ``None`` y ``""`` no produzcan la misma huella.
_NULL_MARKER = "\x00null"


def _frame(field: str | None) -> bytes:
    """
    Un campo con su largo por delante. Sin el largo, ``("ab", "c")`` y ``("a", "bc")`` darían la
    misma huella y dos SQL distintos se confundirían.
    """
    text = _NULL_MARKER if field is None else field
    encoded = text.encode("utf-8")
    return f"{len(encoded)}:".encode("ascii") + encoded


def verdict_fingerprint(spec: MigrationSpec, direction: str) -> str:
    """
    SHA-256 de TODO lo que alimenta el veredicto de esa dirección.

    - ``up``: ``select_up_sql`` elige entre ``up_sql`` y los dos overrides por motor (y para
      PostgreSQL traduce con sqlglot), y la razón ``manifiesto`` sale del flag ``destructive`` de
      las filas del manifiesto.
    - ``down``: ``effective_down_sql`` usa el reverso de las filas del manifiesto cuando este es
      confiable (``usable_manifest`` compara el ``up`` de las filas contra el ``up_sql`` vigente),
      y si no, ``down_sql`` traducido. Por eso entran también los tres ``up`` y cada fila.

    NO entran la versión ni el checksum: renumerar o renombrar no cambia qué destruye el SQL, y
    meterlos invalidaría todo veredicto en cada renumerado.
    """
    if direction not in (DIRECTION_UP, DIRECTION_DOWN):
        raise ValueError(f"Dirección de migración desconocida: {direction!r}.")

    fields: list[str | None] = [
        ANALYSIS_VERSION,
        sqlglot.__version__,
        direction,
        spec.kind,
        str(spec.has_non_portable),
        spec.source_engine,
        spec.up_sql,
        spec.up_sql_mysql,
        spec.up_sql_postgresql,
    ]
    if direction == DIRECTION_UP:
        fields.extend(str(statement.destructive) for statement in spec.manifest)
    else:
        fields.append(spec.down_sql)
        for statement in spec.manifest:
            fields.extend(
                (
                    str(statement.seq),
                    statement.up_sql,
                    statement.down_sql,
                    str(statement.down_confirmed),
                )
            )

    digest = hashlib.sha256()
    for field in fields:
        digest.update(_frame(field))
    return digest.hexdigest()


def encode_reasons(reasons_by_engine: Mapping[str, Sequence[str]]) -> str:
    """JSON estable de ``{motor: [motivo, ...]}``. ``{}`` significa "no destructiva"."""
    return json.dumps(
        {engine: list(reasons) for engine, reasons in reasons_by_engine.items()},
        sort_keys=True,
        ensure_ascii=False,
    )


def decode_reasons(stored_text: str) -> dict[str, tuple[str, ...]] | None:
    """
    Inverso de ``encode_reasons``. ``None`` si el texto no tiene la forma esperada: el llamador lo
    trata como "sin veredicto" y recalcula, en vez de adivinar qué quiso decir una fila dañada.
    """
    try:
        decoded = json.loads(stored_text)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    reasons_by_engine: dict[str, tuple[str, ...]] = {}
    for engine, reasons in decoded.items():
        is_well_formed = isinstance(engine, str) and isinstance(reasons, list)
        if not is_well_formed or not all(isinstance(reason, str) for reason in reasons):
            return None
        reasons_by_engine[engine] = tuple(reasons)
    return reasons_by_engine


def load_verdict_rows(
    session: Session, migration_ids: Iterable[int]
) -> dict[tuple[int, str], ModelMigrationDestructiveVerdict]:
    """Las filas guardadas de esas versiones, indexadas por ``(id de versión, dirección)``."""
    requested_ids = list(migration_ids)
    if not requested_ids:
        return {}
    rows = (
        session.query(ModelMigrationDestructiveVerdict)
        .filter(ModelMigrationDestructiveVerdict.model_migration_id.in_(requested_ids))
        .all()
    )
    return {(row.model_migration_id, row.direction): row for row in rows}


def stored_reasons_if_current(
    stored_rows: Mapping[tuple[int, str], ModelMigrationDestructiveVerdict],
    spec: MigrationSpec,
    direction: str,
) -> dict[str, tuple[str, ...]] | None:
    """Los motivos guardados si la fila existe, está sana y su huella coincide; si no, ``None``."""
    stored_row = stored_rows.get((spec.id, direction))
    if stored_row is None:
        return None
    if stored_row.fingerprint != verdict_fingerprint(spec, direction):
        return None
    return decode_reasons(stored_row.reasons_by_engine)


def store_verdict(
    session: Session,
    spec: MigrationSpec,
    direction: str,
    reasons_by_engine: Mapping[str, Sequence[str]],
) -> None:
    """
    Inserta o reemplaza el veredicto de ``(versión, dirección)``. No hace ``commit``.

    El UNIQUE ``(model_migration_id, direction)`` hace que dos escritores simultáneos no dupliquen
    la fila: el que pierde la carrera recibe ``IntegrityError`` y actualiza la que ganó. Corre en un
    SAVEPOINT para que ese error no deshaga el resto de lo que ya hizo la sesión.
    """
    fingerprint = verdict_fingerprint(spec, direction)
    encoded_reasons = encode_reasons(reasons_by_engine)

    existing = (
        session.query(ModelMigrationDestructiveVerdict)
        .filter(
            ModelMigrationDestructiveVerdict.model_migration_id == spec.id,
            ModelMigrationDestructiveVerdict.direction == direction,
        )
        .first()
    )
    if existing is not None:
        existing.fingerprint = fingerprint
        existing.reasons_by_engine = encoded_reasons
        return

    try:
        with session.begin_nested():
            session.add(
                ModelMigrationDestructiveVerdict(
                    model_migration_id=spec.id,
                    direction=direction,
                    fingerprint=fingerprint,
                    reasons_by_engine=encoded_reasons,
                )
            )
    except IntegrityError:
        lost_race_row = (
            session.query(ModelMigrationDestructiveVerdict)
            .filter(
                ModelMigrationDestructiveVerdict.model_migration_id == spec.id,
                ModelMigrationDestructiveVerdict.direction == direction,
            )
            .one()
        )
        lost_race_row.fingerprint = fingerprint
        lost_race_row.reasons_by_engine = encoded_reasons
