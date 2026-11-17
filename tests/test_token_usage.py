"""
``record_last_used`` es de mejor esfuerzo: un error operativo de la BD (un 1020 de MariaDB, un
timeout) se registra y se descarta. Perder un refresco de ``last_used_at`` es mucho menos grave
que rechazar a un agente legítimo con un 500.
"""

from datetime import datetime, timedelta

from sqlalchemy.exc import OperationalError

from app.core.token_usage import record_last_used
from app.models.api_token import ApiToken


class _FailingSession:
    def __init__(self) -> None:
        self.rolled_back = False
        self.closed = False

    def execute(self, *_args, **_kwargs):
        raise OperationalError("UPDATE api_tokens", {}, Exception("1020 Record has changed"))

    def commit(self) -> None:
        raise AssertionError("no debería commitear tras un error")

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_operational_error_is_swallowed_and_the_session_is_released():
    session = _FailingSession()
    record_last_used(lambda: session, ApiToken, 1, datetime(2026, 1, 1), timedelta(seconds=60))
    assert session.rolled_back
    assert session.closed
