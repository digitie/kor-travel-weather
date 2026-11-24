"""DB 예외의 일시 장애 분류. 공개 조회(app.py)와 service export가 같은 기준을 쓴다."""

from __future__ import annotations

from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError

#: 서버 종료·재시작·기동 중(admin_shutdown, crash_shutdown, cannot_connect_now).
#: 연결 오류 class 08(connection_exception 계열)도 같은 연결 단절로 본다.
CONNECTION_LOSS_SQLSTATES = frozenset({"57P01", "57P02", "57P03"})
#: 읽기 경합(statement·lock timeout, deadlock, 직렬화, 연결 수 초과).
#: 권한(28)·디스크(53100)·I/O(58)·결함(42 등)은 일시 장애가 아니다.
CONTENTION_SQLSTATES = frozenset({"57014", "55P03", "40P01", "40001", "53300"})
#: 접속 단계에서 기다려도 같은 설정 오류의 서버·libpq 원문 조각(소문자).
#:
#: psycopg 3은 접속 실패(``connection failed: ...``)를 SQLSTATE 없이 올린다. 그래서 비밀번호
#: 오류(28P01)·접속 규칙 거부(28000)·없는 DB(3D000)도 연결 거부·기동 중과 똑같이 SQLSTATE가
#: 없고, 원문으로만 구분할 수 있다. 서버 ``lc_messages``가 영어가 아니면 맞지 않아 일시 장애로
#: 남는다(보수적으로 재시도 쪽).
PERMANENT_CONNECT_FAILURE_MARKERS = (
    "authentication failed",  # 28P01 password, 28000 ident·peer·cert
    "no pg_hba.conf entry",  # 28000
    "pg_hba.conf rejects",  # 28000
    "is not permitted to log in",  # 28000
    "does not exist",  # 3D000 database, 28000 role(trust 인증)
    "permission denied",  # 42501 CONNECT 권한
    "no password supplied",  # libpq: 비밀번호 미설정
)


def sqlstate(exc: BaseException) -> str | None:
    """SQLAlchemy가 감싼 DBAPI 예외의 SQLSTATE. 없으면 None."""
    state = getattr(getattr(exc, "orig", None), "sqlstate", None)
    return state if isinstance(state, str) and state else None


def driver_error_name(exc: BaseException) -> str:
    """로그용 DBAPI 예외 class 이름(예: ``ConnectionTimeout``). 원문·주소·계정은 담지 않는다."""
    orig = getattr(exc, "orig", None)
    return type(orig).__name__ if orig is not None else "-"


def is_permanent_connect_failure(exc: BaseException) -> bool:
    """SQLSTATE 없는 접속 실패가 인증·접속 규칙·없는 DB/role·CONNECT 권한 오류면 참이다."""
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False
    message = str(orig).lower()
    return any(marker in message for marker in PERMANENT_CONNECT_FAILURE_MARKERS)


def is_connection_loss(exc: BaseException) -> bool:
    """DB 연결이 끊겼거나 서버가 연결을 받을 수 없는 상태면 참이다.

    SQLSTATE가 있으면 class 08과 57P01~57P03만 본다. SQLSTATE가 없으면 SQLAlchemy가 단절로
    판정한 ``connection_invalidated``이거나 ``OperationalError``(server closed the connection
    unexpectedly, connection refused, connection timeout, 기동·종료 중 접속 거부)면 참이다.
    단, 접속 단계의 설정 오류(:data:`PERMANENT_CONNECT_FAILURE_MARKERS`)는 기다려도 같으므로
    빼서 처리되지 않은 오류(500, traceback)로 남긴다.
    """
    if not isinstance(exc, SQLAlchemyError):
        return False
    state = sqlstate(exc)
    if state is not None:
        return state.startswith("08") or state in CONNECTION_LOSS_SQLSTATES
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    return isinstance(exc, OperationalError) and not is_permanent_connect_failure(exc)


def is_transient_db_error(exc: BaseException) -> bool:
    """연결 단절 또는 읽기 경합이면 참이다. 같은 요청을 다시 보내면 성공할 수 있다."""
    if is_connection_loss(exc):
        return True
    return isinstance(exc, SQLAlchemyError) and sqlstate(exc) in CONTENTION_SQLSTATES
