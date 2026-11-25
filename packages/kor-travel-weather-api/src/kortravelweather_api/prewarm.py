"""API 시작 직후 한 번 도는 읽기 전용 warmup과 ``/ready`` 상태.

배포 직후 관리 지도의 첫 마커 묶음(60곳)은 이어진 묶음보다 약 10초 느렸다(26초 대 15초).
빈 DB pool·지연 import·catalog/plan cache·PG 버퍼가 첫 요청에 몰린 탓이다
(``docs/reports/2026-11-24-map-call-performance-review.md`` §4, 설계는
``docs/weather-api-prewarm.md``). 포트 열기를 막지 않는 task 하나가 관리 지도가 처음 밟는
읽기 경로를 사용자 요청과 같은 인자로 한 번 밟는다.

안전 장치:

- 읽기 전용. 쓰기 함수(``refresh_region_locations`` 등)는 부르지 않는다. 세션 폐기 확인은
  존재할 수 없는 무작위 값으로 해 lazy 삭제 분기에 들어가지 않는다.
- 동시성 1. 단계를 차례로 한 thread에서 실행하고 마커는 사용자와 같은 ``marker_limiter``,
  세션 확인은 같은 ``auth_limiter``로 들어간다. 마커 자리 대기도 마커 몫의 시간 안에서 한다.
- 문장당 5초·전체 30초. 공개 읽기 마감 장치(:mod:`.read_limits`)로 문장마다
  ``statement_timeout``을 걸고 pool 대기도 남은 시간으로 줄인다.
- 마커는 15곳씩 나눠 읽는다. 사용자 묶음(60곳)을 한 번에 읽으면 배포 직후 한 묶음이 27초를
  넘겨 남은 예산을 다 쓰고 57014로 끝나 아무것도 데우지 못했다(odroid 2026-11-25). 작은 묶음은
  문장마다 5초 상한 안에 들고, 한 묶음이 실패해도 다음 묶음으로 간다. 마커 몫은 전체 예산에서
  상세 단계 몫(문장 상한 하나)을 뺀 시각까지다.
- 사용자 요청(``/v1/*``)이 2건 이상 처리 중이면 남은 단계를 생략한다.
- 종료 시 취소는 worker thread에도 전해 다음 문장을 보내지 않는다.
- 예외는 로그·지표로만 남기고 앱 기동·요청 처리에 영향을 주지 않는다. 지도 엔드포인트에는
  결과 캐시가 없으므로 warmup이 오래된 데이터를 남기지 않는다.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from datetime import time as day_time
from threading import Event
from time import monotonic
from typing import Any, Literal, TypeVar, get_args
from uuid import uuid4

from anyio import CapacityLimiter, move_on_after, to_thread
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from kortravelweather.metrics import observe_api_prewarm_step
from kortravelweather.models import WeatherKind, WeatherLocation, kst_now
from kortravelweather.repository import WeatherRepository

from .db_errors import driver_error_name, sqlstate
from .read_limits import PublicReadTimeout, public_read_deadline

logger = logging.getLogger(__name__)

#: 전체 예산(초). 넘으면 남은 단계를 생략하고 ``/ready``는 200이 된다.
PREWARM_BUDGET_SECONDS = 30.0
#: 문장 하나의 ``statement_timeout`` 상한(초).
PREWARM_STATEMENT_SECONDS = 5.0
#: pool에 미리 열어 두는 연결 수(pool_size 10 안).
PREWARM_CONNECTIONS = 3
#: 관리 지도 ``MARKER_BATCH_SIZE``(weather-marker-queue.ts)와 같다. 앞쪽 2묶음(120곳)을 데운다.
MARKER_BATCH_SIZE = 60
MARKER_BATCHES = 2
#: warmup이 한 번에 읽는 마커 위치 수. 60곳 묶음의 배포 직후 문장 최댓값(사실 수화 12.4초,
#: 특보 후보 9.7초, odroid ``pg_stat_statements``)을 4로 나누면 문장 상한 5초 안에 든다.
MARKER_CHUNK_SIZE = 15
#: ``/v1/weather/markers``가 ``marker_values_many``에 넘기는 위치당 행 수와 같다.
MARKER_ROWS_PER_LOCATION = 80
#: 관리 지도 위치 카탈로그 한 페이지(``getAllPublicLocations``)와 같다.
LOCATION_PAGE_SIZE = 1000
#: 관리 지도 상세의 최신값(``getLatest(…, 200)``)·예보(``FORECAST_DETAIL_LIMIT``) 상한과 같다.
DETAIL_LATEST_LIMIT = 200
DETAIL_FORECAST_LIMIT = 5000
#: 처리 중인 사용자 요청이 이 수 이상이면 남은 단계를 생략한다.
BUSY_REQUESTS = 2
#: 종료 시 취소한 task를 기다리는 상한(초).
SHUTDOWN_GRACE_SECONDS = 6.0
#: 취소된 단계가 worker thread를 기다리는 상한(초). 실행 중이던 문장(5초 상한)이 끝날 시간이고
#: ``SHUTDOWN_GRACE_SECONDS``보다 짧아 task가 종료 대기 안에 끝난다.
THREAD_JOIN_SECONDS = 5.5
#: 공개 위치·대표 위치 읽기가 함수 안에서 처음 import하는 모듈.
LAZY_MODULES = ("kortravelcommon.address_providers", "kortravelcommon.reverse_address")

WarmStatus = Literal["disabled", "running", "done", "skipped"]
#: budget: 전체 예산 초과, busy: 사용자 요청 처리 중, error: 첫 연결 실패, shutdown: 종료.
SkipReason = Literal["budget", "busy", "error", "shutdown"]
StepName = Literal[
    "connections", "session_check", "regions", "locations", "markers", "location_detail",
]
StepOutcome = Literal["ok", "failed", "skipped"]
STEP_NAMES: tuple[StepName, ...] = get_args(StepName)

_T = TypeVar("_T")


class RequestActivity:
    """처리 중인 사용자 요청(``/v1/*``) 수. event loop에서만 바뀌므로 잠금이 없다."""

    def __init__(self) -> None:
        self.active = 0


@dataclass(frozen=True)
class StepItems:
    """여러 묶음으로 나눠 읽는 단계(마커)의 진행. 위치 수다."""

    warmed: int
    planned: int


#: 단계 함수의 반환값. 묶음으로 나눠 읽는 단계는 진행(:class:`StepItems`)을 같이 돌려준다.
StepReport = StepOutcome | tuple[StepOutcome, StepItems]


@dataclass
class StepResult:
    name: StepName
    outcome: StepOutcome
    elapsed_ms: int
    items: StepItems | None = None


@dataclass
class PrewarmState:
    """프로세스 하나의 warmup 상태. ``/ready``가 읽는다."""

    status: WarmStatus = "disabled"
    reason: SkipReason | None = None
    started_at: float | None = None
    finished_at: float | None = None
    steps: list[StepResult] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """warmup이 끝났거나(완료·생략) 꺼져 있으면 참이다."""
        return self.status != "running"

    def elapsed_ms(self, now: float | None = None) -> int | None:
        if self.started_at is None:
            return None
        end = self.finished_at
        if end is None:
            end = monotonic() if now is None else now
        return max(0, int((end - self.started_at) * 1000))


class ReadinessStepOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StepName
    outcome: StepOutcome
    elapsed_ms: int = Field(ge=0)
    warmed: int | None = Field(
        ge=0,
        description="markers: 읽기를 마친 위치 수(성공한 묶음의 위치 합). 다른 단계·생략은 null",
    )
    planned: int | None = Field(
        ge=0,
        description="markers: 데우려던 위치 수(카탈로그 앞쪽 최대 120곳). 다른 단계·생략은 null",
    )


class ReadinessOut(BaseModel):
    """``GET /ready`` 본문. 시작 warmup 상태이며 DB 건강 검사가 아니다."""

    model_config = ConfigDict(extra="forbid")

    ready: bool = Field(description="warmup이 끝났거나(done·skipped) 꺼져 있으면(disabled) true")
    warm: WarmStatus
    reason: SkipReason | None = Field(description="warm=skipped일 때 남은 단계를 생략한 이유")
    elapsed_ms: int | None = Field(
        ge=0, description="warmup 시작부터 끝(진행 중이면 지금)까지. 꺼져 있으면 null",
    )
    steps: list[ReadinessStepOut] = Field(description="끝난 단계(생략 포함)의 결과")

    @classmethod
    def from_state(cls, state: PrewarmState) -> ReadinessOut:
        return cls(
            ready=state.ready,
            warm=state.status,
            reason=state.reason,
            elapsed_ms=state.elapsed_ms(),
            steps=[
                ReadinessStepOut(
                    name=step.name, outcome=step.outcome, elapsed_ms=step.elapsed_ms,
                    warmed=None if step.items is None else step.items.warmed,
                    planned=None if step.items is None else step.items.planned,
                )
                for step in state.steps
            ],
        )


def _forecast_detail_window(now: datetime) -> tuple[datetime, datetime]:
    """관리 지도 상세 예보 구간(``forecastWindowStart``·``forecastWindowEnd``)과 같다."""
    start = now.replace(minute=0, second=0, microsecond=0)
    end = datetime.combine(now.date() + timedelta(days=2), day_time(0), tzinfo=now.tzinfo)
    return start, end


class ApiPrewarm:
    """``create_app``의 lifespan이 시작하는 1회 warmup."""

    def __init__(
        self,
        repository: WeatherRepository,
        *,
        state: PrewarmState,
        activity: RequestActivity,
        marker_limiter: CapacityLimiter,
        auth_limiter: CapacityLimiter,
        budget_seconds: float | None = None,
        statement_seconds: float | None = None,
    ) -> None:
        self.repository = repository
        self.state = state
        self.activity = activity
        self.marker_limiter = marker_limiter
        self.auth_limiter = auth_limiter
        self.budget_seconds = PREWARM_BUDGET_SECONDS if budget_seconds is None else budget_seconds
        self.statement_seconds = (
            PREWARM_STATEMENT_SECONDS if statement_seconds is None else statement_seconds
        )
        self._deadline = 0.0
        self._locations: list[WeatherLocation] = []
        # 종료 취소를 worker thread에 전한다. 켜지면 다음 문장을 보내지 않는다.
        self._cancelled = Event()

    def start(self) -> asyncio.Task[None]:
        """상태를 running으로 두고 warmup task를 만든다. 포트 열기를 기다리게 하지 않는다."""
        self.state.status = "running"
        self.state.reason = None
        self.state.started_at = monotonic()
        self.state.finished_at = None
        self.state.steps = []
        self._deadline = self.state.started_at + self.budget_seconds
        return asyncio.create_task(self.run(), name="weather-api-prewarm")

    async def run(self) -> None:
        steps: tuple[tuple[StepName, Callable[[], Awaitable[StepReport]]], ...] = (
            ("connections", self._connections),
            ("session_check", self._session_check),
            ("regions", self._regions),
            ("locations", self._catalog),
            ("markers", self._markers),
            ("location_detail", self._location_detail),
        )
        reason: SkipReason | None = None
        try:
            for index, (name, step) in enumerate(steps):
                reason = self._stop_reason()
                if reason is not None:
                    self._skip(steps[index:])
                    break
                outcome = await self._run_step(name, step)
                if outcome == "failed" and name == "connections":
                    # 연결도 못 여는 DB에 나머지 단계를 보내 봐야 예산만 쓴다.
                    reason = "error"
                    self._skip(steps[index + 1:])
                    break
        except asyncio.CancelledError:
            reason = "shutdown"
            raise
        finally:
            self._finish(reason)

    def _skip(self, steps: tuple[tuple[StepName, object], ...]) -> None:
        for name, _ in steps:
            self._record(name, "skipped", 0.0)

    def _finish(self, reason: SkipReason | None) -> None:
        state = self.state
        state.finished_at = monotonic()
        state.reason = reason
        state.status = "done" if reason is None else "skipped"
        counts = {outcome: 0 for outcome in get_args(StepOutcome)}
        for step in state.steps:
            counts[step.outcome] += 1
        log = logger.info if reason in (None, "busy", "shutdown") else logger.warning
        log(
            "weather_api_prewarm_finished warm=%s reason=%s elapsed_ms=%s ok=%d failed=%d "
            "skipped=%d",
            state.status, reason or "-", state.elapsed_ms(), counts["ok"], counts["failed"],
            counts["skipped"],
        )

    def _stop_reason(self) -> SkipReason | None:
        if monotonic() >= self._deadline:
            return "budget"
        if self.activity.active >= BUSY_REQUESTS:
            return "busy"
        return None

    def _record(
        self, name: StepName, outcome: StepOutcome, seconds: float,
        items: StepItems | None = None,
    ) -> None:
        elapsed_ms = max(0, int(seconds * 1000))
        self.state.steps.append(
            StepResult(name=name, outcome=outcome, elapsed_ms=elapsed_ms, items=items),
        )
        observe_api_prewarm_step(name, outcome, seconds)
        if items is None:
            logger.info(
                "weather_api_prewarm_step step=%s outcome=%s elapsed_ms=%d",
                name, outcome, elapsed_ms,
            )
        else:
            logger.info(
                "weather_api_prewarm_step step=%s outcome=%s elapsed_ms=%d warmed=%d planned=%d",
                name, outcome, elapsed_ms, items.warmed, items.planned,
            )

    @staticmethod
    def _log_failure(name: StepName, exc: Exception, *, chunk: str | None = None) -> None:
        # warmup 실패는 로그·지표로만 남긴다. 원문은 SQL·주소를 담을 수 있어 남기지 않고,
        # DB 밖 예외(코드 결함)만 traceback을 남긴다.
        logger.warning(
            "weather_api_prewarm_step_failed step=%s%s type=%s sqlstate=%s driver=%s",
            name, "" if chunk is None else f" chunk={chunk}", type(exc).__name__,
            sqlstate(exc) or "-", driver_error_name(exc),
            exc_info=not isinstance(exc, (SQLAlchemyError, PublicReadTimeout)),
        )

    async def _run_step(
        self, name: StepName, step: Callable[[], Awaitable[StepReport]],
    ) -> StepOutcome:
        started = monotonic()
        items: StepItems | None = None
        try:
            report = await step()
        except Exception as exc:
            outcome: StepOutcome = "failed"
            self._log_failure(name, exc)
        else:
            outcome, items = report if isinstance(report, tuple) else (report, None)
        self._record(name, outcome, monotonic() - started, items)
        return outcome

    async def _call(
        self,
        function: Callable[..., _T],
        *args: Any,
        limiter: CapacityLimiter | None = None,
        deadline: float | None = None,
        **kwargs: Any,
    ) -> _T:
        """남은 예산(``deadline``이 있으면 그 시각까지)을 마감으로, 5초를 문장 상한으로 걸고
        worker thread에서 실행한다."""
        started, finished = Event(), Event()
        until = self._deadline if deadline is None else min(deadline, self._deadline)

        def bounded() -> _T:
            # 취소 확인보다 먼저 켠다. 취소 쪽은 신호를 켠 뒤 이 값을 보므로, 이 thread가 신호를
            # 놓치고 문장을 보내면 취소 쪽은 반드시 이 thread를 기다린다.
            started.set()
            try:
                if self._cancelled.is_set():
                    raise PublicReadTimeout("warmup이 취소되었습니다.")
                remaining = until - monotonic()
                if remaining <= 0:
                    raise PublicReadTimeout("warmup 예산이 끝났습니다.")
                with public_read_deadline(
                    remaining, statement_seconds=self.statement_seconds, cancelled=self._cancelled,
                ):
                    return function(*args, **kwargs)
            finally:
                finished.set()

        try:
            return await to_thread.run_sync(bounded, limiter=limiter)
        except asyncio.CancelledError:
            # asyncio의 task 취소는 기다림만 끝내고 worker thread는 멈추지 못한다(anyio 4.16 실측).
            # thread에 신호를 보내 다음 문장을 막고, 실행 중이던 문장(5초 상한)이 끝나 thread가
            # 돌아오길 잠깐 기다린다. 그래야 thread를 남긴 채 종료가 끝나지 않고, 마커 자리도
            # thread가 돌아온 뒤에 돌려준다.
            self._cancelled.set()
            if started.is_set():
                await asyncio.to_thread(finished.wait, THREAD_JOIN_SECONDS)
            raise

    async def _connections(self) -> StepOutcome:
        await self._call(self._open_connections)
        return "ok"

    def _open_connections(self) -> None:
        for module in LAZY_MODULES:
            importlib.import_module(module)
        # 한 thread에서 차례로 열고 모두 쥔 채 확인해야 pool에 서로 다른 연결이 남는다.
        with ExitStack() as stack:
            for _ in range(PREWARM_CONNECTIONS):
                connection = stack.enter_context(self.repository.engine.connect())
                connection.execute(text("SELECT 1"))

    async def _session_check(self) -> StepOutcome:
        # BFF는 모든 관리 요청마다 이 확인을 5초 fail-closed로 부른다. 무작위 값은 어떤
        # 폐기 기록과도 맞지 않으므로 행을 읽기만 한다.
        await self._call(
            self.repository.is_admin_session_revoked,
            f"prewarm-{uuid4().hex}",
            limiter=self.auth_limiter,
        )
        return "ok"

    async def _regions(self) -> StepOutcome:
        await self._call(self.repository.region_locations)
        return "ok"

    async def _catalog(self) -> StepOutcome:
        self._locations = await self._call(self._read_catalog)
        return "ok"

    def _read_catalog(self) -> list[WeatherLocation]:
        # ``GET /v1/weather/locations?limit=1000&offset=0``과 같은 세 읽기.
        rows = self.repository.list_locations(
            enabled_only=True, search=None, limit=LOCATION_PAGE_SIZE, offset=0,
            include_metadata=False,
        )
        self.repository.count_locations(enabled_only=True, search=None)
        self.repository.public_location_regions(rows)
        return rows

    async def _markers(self) -> StepReport:
        """앞쪽 위치를 작은 묶음으로 나눠 사용자 마커 읽기를 한 번씩 밟는다.

        묶음마다 따로 자리·연결·transaction을 쓰고, 실패한 묶음은 기록만 하고 다음 묶음으로
        간다. 마커 몫(전체 마감에서 상세 단계 몫을 뺀 시각)이 문장 상한 하나보다 적게 남으면 새
        묶음을 시작하지 않는다. 시작해도 마감에 잘릴 가능성이 크고, 상세 단계를 굶기지 않기
        위해서다.
        """
        ids = [row.location_id for row in self._locations[: MARKER_BATCH_SIZE * MARKER_BATCHES]]
        chunks = [ids[start:start + MARKER_CHUNK_SIZE]
                  for start in range(0, len(ids), MARKER_CHUNK_SIZE)]
        deadline = self._marker_deadline()
        last_start = deadline - self.statement_seconds
        warmed = failed = attempted = 0
        for index, chunk in enumerate(chunks, start=1):
            if self._stop_reason() is not None or monotonic() >= last_start:
                break
            # 사용자 마커와 같은 limiter(2)를 나눠 쓴다. warmup은 한 자리를 한 묶음 동안만 쓴다.
            if not await self._acquire_marker_slot(last_start):
                break
            try:
                # 자리를 기다리는 사이 사용자 요청이 몰렸으면 묶음을 보내지 않고 자리를 돌려준다.
                if self._stop_reason() is not None:
                    break
                attempted += 1
                await self._call(self._marker_batch, chunk, deadline=deadline)
            except Exception as exc:
                failed += 1
                self._log_failure("markers", exc, chunk=f"{index}/{len(chunks)}")
            else:
                warmed += len(chunk)
            finally:
                self.marker_limiter.release()
        items = StepItems(warmed=warmed, planned=len(ids))
        if failed:
            return "failed", items
        return ("ok" if attempted else "skipped"), items

    def _marker_deadline(self) -> float:
        """마커 단계의 마감. 상세 단계가 문장 상한 하나만큼은 쓸 수 있게 남긴다."""
        return self._deadline - self.statement_seconds

    async def _acquire_marker_slot(self, until: float) -> bool:
        """마커 자리를 ``until``까지만 기다린다. 그때까지 못 얻으면 거짓이다."""
        with move_on_after(until - monotonic()):
            await self.marker_limiter.acquire()
            return True
        return False

    def _marker_batch(self, ids: list[str]) -> None:
        # ``GET /v1/weather/markers``가 관리 지도 기본 필터(종류 6개 전체)로 하는 읽기와 같다.
        # 위치 PK 조회는 자기 transaction, 값 읽기는 공유 읽기 묶음으로 라우터와 같게 나눈다.
        locations = self.repository.get_locations_by_ids(ids, enabled_only=True)
        by_id = {location.location_id: location for location in locations}
        with self.repository.read_scope():
            self.repository.marker_values_many(
                [location_id for location_id in ids if location_id in by_id],
                limit_per_location=MARKER_ROWS_PER_LOCATION,
                kinds=list(WeatherKind),
                locations=by_id,
            )

    async def _location_detail(self) -> StepOutcome:
        if not self._locations:
            return "skipped"
        await self._call(self._read_detail, self._locations[0].location_id)
        return "ok"

    def _read_detail(self, location_id: str) -> None:
        # 관리 지도 상세의 ``/latest?limit=200``과 ``/forecast?from&to&limit=5000``.
        self.repository.get_location(location_id)
        self.repository.latest_values(location_id, limit=DETAIL_LATEST_LIMIT)
        start, end = _forecast_detail_window(kst_now())
        self.repository.timeline(
            location_id, from_at=start, to_at=end, limit=DETAIL_FORECAST_LIMIT,
            include_revisions=False, exclude_alerts=True,
        )


async def stop_prewarm(task: asyncio.Task[None]) -> None:
    """종료 시 warmup task를 취소하고 최대 6초 기다린다.

    취소는 worker thread에도 전해져 다음 문장을 보내지 않고, task는 실행 중이던 문장(5초 상한)이
    끝나 thread가 돌아온 뒤 끝난다.
    """
    if task.done():
        return
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=SHUTDOWN_GRACE_SECONDS)
    if not done:
        logger.warning("weather_api_prewarm_shutdown_pending grace_seconds=%s",
                       SHUTDOWN_GRACE_SECONDS)


__all__ = [
    "STEP_NAMES",
    "ApiPrewarm",
    "PrewarmState",
    "ReadinessOut",
    "RequestActivity",
    "stop_prewarm",
]
