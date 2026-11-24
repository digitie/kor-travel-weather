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

퇴역과 GC는 서로의 결과에 기대지 않으므로 한 단계가 실패해도 다른 단계는 돈다(퇴역이 계속
문장 상한에 걸려도 artifact가 쌓이지 않는다). 둘 다 시도한 뒤 하나라도 실패했으면 run을
실패로 끝낸다(sensor의 skip 사유에 묻히지 않는다). 메시지에는 단계와 분류만 남긴다.

GC 삭제 batch는 배타 artifact guard를 잡는다. 참조 판정은 공유 guard에서 먼저 하므로 보호
행뿐인 batch는 배타 guard를 잡지 않는다. 배타 guard를 잡은 호출 뒤에는 그 호출 시간 이상
쉬어, 회수할 것이 많은 run에서도 배타 점유가 GC 시간의 절반을 넘지 않게 한다.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from dagster import (
    DefaultScheduleStatus,
    Failure,
    in_process_executor,
    job,
    op,
)
from kortravelcommon.dagster import RecoveryPolicy, coalescing_schedule

from kortravelweather.export_collector import (
    RETIRE_BATCH,
    failure_reason,
    retire_settled_attempts,
)
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
#: writer(5초)가 굶지 않도록 호출 한 번은 sensor 시절과 같은 1.5초만 지우고 쉰다. 쉬는 시간은
#: 최소 ``GC_YIELD_SECONDS``이고, 배타 guard를 잡은 호출 뒤에는 그 호출 시간 이상이다.
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


class ExportMaintenanceError(RuntimeError):
    """정리 단계가 하나 이상 실패했다. 메시지는 단계와 분류만 담고 ``report``는 진행이다."""

    def __init__(self, report: dict[str, Any]):
        super().__init__(f"export 정리 실패({report['failures']})")
        self.report = report


def _retire(repository, report: dict[str, Any], clock: Callable[[], float]) -> None:
    """퇴역을 예산 안에서 반복한다. 진행은 문장마다 ``report``에 남긴다(실패해도 남는다)."""
    retired: Counter[str] = Counter()
    report.update({"retired_superseded": 0, "retired_confirmed_terminal": 0, "retire_rounds": 0})
    deadline = clock() + RETIRE_BUDGET_SECONDS
    while clock() < deadline:
        counts = retire_settled_attempts(
            repository, statement_timeout_seconds=RETIRE_STATEMENT_SECONDS,
        )
        retired.update(counts)
        report.update({
            "retired_superseded": retired.get("SUPERSEDED", 0),
            "retired_confirmed_terminal": retired.get("CONFIRMED_TERMINAL", 0),
            "retire_rounds": report["retire_rounds"] + 1,
        })
        # 어느 문장도 상한까지 차지 않았으면 남은 퇴역 대상이 없다.
        if max(counts.values(), default=0) < RETIRE_BATCH:
            break


def _gc_pause(mode: str, result: dict[str, Any], elapsed: float) -> float:
    """다음 GC 호출 전 쉬는 시간. 배타 guard를 잡은 호출 뒤에는 그 호출 시간 이상 쉰다.

    delete 모드는 참조 판정 뒤 지울 대상이 있던 batch만 배타 guard를 잡는다. 호출 시간 전체를
    점유 상한으로 보고 같은 시간 이상 쉬면 배타 점유는 GC 시간의 절반 이하다.
    """
    if mode == "delete" and int(result.get("eligible", 0)) > 0:
        return max(GC_YIELD_SECONDS, elapsed)
    return GC_YIELD_SECONDS


