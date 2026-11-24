"""DB 예외의 일시 장애 분류. 공개 조회(app.py)와 service export가 같은 기준을 쓴다."""

from __future__ import annotations

from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError

#: 서버 종료·재시작·기동 중(admin_shutdown, crash_shutdown, cannot_connect_now).
#: 연결 오류 class 08(connection_exception 계열)도 같은 연결 단절로 본다.
CONNECTION_LOSS_SQLSTATES = frozenset({"57P01", "57P02", "57P03"})
#: 읽기 경합(statement·lock timeout, deadlock, 직렬화, 연결 수 초과).
#: 권한(28)·디스크(53100)·I/O(58)·결함(42 등)은 일시 장애가 아니다.
CONTENTION_SQLSTATES = frozenset({"57014", "55P03", "40P01", "40001", "53300"})


def sqlstate(exc: BaseException) -> str | None:
    """SQLAlchemy가 감싼 DBAPI 예외의 SQLSTATE. 없으면 None."""
    state = getattr(getattr(exc, "orig", None), "sqlstate", None)
    return state if isinstance(state, str) and state else None


def is_connection_loss(exc: BaseException) -> bool:
    """DB 연결이 끊겼거나 서버가 연결을 받을 수 없는 상태면 참이다.

    SQLSTATE가 있으면 class 08과 57P01~57P03만 본다. SQLSTATE가 없는 단절(server closed the
    connection unexpectedly, connection refused)은 SQLAlchemy가 단절로 판정한
    ``connection_invalidated`` 또는 ``OperationalError``로 판단한다.
    """
    if not isinstance(exc, SQLAlchemyError):
        return False
    state = sqlstate(exc)
    if state is not None:
        return state.startswith("08") or state in CONNECTION_LOSS_SQLSTATES
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    return isinstance(exc, OperationalError)


def is_transient_db_error(exc: BaseException) -> bool:
    """연결 단절 또는 읽기 경합이면 참이다. 같은 요청을 다시 보내면 성공할 수 있다."""
    if is_connection_loss(exc):
        return True
    return isinstance(exc, SQLAlchemyError) and sqlstate(exc) in CONTENTION_SQLSTATES
