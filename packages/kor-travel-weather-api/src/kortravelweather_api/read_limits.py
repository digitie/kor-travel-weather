"""공개 읽기의 요청 마감. 관리자/수집 engine 설정은 바꾸지 않는다.

문장마다 남은 마감을 ``SET LOCAL statement_timeout = '<ms>'``로 걸면 문장마다 왕복이 하나
더 들고, 값이 매번 다른 SQL 문자열이 psycopg의 prepare 후보 LRU를 밀어냈다(호출 검토 B9).
지금은 상수 SQL ``SELECT set_config(..., true)``에 값을 bind로 넘기고 transaction마다 한 번
건다. 이미 건 값이 이번 문장의 남은 마감과 :data:`_REARM_SLACK_SECONDS`(짧은 값은 그 값의
10%)보다 크게 어긋날 때만 다시 건다. 그래서 한 문장이 요청 마감을 넘겨 실행될 수 있는 시간도
그 여유 이하다. 같은 문장에서 ``lock_timeout``(:data:`PUBLIC_LOCK_TIMEOUT_MS`)도 건다. 야간
파티션 DDL 같은 lock 대기열 뒤에서 마감 15초를 다 쓰지 않고 55P03으로 끝나 503(재시도)이 된다.
"""

import re
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from threading import Event
from time import monotonic

from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

from kortravelweather.read_deadline import PUBLIC_READ_DEADLINE
from kortravelweather.read_performance import read_stage

_DEADLINE = PUBLIC_READ_DEADLINE
#: 문장 하나의 statement_timeout 상한(초). 남은 마감보다 짧을 때만 쓴다(API 시작 warmup 전용).
_STATEMENT_CAP: ContextVar[float | None] = ContextVar("weather_read_statement_cap", default=None)
#: 켜지면 다음 문장부터 보내지 않는다(API 시작 warmup 전용). asyncio 취소는 worker thread를
#: 멈추지 못하므로 thread 쪽에 취소를 전하는 신호다.
_CANCELLED: ContextVar[Event | None] = ContextVar("weather_read_cancelled", default=None)

#: 공개 읽기의 lock 대기 상한(ms). 남은 마감이 더 짧으면 그 값을 쓴다.
PUBLIC_LOCK_TIMEOUT_MS = 2000
#: 이미 건 statement_timeout을 그대로 쓰는 어긋남의 상한(초)과 비율.
_REARM_SLACK_SECONDS = 0.05
_REARM_SLACK_RATIO = 0.1
#: 이 transaction에 건 statement_timeout(초). DBAPI 연결 info에 두고 transaction이 시작될 때 지운다.
_ARMED = "weather_public_read_statement_timeout"
_ARM_SQL = (
    "SELECT set_config('statement_timeout', %s, true), set_config('lock_timeout', %s, true)"
)
#: 이 문장들 앞에서는 걸지 않는다. ``SET``은 lock을 기다리지 않고, ``SELECT set_config``는
#: snapshot을 잡는 query라 뒤이은 ``SET TRANSACTION ISOLATION LEVEL``을 실패시킨다.
#: ``ROLLBACK TO SAVEPOINT``·``RELEASE SAVEPOINT`` 앞에서 걸면 savepoint 안에서 건 값이 곧바로
#: 되돌려지고(기록과 실제 값이 어긋난다), 실패한 transaction에서는 그 SELECT가 25P02로 실패해
#: savepoint 복구가 막힌다.
_SKIP_ARM = re.compile(r"\s*(SET|ROLLBACK|RELEASE)\s", re.IGNORECASE)


class PublicReadTimeout(TimeoutError):
    pass


class PublicReadBusy(RuntimeError):
    """공개 읽기의 입장 대기가 상한을 넘었다. 503과 ``retry-after``로 다시 보내게 한다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@contextmanager
def public_read_deadline(seconds, *, statement_seconds=None, cancelled=None):
    """``seconds`` 뒤를 마감으로 둔다.

    ``statement_seconds``는 문장마다 따로 거는 상한, ``cancelled``는 켜지면 다음 문장을 막는
    ``threading.Event``다.
    """
    token = _DEADLINE.set(monotonic() + seconds)
    cap_token = _STATEMENT_CAP.set(statement_seconds)
    cancel_token = _CANCELLED.set(cancelled)
    try:
        yield
    finally:
        _CANCELLED.reset(cancel_token)
        _STATEMENT_CAP.reset(cap_token)
        _DEADLINE.reset(token)


def _needs_arm(armed: float | None, wanted: float) -> bool:
    if armed is None:
        return True
    return abs(armed - wanted) > min(_REARM_SLACK_SECONDS, armed * _REARM_SLACK_RATIO)


def install_public_read_limits(engine):
    if getattr(engine, "_weather_public_read_limits", False):
        return
    engine._weather_public_read_limits = True

    @event.listens_for(engine, "begin")
    def begin(connection):
        # set_config(..., true)의 수명은 transaction이다. 새 transaction에는 아직 상한이 없다.
        connection.info.pop(_ARMED, None)

    @event.listens_for(engine, "rollback_savepoint")
    def rollback_savepoint(connection, name, context):
        # savepoint 안에서 건 설정은 그 rollback으로 되돌아간다. 이 event는 ``ROLLBACK TO
        # SAVEPOINT`` 문장보다 먼저 오고 그 문장 앞에서는 걸지 않으므로(``_SKIP_ARM``) 기록을
        # 지우면 rollback 뒤 첫 문장에서 다시 건다.
        with suppress(SQLAlchemyError):
            connection.info.pop(_ARMED, None)

    @event.listens_for(engine, "before_cursor_execute")
    def before(connection, cursor, statement, parameters, context, executemany):
        deadline = _DEADLINE.get()
        if deadline is not None:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise PublicReadTimeout("공개 읽기 마감이 지났습니다.")
            cancelled = _CANCELLED.get()
            if cancelled is not None and cancelled.is_set():
                raise PublicReadTimeout("읽기가 취소되었습니다.")
            cap = _STATEMENT_CAP.get()
            if cap is not None:
                remaining = min(remaining, cap)
            # timeout 설정과 SQL 관측은 독립이다. service export는 자체 transaction 예산을 쓴다.
            if not _SKIP_ARM.match(statement) and _needs_arm(
                connection.info.get(_ARMED), remaining,
            ):
                timeout_ms = max(1, int(remaining * 1000))
                lock_ms = min(timeout_ms, PUBLIC_LOCK_TIMEOUT_MS)
                with cursor.connection.cursor() as settings_cursor:
                    settings_cursor.execute(_ARM_SQL, (f"{timeout_ms}ms", f"{lock_ms}ms"))
                connection.info[_ARMED] = timeout_ms / 1000
        context._weather_read_stage = read_stage("sql")
        context._weather_read_stage.__enter__()

    def finish(context):
        stage = getattr(context, "_weather_read_stage", None)
        if stage is not None:
            context._weather_read_stage = None
            stage.__exit__(None, None, None)

    @event.listens_for(engine, "after_cursor_execute")
    def after(connection, cursor, statement, parameters, context, executemany):
        finish(context)

    @event.listens_for(engine, "handle_error")
    def error(context):
        if context.execution_context is not None:
            finish(context.execution_context)