def _collect_garbage(
    repository, settings: WeatherSettings, report: dict[str, Any],
    clock: Callable[[], float], sleep: Callable[[float], None],
) -> None:
    """GC를 끝까지(또는 예산까지) 돈다. 합계는 실패해도 ``report``와 지표에 남긴다."""
    mode = settings.service_export_artifact_gc
    report["gc_mode"] = mode
    if mode == "off":
        return
    gc: Counter[str] = Counter()
    calls, complete, pending = 0, False, 0
    scan = DatabaseArtifactGCScan(
        page_size=GC_SCAN_PAGE, statement_seconds=GC_SCAN_STATEMENT_SECONDS,
    )
    gc_deadline = clock() + GC_BUDGET_SECONDS
    try:
        while clock() < gc_deadline:
            started = clock()
            result = collect_export_artifacts(
                repository, settings.service_export_artifact_root, mode=mode,
                retention=timedelta(hours=settings.service_export_artifact_retention_hours),
                deadline=started + GC_SCAN_STATEMENT_SECONDS,
                delete_budget_seconds=GC_DELETE_SECONDS, scan=scan,
            )
            calls += 1
            gc.update({key: int(result.get(key, 0)) for key in _GC_SUM_KEYS})
            complete, pending = bool(result.get("complete")), int(result.get("pending", 0))
            if complete:
                break
            if not (result.get("visited") or result.get("live") or result.get("eligible")):
                # 목록도 판정도 진척이 없다(guard 경합 등). 다음 run에 다시 본다.
                break
            sleep(_gc_pause(mode, result, clock() - started))
    finally:
        scan.close()
        report.update({f"gc_{key}": gc.get(key, 0) for key in _GC_SUM_KEYS})
        report.update({"gc_calls": calls, "gc_complete": complete, "gc_pending": pending})
        if calls:
            # run 합계로 한 번만 기록한다. 호출마다 기록하면 reclaimable gauge에 마지막 호출분만
            # 남는다(그 호출이 본 일부 batch의 byte).
            observe_export_artifact_gc({key: gc.get(key, 0) for key in _GC_SUM_KEYS})


def run_export_maintenance(
    repository, settings: WeatherSettings, *, clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """퇴역과 GC를 서로 독립으로 둘 다 시도한다.

    한 단계가 실패해도 다른 단계는 돈다. 하나라도 실패했으면 둘 다 시도한 뒤
    ``ExportMaintenanceError``(분류만, 원 예외 연결 없음)를 올린다.
    """
    report: dict[str, Any] = {}
    # DB URL·SQL 파라미터가 담길 수 있는 원문 대신 분류(SQLSTATE·클래스 이름)만 남긴다.
    failures: list[str] = []
    try:
        _retire(repository, report, clock)
    except Exception as exc:
        failures.append(f"retire:{failure_reason(exc)}")
    try:
        _collect_garbage(repository, settings, report, clock, sleep)
    except Exception as exc:
        failures.append(f"gc:{failure_reason(exc)}")
    if failures:
        report["failures"] = ",".join(failures)
        raise ExportMaintenanceError(report)
    return report


def _log_report(context, report: dict[str, Any]) -> None:
    context.log.info(
        "weather_export_maintenance "
        + " ".join(f"{key}={value}" for key, value in sorted(report.items()))
    )


@op(required_resource_keys={"weather_repository"})
def weather_export_maintenance(context) -> dict[str, Any]:
    settings = WeatherSettings()
    if not maintenance_enabled(settings):
        context.log.info("export publisher가 꺼져 있어 정리할 것이 없습니다.")
        return {"skipped": True}
    repository = context.resources.weather_repository.create_repository()
    try:
        report = run_export_maintenance(repository, settings)
    except ExportMaintenanceError as exc:
        _log_report(context, exc.report)
        # 실패한 run에도 다른 단계의 진행(퇴역 수·GC 합계)을 metadata로 남긴다.
        raise Failure(description=str(exc), metadata=exc.report) from None
    finally:
        repository.engine.dispose()
    _log_report(context, report)
    context.add_output_metadata(report)
    return report


_POLICY = RecoveryPolicy(MAX_RUNTIME_SECONDS, idempotent=True, infrastructure_retries=1)


def maintenance_policy() -> RecoveryPolicy:
    return _POLICY


@job(
    name=JOB_NAME,
    executor_def=in_process_executor,
    # dagster/priority 0: 매시지만 export 정리다(공용 queue 규칙). 분류의 정본은 definitions의
    # ``JOB_PRIORITIES``이고 tests/test_run_priority.py가 이 값과 대조한다(순환 import라
    # 상수로 둔다).
    tags={**_POLICY.tags(project="weather", job_name=JOB_NAME), "dagster/priority": "0"},
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
