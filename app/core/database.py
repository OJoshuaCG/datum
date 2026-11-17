from contextlib import contextmanager

from sqlalchemy import URL, create_engine, delete, event, select, text
from sqlalchemy.orm import sessionmaker

from app.core.environments import (
    DB_CONNECT_TIMEOUT_S,
    DB_ENGINE,
    DB_HOST,
    DB_NAME,
    DB_PASS,
    DB_POOL_TIMEOUT_S,
    DB_PORT,
    DB_READ_TIMEOUT_S,
    DB_STATEMENT_TIMEOUT_S,
    DB_USER,
    DB_WRITE_TIMEOUT_S,
)
from app.exceptions.AppHttpException import AppHttpException
from app.utils import dict_utils


def _get_engine_kwargs(engine_prefix: str) -> dict:
    """
    Retorna kwargs para create_engine según el motor de base de datos.

    Motores soportados:
      - mysql / mariadb  → charset utf8mb4, init_command (incluye time_zone UTC)
      - postgresql       → connect_timeout, timezone UTC
      - sqlite           → check_same_thread=False

    SQLite queda afuera del pin de zona a propósito: no tiene sesión con ``time_zone`` y su
    ``CURRENT_TIMESTAMP`` ya es UTC.
    """
    base = {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_recycle": 1800,   # recicla conexiones cada 30 min
        "pool_pre_ping": True,  # verifica la conexión antes de usarla (evita stale connections)
        # Con el pool agotado, el request falla a los N segundos en vez de esperar los 30 por
        # defecto de SQLAlchemy: una espera de 30 s ya es una página colgada para el operador.
        "pool_timeout": DB_POOL_TIMEOUT_S,
    }

    # La SESIÓN se fija en UTC en los dos motores, y no es preferencia de formato: en la
    # misma fila conviven timestamps de DOS relojes. ``applied_at`` de
    # ``database_migration_history`` lo escribe el proceso Python con
    # ``datetime.now(timezone.utc)``, mientras que ``created_at``/``updated_at`` del
    # ``TimestampMixin`` salen de ``func.now()``, o sea del reloj del MOTOR. Sin fijar la
    # zona, un servidor en horario local hace que las dos columnas de una misma fila difieran
    # por el offset **y nada lo declare**, que es la peor forma de un dato de auditoría.
    #
    # OJO AL DESPLEGAR: si este servidor NO venía en UTC, los ``created_at`` escritos ANTES
    # de este cambio están en su hora local y los de después en UTC. Hay una discontinuidad
    # en ese punto. En un contenedor con la zona por defecto (UTC) el cambio es un no-op.
    if engine_prefix in ("mysql", "mariadb"):
        base["connect_args"] = {
            "charset": "utf8mb4",
            "init_command": (
                "SET NAMES utf8mb4 COLLATE utf8mb4_general_ci, time_zone = '+00:00'"
            ),
            # Red de seguridad del CLIENTE, por encima del tope del motor (ver
            # ``_apply_server_statement_timeout``): si el socket no responde, pymysql corta y el
            # pool descarta la conexión. NO cancela la consulta en el servidor, por eso el límite
            # real es ``max_statement_time`` y este valor tiene que ser mayor.
            "connect_timeout": DB_CONNECT_TIMEOUT_S,
            "read_timeout": DB_READ_TIMEOUT_S,
            "write_timeout": DB_WRITE_TIMEOUT_S,
        }
    elif engine_prefix == "postgresql":
        options = "-c timezone=UTC"
        if DB_STATEMENT_TIMEOUT_S > 0:
            # El motor cancela la sentencia y libera sus locks; la conexión sigue usable.
            options += f" -c statement_timeout={DB_STATEMENT_TIMEOUT_S * 1000}"
        base["connect_args"] = {"connect_timeout": DB_CONNECT_TIMEOUT_S, "options": options}
    elif engine_prefix == "sqlite":
        base["connect_args"] = {"check_same_thread": False}

    return base


