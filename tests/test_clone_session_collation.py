"""
Clon: los objetos con cuerpo (vistas, rutinas, triggers, eventos) conservan la collation de
sesión del origen, y la BD destino hereda la collation del origen entre MySQL y MariaDB.

Son pruebas de las funciones puras; el comportamiento contra un servidor real se verifica
aparte (ver docs/development/decisiones-e-incidentes.md).
"""

from app.controllers.clone_controller import CloneController
from app.services.db_admin.base_adapter import ServerAdapter
from app.services.db_admin.dtos import (
    EventInfo,
    RoutineInfo,
    SchemaSnapshot,
    TriggerInfo,
    ViewInfo,
)

UNICODE_CI = "utf8mb4_unicode_ci"
MYSQL8_DEFAULT = "utf8mb4_0900_ai_ci"


class _FakeTargetAdapter:
    """Adapter destino mínimo: solo declara su dialecto y qué collations conoce."""

    def __init__(self, dialect: str, known_collations: set[str]):
        self.dialect = dialect
        self._known_collations = known_collations

    def supports_charset_combination(self, charset: str, collation: str) -> bool:
        return collation in self._known_collations


def _snapshot_with_one_object_of_each_type() -> SchemaSnapshot:
    return SchemaSnapshot(
        database="origen",
        source_engine="mariadb",
        db_charset="utf8mb4",
        db_collation=UNICODE_CI,
        views=[ViewInfo(name="v1", definition="select 1", collation_connection=UNICODE_CI)],
        routines=[
            RoutineInfo(name="dup", kind="FUNCTION", body="f", collation_connection=UNICODE_CI),
            RoutineInfo(name="dup", kind="PROCEDURE", body="p", collation_connection="utf8mb4_bin"),
        ],
        triggers=[
            TriggerInfo(name="t1", table="x", action="a", collation_connection=UNICODE_CI)
        ],
        events=[EventInfo(name="e1", body="b", collation_connection=UNICODE_CI)],
    )


def test_frozen_collations_are_keyed_like_render_diff():
    by_object = CloneController._frozen_collation_by_object(
        _snapshot_with_one_object_of_each_type()
    )

    assert by_object[("view", "v1")] == UNICODE_CI
    assert by_object[("trigger", "t1")] == UNICODE_CI
    assert by_object[("event", "e1")] == UNICODE_CI
    # Función y procedimiento homónimos no se pisan: render_diff los nombra "KIND:nombre".
    assert by_object[("routine", "FUNCTION:dup")] == UNICODE_CI
    assert by_object[("routine", "PROCEDURE:dup")] == "utf8mb4_bin"


def test_objects_without_frozen_collation_are_left_out():
    snapshot = SchemaSnapshot(
        database="origen",
        source_engine="postgresql",
        views=[ViewInfo(name="v1", definition="select 1")],
    )

    assert CloneController._frozen_collation_by_object(snapshot) == {}


def test_same_engine_keeps_the_source_collation_without_asking_the_target():
    target = _FakeTargetAdapter("mysql", known_collations=set())

    portable = CloneController._portable_collation("mysql", target, MYSQL8_DEFAULT, {})

    assert portable == MYSQL8_DEFAULT


def test_cross_engine_keeps_a_collation_the_target_knows():
    target = _FakeTargetAdapter("mysql", known_collations={UNICODE_CI})

    portable = CloneController._portable_collation("mariadb", target, UNICODE_CI, {})

    assert portable == UNICODE_CI


def test_cross_engine_drops_a_collation_the_target_does_not_know():
    target = _FakeTargetAdapter("mariadb", known_collations={UNICODE_CI})

    portable = CloneController._portable_collation("mysql", target, MYSQL8_DEFAULT, {})

    assert portable is None


def test_postgresql_collations_are_never_portable_to_mysql_family():
    target = _FakeTargetAdapter("mysql", known_collations={"en_US.UTF-8"})

    portable = CloneController._portable_collation("postgresql", target, "en_US.UTF-8", {})

    assert portable is None


def test_database_default_is_inherited_across_mysql_and_mariadb():
    controller = CloneController()
    snapshot = _snapshot_with_one_object_of_each_type()
    target = _FakeTargetAdapter("mysql", known_collations={UNICODE_CI})

    charset, collation = controller._inherited_db_collation(snapshot, target)

    assert (charset, collation) == ("utf8mb4", UNICODE_CI)


def test_database_default_falls_back_to_the_server_default_when_unsupported():
    controller = CloneController()
    snapshot = _snapshot_with_one_object_of_each_type()
    target = _FakeTargetAdapter("mysql", known_collations=set())

    assert controller._inherited_db_collation(snapshot, target) == (None, None)


def test_runner_is_called_without_the_new_kwarg_when_no_statement_needs_it():
    assert CloneController._session_collation_kwargs([None, None]) == {}
    assert CloneController._session_collation_kwargs([None, UNICODE_CI]) == {
        "session_collations": [None, UNICODE_CI]
    }


def test_frozen_collation_is_copied_onto_the_snapshot_dtos():
    views = [ViewInfo(name="v1", definition="select 1")]
    routines = [RoutineInfo(name="f1", kind="FUNCTION", body="f")]
    triggers = [TriggerInfo(name="t1", table="x", action="a")]
    events = [EventInfo(name="e1", body="b")]
    frozen = {
        ("view", "v1"): UNICODE_CI,
        ("function", "f1"): "utf8mb4_bin",
        ("trigger", "t1"): UNICODE_CI,
    }

    ServerAdapter._apply_frozen_collations(
        frozen, views=views, routines=routines, triggers=triggers, events=events
    )

    assert views[0].collation_connection == UNICODE_CI
    assert routines[0].collation_connection == "utf8mb4_bin"
    assert triggers[0].collation_connection == UNICODE_CI
    # El evento no vino en el mapa: queda None en vez de inventar un valor.
    assert events[0].collation_connection is None
