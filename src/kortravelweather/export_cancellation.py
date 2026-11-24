"""연결이 끊긴 service export 요청을 worker thread에서 멈춘다(호출 검토 A6).

route의 감시 task가 요청자 연결 끊김을 보면 :class:`ExportCancellation`에 표시한다. worker
thread는 단계 경계(limiter 진입·freeze 전후·렌더 전·같은 키 대기)와 SQL 문장마다 이 표시를
확인해 :class:`ExportRequestCancelled`로 멈춘다. 끊긴 순간 실행 중이던 SQL은 psycopg
``cancel_safe()``로 서버에 취소를 요청한다. 멈춘 요청은 transaction을 rollback하고 export
슬롯(``service_export_limiter``)과 DB 연결을 바로 돌려준다.

페이지 캐시 리더(같은 키의 첫 요청)는 예외다. Map은 read timeout 뒤 같은 cursor로 다시
요청하므로 리더를 끊김 즉시 멈추면 재시도가 처음부터 다시 만들고, 페이지 생성 시간이 Map
timeout보다 길면 영영 끝나지 않는다. 그래서 리더는 끊긴 뒤 :data:`LEADER_GRACE_SECONDS` 동안,
그리고 같은 키 요청이 대기자이거나 export 슬롯을 기다리는 동안 계속해 완성 페이지를 캐시에
남긴다. 유예가 끝난 리더는 실행 중 SQL을 끊지 않고 다음 문장 전에 멈춘다(문장 상한 5초).
아직 일을 시작하지 않은 요청, 다른 리더를 기다리는 요청, 캐시하지 않는 작업은 끊김 즉시 멈춘다.

server-side cursor(``stream_results``)의 FETCH는 SQLAlchemy cursor hook을 거치지 않는다. 그래서
실행 중인 FETCH는 서버 취소 대상이 아니고(FETCH마다 문장 상한 5초), 끊김은 읽는 쪽 loop의 행마다
:func:`checkpoint`로 확인한다(``export_capacity``).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from typing import Any

from sqlalchemy import event

logger = logging.getLogger(__name__)

#: 페이지 캐시 리더가 연결이 끊긴 뒤에도 같은 키 재시도를 기다리며 계속하는 시간(초).
#: 연결을 끊는 것은 Map의 httpx read timeout이다. Map ``weather_state._weather_get``은 그 전송
#: 오류를 한 번만 재시도하며(``DEFAULT_UPSTREAM_ATTEMPTS=2``), 간격은
#: ``PROVIDER_BOUNDARY_BASE_DELAY_SECONDS=15``초다(jitter ±20%를 넣으면 최대 18초). 503 응답 뒤의
#: 5초 페이지 재시도는 완결된 응답이라 끊긴 리더를 만들지 않으므로 기준이 아니다. 18초에 새 연결·
#: 인증·route 진입 여유를 넉넉히 더했다. export 슬롯 대기는 이 유예가 아니라 같은 키 도착 등록
#: (``ExportPageCache.arriving``)이 맡는다. 재시도가 없으면 고아 리더는 이 시간 뒤 멈춘다.
LEADER_GRACE_SECONDS = 45.0
#: 취소 가능한 대기(같은 키 페이지·승인 선택)의 끊김 확인 간격(초).
WAIT_POLL_SECONDS = 0.25
#: 실행 중 SQL 취소 요청의 상한(초). 넘기면 그 문장은 문장 상한(5초)에 맡긴다.
SQL_CANCEL_TIMEOUT_SECONDS = 2.0

_CURRENT: ContextVar[ExportCancellation | None] = ContextVar(
    "weather_export_cancellation", default=None,
)


class ExportRequestCancelled(Exception):
    """요청자가 연결을 끊어 이 export 작업을 멈췄다. 경합 503이 아니라 499로 끝낸다.

    ``Exception``을 상속한다. ``BaseException``이면 SQLAlchemy가 연결을 무효화(폐기)한다.
    """

    def __init__(self, stage: str) -> None:
        super().__init__(f"요청자 연결이 끊겨 {stage} 단계에서 멈췄습니다.")
        self.stage = stage


class ExportCancellation:
    """한 export 요청의 끊김 표시. 감시 task(event loop)와 worker thread가 함께 쓴다."""

    def __init__(self, *, clock: Callable[[], float] = monotonic,
                 grace_seconds: float | None = None) -> None:
        self.clock = clock
        self.grace_seconds = LEADER_GRACE_SECONDS if grace_seconds is None else grace_seconds
        self._disconnected = threading.Event()
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._running: Any = None
        self._shield: Callable[[], bool] | None = None
        self.disconnected_at: float | None = None
        self.stopped_stage: str | None = None
        self.sql_cancel_requests = 0

    @property
    def disconnected(self) -> bool:
        return self._disconnected.is_set()

    def should_stop(self) -> bool:
        """지금 이 요청의 작업을 멈춰야 하면 참이다.

        리더 구간은 끊긴 뒤 유예가 지나고 같은 키 대기자도 없을 때만 멈춘다. 리더의 실행 중
        SQL은 서버 취소 대상이 아니며 다음 문장 전에 멈춘다(문장 상한 5초).
        """
        if self._cancelled.is_set():
            return True
        if not self._disconnected.is_set():
            return False
        shield = self._shield
        if shield is None:
            return True
        if self.clock() - (self.disconnected_at or 0.0) < self.grace_seconds:
            return False
        try:
            return not shield()
        except Exception:  # noqa: BLE001 - 대기자 판정 실패가 취소를 막지 않는다
            return True

    def check(self, stage: str) -> None:
        if self.should_stop():
            self._stop(stage)

    def _stop(self, stage: str) -> None:
        if self.stopped_stage is None:
            self.stopped_stage = stage
        raise ExportRequestCancelled(stage)

    # -- 감시 task(event loop) 쪽 ---------------------------------------------------------
    def mark_disconnected(self) -> None:
        if not self._disconnected.is_set():
            self.disconnected_at = self.clock()
            self._disconnected.set()

    def cancel(self) -> None:
        """이후 단계를 멈추고 실행 중인 SQL 취소를 서버에 요청한다. 리더 구간에는 부르지 않는다.

        취소 요청은 서버와 짧게 왕복하므로 event loop가 아니라 worker thread에서 부른다.
        잠금을 쥔 채 보내므로 그 SQL을 실행한 worker는 취소 요청이 끝난 뒤에야 연결을 돌려준다.
        """
        with self._lock:
            self._cancelled.set()
            running = self._running
            if running is None:
                return
            try:
                cancel_safe = getattr(running, "cancel_safe", None)
                if callable(cancel_safe):
                    cancel_safe(timeout=SQL_CANCEL_TIMEOUT_SECONDS)
                else:
                    running.cancel()
                self.sql_cancel_requests += 1
            except Exception as exc:  # noqa: BLE001 - 문장 상한(5초)이 남은 안전망이다
                logger.warning(
                    "weather_service_export_sql_cancel_failed reason=%s", type(exc).__name__,
                )

    # -- SQL hook(worker thread) 쪽 -------------------------------------------------------
    def statement_started(self, dbapi_connection: Any) -> None:
        with self._lock:
            if self.should_stop():
                self._stop("sql")
            self._running = dbapi_connection

    def statement_finished(self) -> None:
        with self._lock:
            self._running = None


def current_cancellation() -> ExportCancellation | None:
    return _CURRENT.get()


@contextmanager
def export_cancellation(cancellation: ExportCancellation) -> Iterator[ExportCancellation]:
    """worker thread 안에서 이 요청의 끊김 표시를 단계 확인과 SQL hook에 연결한다."""
    token = _CURRENT.set(cancellation)
    try:
        yield cancellation
    finally:
        _CURRENT.reset(token)


def checkpoint(stage: str) -> None:
    """단계 경계 확인. 감시 중인 요청이 아니면 아무것도 하지 않는다."""
    cancellation = _CURRENT.get()
    if cancellation is not None:
        cancellation.check(stage)


@contextmanager
def leading(has_waiters: Callable[[], bool]) -> Iterator[None]:
    """페이지 캐시 리더 구간. 끊겨도 유예 동안과 ``has_waiters``가 참인 동안은 계속한다."""
    cancellation = _CURRENT.get()
    if cancellation is None:
        yield
        return
    previous = cancellation._shield
    cancellation._shield = has_waiters
    try:
        yield
    finally:
        cancellation._shield = previous


def wait_event(waiting: threading.Event, timeout: float, *, stage: str) -> bool:
    """``Event.wait(timeout)``와 같되 감시 중인 요청이 끊기면 기다리지 않고 멈춘다."""
    cancellation = _CURRENT.get()
    if cancellation is None:
        return waiting.wait(max(0.0, timeout))
    deadline = monotonic() + max(0.0, timeout)
    while True:
        cancellation.check(stage)
        remaining = deadline - monotonic()
        if remaining <= 0:
            return waiting.is_set()
        if waiting.wait(min(WAIT_POLL_SECONDS, remaining)):
            return True


def install_export_cancellation(engine: Any) -> None:
    """감시 중인 export 요청의 SQL 문장마다 끊김을 확인하고 실행 중 문장을 취소 대상으로 둔다."""
    if getattr(engine, "_weather_export_cancellation", False):
        return
    engine._weather_export_cancellation = True

    @event.listens_for(engine, "before_cursor_execute")
    def before(connection, cursor, statement, parameters, context, executemany):
        cancellation = _CURRENT.get()
        if cancellation is not None:
            cancellation.statement_started(cursor.connection)

    @event.listens_for(engine, "after_cursor_execute")
    def after(connection, cursor, statement, parameters, context, executemany):
        cancellation = _CURRENT.get()
        if cancellation is not None:
            cancellation.statement_finished()

    @event.listens_for(engine, "handle_error")
    def error(context):
        cancellation = _CURRENT.get()
        if cancellation is None:
            return None
        cancellation.statement_finished()
        original = context.original_exception
        if isinstance(original, ExportRequestCancelled) or not cancellation.should_stop():
            return None
        # 취소한 SQL의 57014는 경합(503)이 아니다. 다른 handler는 그대로 돌고 이 예외로 끝난다.
        if cancellation.stopped_stage is None:
            cancellation.stopped_stage = "sql"
        return ExportRequestCancelled("sql")