def _apply_server_statement_timeout(dbapi_connection, _connection_record) -> None:
    """
    Fija en la SESIÓN el tope de duración de sentencia que hace cumplir el propio motor.

    Va en un listener ``connect`` y no en ``init_command`` porque el mismo driver
    ``mysql+pymysql`` puede apuntar a MariaDB o a MySQL, y la variable cambia: en MariaDB es
    ``max_statement_time`` (segundos, cualquier sentencia) y en MySQL ``max_execution_time``
    (milisegundos, solo SELECT). Un ``SET`` de la variable equivocada fallaría en CADA conexión
    y tiraría el gateway entero. ``get_server_info()`` no hace una consulta: lo trae el handshake.

    No llega a Alembic: ``alembic/env.py`` arma su propio engine, así que una migración larga de
    la BD de metadatos no queda cortada por este tope.
    """
    if DB_STATEMENT_TIMEOUT_S <= 0:
        return
    server_version = dbapi_connection.get_server_info() or ""
    if "mariadb" in server_version.lower():
        statement = f"SET SESSION max_statement_time = {int(DB_STATEMENT_TIMEOUT_S)}"
    else:
        statement = f"SET SESSION max_execution_time = {int(DB_STATEMENT_TIMEOUT_S * 1000)}"
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(statement)
    finally:
        cursor.close()


#: Filas por lote de las purgas. Un ``DELETE`` sin lotes sobre una tabla grande puede pasar el
#: tope de sentencia: el motor lo deshace entero y la purga no avanza NUNCA, pasada tras pasada.
PURGE_BATCH_SIZE = 1000


def delete_in_batches(session_factory, key_column, *criteria) -> int:
    """
    Borra las filas que cumplen ``criteria`` en lotes de ``PURGE_BATCH_SIZE``, cada uno en su
    propia transacción. Devuelve el total borrado.

    Cada lote lee las claves, CIERRA esa transacción y recién después borra en otra, repitiendo
    ``criteria`` en el ``WHERE``. Leer y escribir en la misma transacción es el patrón que en
    MariaDB 11.8 dispara el error 1020 si alguien toca la fila en el medio. Repetir el criterio
    hace que una fila que cambió entre la lectura y el borrado (por ejemplo una sesión que se
    refrescó) se conserve.

    Sin ``ORDER BY`` a propósito: a una purga le da igual qué lote sale primero, y ordenar
    obligaría al motor a ordenar todo el conjunto que cumple el filtro.
    """
    model = key_column.class_
    total_deleted = 0
    while True:
        session = session_factory()
        try:
            keys = list(
                session.execute(select(key_column).where(*criteria).limit(PURGE_BATCH_SIZE))
                .scalars()
            )
            # Termina la transacción de lectura antes de escribir (ver el docstring).
            session.rollback()
            if not keys:
                return total_deleted
            deleted = session.execute(
                delete(model).where(key_column.in_(keys), *criteria),
                execution_options={"synchronize_session": False},
            ).rowcount
            session.commit()
        finally:
            session.close()
        total_deleted += deleted
        # Lote incompleto: no queda nada. Lote vacío pese a haber claves: alguien más las
        # borró o cambió; cortar evita un bucle si el borrado no avanza.
        if len(keys) < PURGE_BATCH_SIZE or deleted == 0:
            return total_deleted


