"""
``find_access_context`` con la fila de ``users`` ya leída en el mismo request.

La autenticación lee la fila del usuario para chequear ``is_active`` y, acto seguido,
``find_access_context`` volvía a leer ``gateway_role`` de la MISMA fila: una consulta repetida
en cada request autenticado. Pasarle ``user_row`` la evita sin cachear nada entre requests.
"""

from app.models.user_model import UserModel


def _recording_model() -> tuple[UserModel, list[str]]:
    """Un ``UserModel`` real cuyo ``execute_query`` anota cada SQL que pasa por él."""
    model = UserModel()
    executed_sql: list[str] = []
    real_db = model.db

    class RecordingDb:
        """Anota los SQL de ``execute_query`` y delega todo lo demás en la BD real."""

        def execute_query(self, query, *args, **kwargs):
            executed_sql.append(str(query))
            return real_db.execute_query(query, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(real_db, name)

    model.db = RecordingDb()
    return model, executed_sql


def _reads_gateway_role(executed_sql: list[str]) -> bool:
    return any("SELECT gateway_role FROM users" in sql for sql in executed_sql)


def test_user_row_from_the_same_request_skips_rereading_the_role(client):
    admin = UserModel().find_by_username("admin")
    model, executed_sql = _recording_model()

    ctx = model.find_access_context(admin["id"], user_row=admin)

    assert not _reads_gateway_role(executed_sql)
    assert ctx["role"] == (admin.get("gateway_role") or "viewer")
    # El resto del contexto se sigue leyendo en cada request.
    assert ctx == UserModel().find_access_context(admin["id"])


def test_without_user_row_the_role_is_read_from_the_database(client):
    admin = UserModel().find_by_username("admin")
    model, executed_sql = _recording_model()

    model.find_access_context(admin["id"])

    assert _reads_gateway_role(executed_sql)


def test_a_user_row_of_another_user_is_ignored(client):
    """Una fila que no es la de ``user_id`` no puede prestarle su rol: se relee de la BD."""
    admin = UserModel().find_by_username("admin")
    model, executed_sql = _recording_model()
    impostor_row = {"id": admin["id"] + 999, "gateway_role": "owner"}

    ctx = model.find_access_context(admin["id"], user_row=impostor_row)

    assert _reads_gateway_role(executed_sql)
    assert ctx == UserModel().find_access_context(admin["id"])
