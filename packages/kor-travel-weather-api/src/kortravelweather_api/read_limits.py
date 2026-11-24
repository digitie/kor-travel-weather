"""공개 읽기의 요청 마감. 관리자/수집 engine 설정은 바꾸지 않는다."""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event
from time import monotonic

from sqlalchemy import event

from kortravelweather.read_deadline import PUBLIC_READ_DEADLINE
from kortravelweather.read_performance import read_stage

_DEADLINE = PUBLIC_READ_DEADLINE
#: 문장 하나의 statement_timeout 상한(초). 남은 마감보다 짧을 때만 쓴다(API 시작 warmup 전용).
_STATEMENT_CAP: ContextVar[float | None] = ContextVar("weather_read_statement_cap", default=None)
#: 켜지면 다음 문장부터 보내지 않는다(API 시작 warmup 전용). asyncio 취소는 worker thread를
#: 멈추지 못하므로 thread 쪽에 취소를 전하는 신호다.
_CANCELLED: ContextVar[Event | None] = ContextVar("weather_read_cancelled", default=None)


class PublicReadTimeout(TimeoutError):
    pass


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


def install_public_read_limits(engine):
    if getattr(engine, "_weather_public_read_limits", False):
        return
    engine._weather_public_read_limits = True

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
            timeout_ms = max(1, int(remaining * 1000))
            with cursor.connection.cursor() as settings_cursor:
                settings_cursor.execute(f"SET LOCAL statement_timeout = '{timeout_ms}ms'")
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