class Database:
    """
    Singleton de conexión a base de datos.

    El engine y el pool de conexiones se crean UNA sola vez y se comparten
    entre todas las instancias durante el ciclo de vida de la aplicación.
    Esto evita la apertura de múltiples conexiones por request y el
    acumulamiento de conexiones en estado sleep en el servidor de base de datos.

    Uso:
        db = Database()   # siempre retorna la misma instancia
    """

    _instance: "Database | None" = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        db_name: str = DB_NAME,
        db_user: str = DB_USER,
        db_pass: str = DB_PASS,
        db_host: str = DB_HOST,
        db_port: int = DB_PORT,
        db_engine: str = DB_ENGINE,
    ):
        if self._initialized:
            return

        self.__db_name: str = db_name
        self.__db_user: str = db_user
        self.__db_pass: str = db_pass
        self.__db_host: str = db_host
        self.__db_port: str = db_port
        self.__db_engine: str = db_engine

        engine_prefix = db_engine.split("+")[0].lower()

        if engine_prefix == "sqlite":
            DB_URL = f"{db_engine}:///{db_name}"
        else:
            # URL.create() codifica usuario/contraseña correctamente (percent-encoding).
            # Una f-string plana rompe con contraseñas que contienen %, @, :, /, ? u otros
            # caracteres reservados de URL: create_engine() reparsea la cadena como URL y
            # los decodifica, corrompiendo la contraseña antes de llegar al driver.
            DB_URL = URL.create(
                drivername=db_engine,
                username=db_user,
                password=db_pass,
                host=db_host,
                port=db_port,
                database=db_name,
            )

        engine_kwargs = _get_engine_kwargs(engine_prefix)

        self.engine = create_engine(DB_URL, **engine_kwargs)
        if engine_prefix in ("mysql", "mariadb"):
            event.listen(self.engine, "connect", _apply_server_statement_timeout)
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self._initialized = True

    @contextmanager
    def get_session(self):
        session = self.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    def get_declarative_base_session(self):
        """
        Retorna sesión para uso con modelos ORM SQLAlchemy.

        Permite coexistencia de SQL directo (execute_query) y ORM.
        La sesión debe cerrarse manualmente después de su uso.

        Uso:
            from app.models import User
            session = db.get_declarative_base_session()
            try:
                user = session.query(User).filter(User.id == 1).first()
                session.commit()
            finally:
                session.close()

        Returns:
            Session: Sesión SQLAlchemy para operaciones ORM
        """
        return self.SessionLocal()

    def execute_query(
        self,
        query,
        params: dict = {},
        fetchone: bool | None = None,
        commit: bool | None = False,
    ):
        with self.get_session() as session:
            try:
                _query = text(query)
                result = session.execute(_query, params)
                session.commit()

                if fetchone:
                    row = result.fetchone()
                    return dict(row._mapping) if row else None
                elif fetchone is False:
                    return [dict(row._mapping) for row in result.fetchall()]

                if hasattr(result, "lastrowid") and result.lastrowid:
                    return result.lastrowid
                return result.rowcount

            except AppHttpException:
                raise
            except Exception as e:
                session.rollback()
                context = {
                    "error_type": type(e).__name__,
                    "query": query,
                    "params": dict_utils._sanitize_dict(params),
                }

                if hasattr(e, "orig"):
                    context["message"] = str(e.orig)
                if hasattr(e, "statement"):
                    context["sql"] = e.statement
                if hasattr(e, "params"):
                    context["params"] = dict_utils._sanitize_dict(e.params)

                raise AppHttpException(
                    message="Ocurrio un error inesperado en el servidor",
                    status_code=500,
                    context=context,
                )

    def call_procedure(self, procedure_name: str, params: list = []):
        try:
            with self.engine.begin() as conn:
                cursor = conn.connection.cursor()
                try:
                    cursor.callproc(procedure_name, params)
                    results = []

                    if cursor.description:
                        rows = cursor.fetchall()
                        columns = [desc[0] for desc in cursor.description]
                        results.append(
                            [dict(zip(columns, row, strict=False)) for row in rows]
                        )

                    while cursor.nextset():
                        if cursor.description:
                            rows = cursor.fetchall()
                            columns = [desc[0] for desc in cursor.description]
                            results.append(
                                [dict(zip(columns, row, strict=False)) for row in rows]
                            )

                    if not results:
                        return False
                    return results[0] if len(results) == 1 else results

                finally:
                    cursor.close()

        except AppHttpException:
            raise
        except Exception as e:
            context = {"error_type": type(e).__name__}

            if e.args:
                context["error_code"] = str(e.args[0])
            if len(e.args) > 1:
                context["message"] = str(e.args[1])

            if procedure_name:
                context["sp"] = procedure_name
            if params:
                context["params"] = dict_utils._sanitize_dict(params)

            if e.args and e.args[0] == 1644:
                raise AppHttpException(
                    f"Ocurrio un error inesperado en el servidor: {e.args[1]}",
                    status_code=500,
                    context=context,
                )

            raise AppHttpException(
                message="Ocurrio un error inesperado en el servidor",
                status_code=500,
                context=context,
            )

    def get_host(self):
        return self.__db_host

    def get_port(self):
        return self.__db_port

    def get_name(self):
        return self.__db_name

    def get_user(self):
        return self.__db_user
