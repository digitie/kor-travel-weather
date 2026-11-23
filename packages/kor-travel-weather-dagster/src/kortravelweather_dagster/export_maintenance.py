"""export publisher의 정리 일(일괄 퇴역·artifact GC)을 sensor tick 밖의 job으로 돈다.

publisher sensor는 승격(fresh·sweep)과 일별 집계만 한다. 정리는 승격의 최신성과
무관하고, sensor gRPC 기한(60초) 안에 넣으려고 잘게 쪼개면 끝나지 못한다.
2026-11-21..23 odroid sensor 48시간(2,845 tick) 실측:

- 퇴역(5분마다 한 번) 561회 중 303회(54%)가 5초 문장 상한에 걸렸다(평균 4.2초 소모).
- GC 571회 중 344회(60%)가 목록 문장 상한(1.5초)에 걸렸다. 20,000행 목록 한 번이
  6.7초(EXPLAIN ANALYZE)라 처음부터 들어갈 수 없었고, tick마다 새 repository를 만들어
  process 안의 목록 cursor가 매번 버려졌다. 성공한 GC도 한 번에 약 240개만 지웠다.
- GC 차례인 tick은 source 예산에서 GC 최악 시간(10초)을 미리 뺐다.

job은 한 시간에 한 번이다(하루 24 run — odroid의 하루 약 1,000 run 대비 2.4%).
퇴역은 sweep의 후보별 판정이 같은 기록으로도 하므로 늦어져도 승격을 막지 않는다.
실패는 run 실패로 드러난다(sensor의 skip 사유에 묻히지 않는다).
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from dagster import (
    DefaultScheduleStatus,
    in_process_executor,
    job,
    op,
)
from kortravelcommon.dagster import RecoveryPolicy, coalescing_schedule

from kortravelweather.export_collector import RETIRE_BATCH, retire_settled_attempts
from kortravelweather.export_database_gc import DatabaseArtifactGCScan
from kortravelweather.export_gc import collect_export_artifacts
from kortravelweather.metrics import observe_export_artifact_gc
from kortravelweather.settings import WeatherSettings

JOB_NAME = "weather_export_maintenance_job"
SCHEDULE_NAME = "hourly_weather_export_maintenance"
LOCATION_NAME = "kortravelweather_dagster.definitions"
#: :52 — 매시 KMA(:00·:05·:20)·AirKorea(:35)·외부 provider phase(:07·:17·:27·:37·:47·:57)
#: 어느 것과도 같은 분에 시작하지 않는다.
CRON_SCHEDULE = "52 * * * *"
#: 퇴역 예산과 문장 상한. 한 문장은 ``RETIRE_BATCH``행까지만 잠근다(SKIP LOCKED).
RETIRE_BUDGET_SECONDS = 60.0
RETIRE_STATEMENT_SECONDS = 30.0
#: GC 전체 예산, 목록 한 page의 행 수·문장 상한, 호출 한 번의 삭제 예산.
GC_BUDGET_SECONDS = 300.0
GC_SCAN_PAGE = 2_000
GC_SCAN_STATEMENT_SECONDS = 15.0
#: 삭제 batch는 배타 artifact guard를 잡는다. API reader(10ms polling, 최대 5초)와 capture
#: writer(5초)가 굶지 않도록 호출 한 번은 sensor 시절과 같은 1.5초만 지우고 잠깐 쉰다.
GC_DELETE_SECONDS = 1.5
GC_YIELD_SECONDS = 0.25
#: 퇴역 60초 + GC 300초 + 마지막 문장 대기에 여유를 둔 run 상한.
MAX_RUNTIME_SECONDS = 900

_GC_SUM_KEYS = ("visited", "scanned", "live", "eligible", "eligible_bytes",
                "deleted", "deleted_bytes", "errors")


def maintenance_enabled(settings: WeatherSettings) -> bool:
    """publisher와 같은 조건이다. export가 꺼졌거나 root가 없으면 정리할 것도 없다."""
    root = settings.service_export_artifact_root
    return bool(settings.service_exports_enabled and root is not None and root.is_absolute())


def run_export_maintenance(
    repository, settings: WeatherSettings, *, clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """퇴역을 예산 안에서 반복하고, GC를 끝까지(또는 예산까지) 돈다. 오류는 그대로 올린다."""
    retired: Counter[str] = Counter()
    rounds = 0
    deadline = clock() + RETIRE_BUDGET_SECONDS
    while clock() < deadline:
        counts = retire_settled_attempts(
            repository, statement_timeout_seconds=RETIRE_STATEMENT_SECONDS,
        )
        rounds += 1
        retired.update(counts)
        # 어느 문장도 상한까지 차지 않았으면 남은 퇴역 대상이 없다.
        if max(counts.values(), default=0) < RETIRE_BATCH:
            break
    report: dict[str, Any] = {
        "retired_superseded": retired.get("SUPERSEDED", 0),
        "retired_confirmed_terminal": retired.get("CONFIRMED_TERMINAL", 0),
        "retire_rounds": rounds,
    }
    mode = settings.service_export_artifact_gc
    report["gc_mode"] = mode
    if mode == "off":
        return report
    gc: Counter[str] = Counter()
    calls, complete, pending = 0, False, 0
    scan = DatabaseArtifactGCScan(
        page_size=GC_SCAN_PAGE, statement_seconds=GC_SCAN_STATEMENT_SECONDS,
    )
    gc_deadline = clock() + GC_BUDGET_SECONDS
    try:
        while clock() < gc_deadline:
            result = collect_export_artifacts(
                repository, settings.service_export_artifact_root, mode=mode,
                retention=timedelta(hours=settings.service_export_artifact_retention_hours),
                deadline=clock() + GC_SCAN_STATEMENT_SECONDS,
                delete_budget_seconds=GC_DELETE_SECONDS, scan=scan,
            )
            calls += 1
            observe_export_artifact_gc(result)
            gc.update({key: int(result.get(key, 0)) for key in _GC_SUM_KEYS})
            complete, pending = bool(result.get("complete")), int(result.get("pending", 0))
            if complete:
                break
            if not (result.get("visited") or result.get("live") or result.get("eligible")):
                # 목록도 판정도 진척이 없다(guard 경합 등). 다음 run에 다시 본다.
                break
            sleep(GC_YIELD_SECONDS)
    finally:
        scan.close()
    report.update({f"gc_{key}": gc.get(key, 0) for key in _GC_SUM_KEYS})
    report.update({"gc_calls": calls, "gc_complete": complete, "gc_pending": pending})
    return report


@op(required_resource_keys={"weather_repository"})
def weather_export_maintenance(context) -> dict[str, Any]:
    settings = WeatherSettings()
    if not maintenance_enabled(settings):
        context.log.info("export publisher가 꺼져 있어 정리할 것이 없습니다.")
        return {"skipped": True}
    repository = context.resources.weather_repository.create_repository()
    try:
        report = run_export_maintenance(repository, settings)
    finally:
        repository.engine.dispose()
    context.log.info(
        "weather_export_maintenance "
        + " ".join(f"{key}={value}" for key, value in sorted(report.items()))
    )
    context.add_output_metadata(report)
    return report


_POLICY = RecoveryPolicy(MAX_RUNTIME_SECONDS, idempotent=True, infrastructure_retries=1)


def maintenance_policy() -> RecoveryPolicy:
    return _POLICY


@job(
    name=JOB_NAME,
    executor_def=in_process_executor,
    tags=_POLICY.tags(project="weather", job_name=JOB_NAME),
    description="export 일괄 퇴역과 artifact GC를 publisher sensor 밖에서 매시 돈다.",
)
def weather_export_maintenance_job():
    weather_export_maintenance()


PUBLISHER_SENSOR_NAME = "weather_export_publisher"


def publisher_sensor_running(instance) -> bool:
    """이 location의 publisher sensor가 저장소에 RUNNING으로 켜져 있는가.

    publisher는 기본 STOPPED라 켠 적이 없으면 상태 행이 없다(= 꺼짐). freeze·운영 정지로
    publisher를 멈추면 정리도 함께 멈춘다 — 정리는 PG를 쓰는 writer다.
    """
    for state in instance.all_instigator_state():
        if (
            state.instigator_name == PUBLISHER_SENSOR_NAME
            and state.instigator_type.value == "SENSOR"
            and state.status.value == "RUNNING"
            and state.repository_selector.location_name == LOCATION_NAME
        ):
            return True
    return False


def _should_execute(context) -> bool:
    return maintenance_enabled(WeatherSettings()) and publisher_sensor_running(context.instance)


weather_export_maintenance_schedule = coalescing_schedule(
    project="weather",
    location_name=LOCATION_NAME,
    job=weather_export_maintenance_job,
    should_execute=_should_execute,
    name=SCHEDULE_NAME,
    cron_schedule=CRON_SCHEDULE,
    execution_timezone="Asia/Seoul",
    # 기본 RUNNING이지만 publisher sensor가 RUNNING이고 export가 켜진 곳에서만 run을 만든다
    # (``_should_execute``). 정리를 publisher 스위치 하나에 묶어 배포 직후 퇴역·GC 공백도,
    # publisher를 멈춘 동안의 별도 writer도 생기지 않는다.
    default_status=DefaultScheduleStatus.RUNNING,
)
