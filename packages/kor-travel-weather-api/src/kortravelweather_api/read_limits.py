"""공개 읽기의 요청 마감. 관리자/수집 engine 설정은 바꾸지 않는다."""

from contextlib import contextmanager
from time import monotonic

from sqlalchemy import event

from kortravelweather.read_deadline import PUBLIC_READ_DEADLINE
from kortravelweather.read_performance import read_stage

_DEADLINE = PUBLIC_READ_DEADLINE


class PublicReadTimeout(TimeoutError):
    pass


@contextmanager
def public_read_deadline(seconds):
    token = _DEADLINE.set(monotonic() + seconds)
    try:
        yield
    finally:
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
