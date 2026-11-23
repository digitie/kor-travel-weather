"""지역별 특보 발표 관측 schedule: coalescing과 연속 실패 차단기.

5분 schedule은 이전 실행이 아직 대기·진행 중이면 tick을 합친다(common
``coalescing_schedule``). 그 위에 같은 오류 코드로 ``BREAKER_THRESHOLD``회 이상 연속 실패하면
다음 실행까지의 간격을 10·20·40분, 최대 60분으로 늘린다. 2026-11-18..24 운영에서 원천 응답
하나의 결정적 오류(ALERT_PAGE_DUPLICATE)가 하루 288 run을 모두 실패시켰다.

- 지문은 ``weather_sync_runs.error``(고정 안전 코드)다. 실행 시작 전에 죽은 run은 행이 없으므로
  세지 않는다(launch 장애는 Dagster plane 쪽 경보 대상이다).
- 차단 중에도 늘어난 간격마다 한 번은 실행한다(probe). 원천이 회복되면 그 실행이 성공하고
  연속 실패가 끊겨 바로 5분 주기로 돌아온다.
- 실패 상태 확인(DB 조회)이 안 되면 실행을 막지 않는다. 차단기는 낭비를 줄이는 장치이며
  수집을 멈추는 근거가 아니다.
- 상태는 gauge ``ktw_alert_collection_failure_streak``·``ktw_alert_collection_breaker_open``와
  tick 로그 ``ALERT_BREAKER_OPEN``으로 남는다. 경보 규칙은 deploy/prometheus/alerts.yml.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from dagster import DefaultScheduleStatus, ScheduleDefinition
from kortravelcommon.dagster import coalescing_schedule

from kortravelweather.alert_events import ALERT_DATASET, ALERT_PROVIDER, ALERT_SAFE_ERROR_CODES
from kortravelweather.metrics import set_alert_collection_breaker
from kortravelweather.models import SyncRun, kst_now

SCHEDULE_NAME = "regional_alert_events_every_five_minutes"
CRON = "*/5 * * * *"
BREAKER_THRESHOLD = 3
BASE_INTERVAL = timedelta(minutes=5)
MAX_INTERVAL = timedelta(minutes=60)
#: tick은 정각 5분 단위이고 실행 시작(``started_at``)은 그보다 수십 초 늦다. 늘린 간격이
#: tick 하나만큼 더 밀리지 않도록 이만큼 일찍 허용한다.
START_SLACK = timedelta(minutes=2)
HISTORY_LIMIT = 30


@dataclass(frozen=True)
class BreakerDecision:
    allow: bool
    open: bool
    streak: int
    error: str | None
    retry_after: datetime | None


def required_gap(streak: int) -> timedelta:
    """연속 실패 수에 따른 다음 실행까지의 최소 간격."""
    if streak < BREAKER_THRESHOLD:
        return BASE_INTERVAL
    return min(BASE_INTERVAL * 2 ** (streak - BREAKER_THRESHOLD + 1), MAX_INTERVAL)


def failure_streak(runs: Sequence[SyncRun]) -> tuple[int, str | None, datetime | None]:
    """최신순 실행 기록에서 같은 오류 코드의 연속 실패 수·코드·마지막 실패 시작 시각."""
    finished = [run for run in runs if run.status != "running"]
    if not finished or finished[0].status != "failed" or not finished[0].error:
        return 0, None, None
    error = finished[0].error
    streak = 0
    for run in finished:
        if run.status != "failed" or run.error != error:
            break
        streak += 1
    return streak, error, finished[0].started_at


def decide(runs: Sequence[SyncRun], *, now: datetime) -> BreakerDecision:
    streak, error, last_started = failure_streak(runs)
    if streak < BREAKER_THRESHOLD or last_started is None:
        return BreakerDecision(True, False, streak, error, None)
    retry_after = last_started + required_gap(streak) - START_SLACK
    return BreakerDecision(now >= retry_after, True, streak, error, retry_after)


def _safe_code(error: str | None) -> str:
    return error if error in ALERT_SAFE_ERROR_CODES else "ALERT_COLLECTION_FAILED"


def breaker_allows(context: Any) -> bool:
    """schedule ``should_execute``. 동일 job 실행 중 여부는 coalescing이 먼저 본다."""
    try:
        repository = context.resources.weather_repository.create_repository()
        try:
            runs = repository.list_sync_runs(
                limit=HISTORY_LIMIT, provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET,
            )
        finally:
            repository.engine.dispose()
    except Exception:
        # DSN·SQL 원문은 남기지 않는다. 확인할 수 없으면 평소처럼 실행한다.
        context.log.warning("ALERT_BREAKER_STATE_UNAVAILABLE")
        return True
    decision = decide(runs, now=kst_now())
    set_alert_collection_breaker(streak=decision.streak, open_=decision.open)
    if decision.open:
        retry_after = decision.retry_after.isoformat() if decision.retry_after else "-"
        message = (
            f"error={_safe_code(decision.error)} streak={decision.streak} "
            f"retry_after={retry_after}"
        )
        if decision.allow:
            context.log.info(f"ALERT_BREAKER_PROBE {message}")
        else:
            context.log.warning(f"ALERT_BREAKER_OPEN {message}")
    return decision.allow


def build_regional_alert_events_schedule(job: Any) -> ScheduleDefinition:
    return coalescing_schedule(
        project="weather",
        location_name="kortravelweather_dagster.definitions",
        job=job,
        should_execute=breaker_allows,
        name=SCHEDULE_NAME,
        cron_schedule=CRON,
        execution_timezone="Asia/Seoul",
        default_status=DefaultScheduleStatus.RUNNING,
        required_resource_keys={"weather_repository"},
    )
