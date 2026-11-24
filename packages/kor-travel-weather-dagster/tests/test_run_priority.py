"""공용 Dagster queue의 ``dagster/priority`` 공통 규칙(소유자 결정 2026-11-24).

정본: kor-travel-docker-manager ``docs/dagster-tenant-fairness.md``. 10 = 1시간 미만 주기
수집·상태 적재, 5 = 1시간 이상·하루 미만 주기 수집, 0 = 하루 이상 주기·정리·유지보수·
수동·관리자 일괄 실행.
Dagster 1.13.24 ``QueuedRunCoordinatorDaemon._priority_sort``는 run tag만 읽으므로(없으면 0) 값이
schedule·sensor·재실행이 만드는 run tag까지 닿는지도 본다.

수집 실패 감사(Map ``docs/reports/2026-11-23-collection-failure-audit.md`` §2.6 #9) 대응.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import pairwise
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import kortravelcommon.dagster as common_dagster
import pytest
from dagster import (
    DagsterEvent,
    DagsterEventType,
    DagsterInstance,
    DagsterRunStatus,
    build_schedule_context,
    build_sensor_context,
)
from dagster._core.events import JobFailureData, RunFailureReason
from dagster._utils.schedules import schedule_execution_time_iterator
from kortravelweather_dagster import alert_schedule, export_maintenance, export_prebuild
from kortravelweather_dagster import definitions as definitions
from kortravelweather_dagster.recovery import (
    INFRA_RETRY_ENABLED_ENV,
    LOCATION_NAME,
    NATIVE_ORIGIN_ENV,
    optional_recovery_sensors,
)

TAG = "dagster/priority"
SEOUL = ZoneInfo("Asia/Seoul")
#: 주기와 무관하게 0인 job(정리·유지보수). 주기만 보면 단기예보 정리(10분)는 10이 된다 —
#: 규칙은 "수집·상태 적재"만 올린다. 새 정리 job은 여기에도 넣어야 이 시험이 통과한다.
MAINTENANCE_JOBS = frozenset({
    "weather_retention_job",
    "weather_short_forecast_retention_job",
    "weather_export_maintenance_job",
    "weather_export_prebuild_job",
    "address_refresh_job",
})


def _jobs():
    repository = definitions.defs.get_repository_def()
    jobs = [job for job in repository.get_all_jobs() if not job.name.startswith("__ASSET_JOB")]
    assert len(jobs) >= 20
    return jobs


def _expected(period: timedelta | None, *, maintenance: bool) -> int:
    """규칙 그대로: 주기가 없으면(수동 전용) 0.

    1시간 미만 주기는 현황이면 10, 예보처럼 현황이 아니면 5다. weather의 1시간 미만 주기 수집은
    특보 발표 관측(현황) 하나뿐이라 주기만으로 판정한다 — 1시간 미만 예보 job이 생기면
    여기를 고친다.
    """
    if maintenance or period is None or period >= timedelta(days=1):
        return 0
    if period < timedelta(hours=1):
        return 10
    return 5


def _period(cron: str) -> timedelta:
    """3일 동안의 실제 Dagster 달력에서 가장 긴 tick 간격(= 수집이 보장하는 주기)."""
    start = datetime(2026, 11, 2, tzinfo=SEOUL)
    ticks = []
    for tick in schedule_execution_time_iterator(
        start.timestamp(), cron, execution_timezone="Asia/Seoul"
    ):
        if tick >= start + timedelta(days=3):
            break
        ticks.append(tick)
    # 하루에 한 번 이상 도는 cron만 다룬다. 더 긴 주기의 job이 생기면 이 창을 넓힌다(값은 0).
    assert len(ticks) >= 2, f"3일 창에 tick이 둘 미만인 cron: {cron}"
    return max(b - a for a, b in pairwise(ticks))


def _schedule_periods() -> dict[str, timedelta]:
    regional = {c.job_name: c.regular_cron_schedule for c in definitions.REGIONAL_COLLECTIONS}
    periods: dict[str, timedelta] = {}
    for schedule in definitions.defs.get_repository_def().schedule_defs:
        assert isinstance(schedule.cron_schedule, str), schedule.name
        # 지역 원천의 보충 tick(+1h·+3h)은 정규 tick이 실패했을 때만 run을 만든다.
        # 주기는 정규 cron이다.
        cron = regional.get(schedule.job_name, schedule.cron_schedule)
        assert schedule.job_name not in periods, f"job 하나에 schedule 둘: {schedule.job_name}"
        periods[schedule.job_name] = _period(cron)
    return periods


def test_every_defined_job_is_classified_and_nothing_stale_remains() -> None:
    names = {job.name for job in _jobs()}
    assert names == set(definitions.JOB_PRIORITIES)
    assert set(definitions.JOB_PRIORITIES.values()) <= {0, 5, 10}


def test_every_job_carries_its_priority_on_the_tags_every_launch_path_reads() -> None:
    # schedule·sensor daemon은 ``run_tags``, Launchpad·GraphQL launch·재실행(native retry 포함)은
    # job ``tags``, common infra retry는 ``job.tags``를 run tag의 바탕으로 쓴다. 둘 다 같아야 한다.
    for job in _jobs():
        expected = str(definitions.JOB_PRIORITIES[job.name])
        if job.name in definitions.COMMON_OWNED_JOBS:
            # common이 정의하는 job은 tag가 없고 생략은 Dagster에서 0이다.
            assert job.run_tags.get(TAG, "0") == expected == "0", job.name
            continue
        assert job.tags.get(TAG) == expected, job.name
        assert job.run_tags.get(TAG) == expected, job.name


def test_priority_follows_the_scheduled_period() -> None:
    periods = _schedule_periods()
    seen = {}
    for job in _jobs():
        period = periods.get(job.name)
        seen[job.name] = (period, definitions.JOB_PRIORITIES[job.name])
        assert definitions.JOB_PRIORITIES[job.name] == _expected(
            period, maintenance=job.name in MAINTENANCE_JOBS
        ), (job.name, period)
    # 규칙의 경계값을 실제 job으로 고정한다(1시간 정각은 5, 5분은 10, 하루는 0).
    assert seen["kma_ultra_short_forecast_job"] == (timedelta(hours=1), 5)
    assert seen["kma_regional_alert_events_job"] == (timedelta(minutes=5), 10)
    assert seen["weather_retention_job"] == (timedelta(days=1), 0)
    assert seen["weather_short_forecast_retention_job"] == (timedelta(minutes=10), 0)
    assert seen["weather_export_maintenance_job"] == (timedelta(hours=1), 0)
    assert seen["kma_mid_forecast_job"] == (timedelta(hours=12), 5)
    # schedule이 없는 외부 provider는 관리자 수동 실행 전용이다. 지금은 여섯이 있다 — 모두
    # slow 집합으로 옮기면 이 고정을 함께 지운다.
    unscheduled = {
        c.job_name
        for c in definitions.EXTERNAL_COLLECTIONS
        if c not in definitions.SCHEDULED_EXTERNAL_COLLECTIONS
    }
    assert unscheduled
    for name in unscheduled:
        assert seen[name] == (None, 0), name


def test_only_the_five_minute_collection_is_on_top_and_nothing_is_negative() -> None:
    values = definitions.JOB_PRIORITIES
    assert {name for name, value in values.items() if value == 10} == {
        "kma_regional_alert_events_job",
    }
    # 공용 queue에서 다른 테넌트(기본 0)보다 뒤로 미는 음수·10 초과는 두지 않는다. 10이 하나도
    # 없게 되는 변경(5분 job 제거)이면 위 집합과 함께 이 고정도 고친다.
    assert min(values.values()) == 0 and max(values.values()) == 10


def test_priority_does_not_disturb_the_other_run_tags() -> None:
    # 기존 run_group·max_runtime·project tag와 공존하고 그 값을 바꾸지 않는다.
    for job in _jobs():
        if job.name in definitions.COMMON_OWNED_JOBS:
            continue
        policy = definitions._job_policy(job.name).tags(project="weather", job_name=job.name)
        for key, value in policy.items():
            assert job.run_tags[key] == value, (job.name, key)
    for slug, job in definitions.external_weather_jobs.items():
        collection = next(c for c in definitions.EXTERNAL_COLLECTIONS if c.slug == slug)
        assert job.run_tags[definitions.EXTERNAL_RUN_GROUP_TAG] == collection.run_group
    for job in definitions.regional_weather_jobs.values():
        assert job.run_tags[definitions.EXTERNAL_RUN_GROUP_TAG] == definitions.REGIONAL_RUN_GROUP


def _day_ticks(cron: str) -> list[datetime]:
    start = datetime(2026, 11, 2, tzinfo=SEOUL)
    ticks = []
    for tick in schedule_execution_time_iterator(
        start.timestamp(), cron, execution_timezone="Asia/Seoul"
    ):
        if tick >= start + timedelta(days=1):
            return ticks
        ticks.append(tick)
    return ticks


def _open_breaker_history():
    # 같은 오류로 3회 연속 실패한 이력. 차단 중 probe 분기(간격이 지난 tick)도 평가된다.
    failed = SimpleNamespace(
        status="failed", error="ALERT_PAGE_DUPLICATE",
        started_at=datetime(2026, 11, 1, 23, 0, tzinfo=SEOUL),
    )
    return [failed, failed, failed]


@pytest.mark.parametrize("history", ["healthy", "breaker_open"])
def test_every_schedule_tick_leaves_the_job_priority_in_place(monkeypatch, history) -> None:
    # scheduler는 ``{**job.run_tags, **request.tags}``(dagster/_scheduler/scheduler.py
    # ``_create_scheduler_run``)로 run tag를 만든다. 요청이 priority를 덮거나 비우면 job 값이
    # run에 닿지 않는다. 하루치 모든 tick을 실제로 평가한다 — 지역 원천의 보충 tick(정규 run
    # 성공이 없어 실행), 특보 차단기의 probe 분기까지 들어간다.
    rows = _open_breaker_history() if history == "breaker_open" else []
    # export 정리 schedule은 publisher sensor가 RUNNING이고 export가 켜진 곳에서만 run을 만든다.
    monkeypatch.setattr(export_maintenance, "maintenance_enabled", lambda settings: True)
    monkeypatch.setattr(export_maintenance, "publisher_sensor_running", lambda instance: True)
    # export 사전 구성 schedule도 같은 조건(+ 사전 구성 설정)에서만 run을 만든다.
    monkeypatch.setattr(export_prebuild, "prebuild_enabled", lambda settings: True)
    monkeypatch.setattr(alert_schedule, "_read_history", lambda database_url: rows)
    # 차단기 gauge(프로세스 전역)를 열린 채 남기지 않는다.
    monkeypatch.setattr(alert_schedule, "set_alert_collection_breaker", lambda **kwargs: None)
    monkeypatch.setattr(
        alert_schedule, "kst_now", lambda: datetime(2026, 11, 2, 12, 0, tzinfo=SEOUL)
    )
    repository = definitions.defs.get_repository_def()
    requested: dict[str, int] = {}
    with DagsterInstance.local_temp() as instance:
        for schedule in repository.schedule_defs:
            job = repository.get_job(schedule.job_name)
            assert TAG not in (schedule.tags or {}), schedule.name
            # 5·10분 schedule은 앞 24 tick이면 분기가 같다(시험 시간 상한).
            for tick in _day_ticks(schedule.cron_schedule)[:24]:
                with build_schedule_context(
                    instance=instance,
                    scheduled_execution_time=tick,
                    resources={"weather_repository": SimpleNamespace(database_url="fixture-dsn")},
                ) as context:
                    result = schedule.evaluate_tick(context)
                for request in result.run_requests:
                    assert TAG not in request.tags, schedule.name
                    merged = {**job.run_tags, **request.tags}
                    assert merged[TAG] == str(definitions.JOB_PRIORITIES[job.name]), schedule.name
                    requested[schedule.name] = requested.get(schedule.name, 0) + 1
    # 모든 schedule이 적어도 한 번 run을 요청했다(평가가 조용히 전부 skip되면 시험이 빈다).
    assert set(requested) == {schedule.name for schedule in repository.schedule_defs}
    # 지역 원천은 정규 2회 + 보충 4회 모두 요청한다(성공 run이 없는 임시 instance).
    for collection in definitions.REGIONAL_COLLECTIONS:
        assert requested[collection.schedule_name] == 6, collection.schedule_name


def test_infrastructure_retry_child_gets_the_priority_from_the_job(monkeypatch) -> None:
    # 배포 전에 만들어진(priority 없는) run이 재시도돼도 child는 job 값을 받는다. common은
    # parent의 ``dagster/*`` tag를 버리고 ``job.tags``를 얹는다.
    monkeypatch.delenv(NATIVE_ORIGIN_ENV, raising=False)
    monkeypatch.setenv(INFRA_RETRY_ENABLED_ENV, "true")
    monkeypatch.setattr(common_dagster, "FAILURE_SETTLE_SECONDS", 0)
    mapping = {
        job.name: (job, definitions._job_policy(job.name)) for job in definitions._COLLECTION_JOBS
    }
    (retry,) = optional_recovery_sensors(mapping)
    job = definitions.kma_regional_alert_events_job
    legacy_tags = {key: value for key, value in job.tags.items() if key != TAG}
    overrides = {"run_retries": {"enabled": False, "max_retries": 0}}
    with DagsterInstance.local_temp(overrides=overrides) as instance:
        parent = instance.create_run_for_job(
            job,
            status=DagsterRunStatus.FAILURE,
            tags={**legacy_tags, "dagster/code_location": LOCATION_NAME},
        )
        instance.report_dagster_event(
            DagsterEvent(
                event_type_value=DagsterEventType.RUN_FAILURE.value,
                job_name=job.name,
                event_specific_data=JobFailureData(
                    None, failure_reason=RunFailureReason.RUN_WORKER_RESTART
                ),
            ),
            parent.run_id,
        )
        assert TAG not in instance.get_run_by_id(parent.run_id).tags
        (request,) = retry.evaluate_tick(build_sensor_context(instance=instance)).run_requests
    assert request.tags[TAG] == "10"


@pytest.mark.parametrize("name", sorted(definitions.COMMON_OWNED_JOBS))
def test_common_owned_jobs_stay_at_the_implicit_zero(name) -> None:
    # common이 나중에 priority 인자를 받으면 여기서 0을 명시로 바꾼다. 그 전까지 0이 아닌
    # 분류는 run에 닿을 길이 없으므로 표에서 막는다.
    assert definitions.JOB_PRIORITIES[name] == 0
    job = definitions.defs.get_repository_def().get_job(name)
    assert job.run_tags.get(TAG, "0") == "0"
