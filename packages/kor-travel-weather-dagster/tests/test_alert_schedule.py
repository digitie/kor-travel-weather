"""특보 발표 관측 schedule의 coalescing과 연속 실패 차단기."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from dagster import DagsterInstance, DagsterRunStatus, build_schedule_context, job, op
from kortravelweather_dagster import alert_schedule as module

from kortravelweather.alert_events import ALERT_DATASET, ALERT_PROVIDER
from kortravelweather.metrics import (
    ALERT_COLLECTION_BREAKER_OPEN,
    ALERT_COLLECTION_FAILURE_STREAK,
)
from kortravelweather.models import SyncRun, kst_now

NOW = kst_now()


def run(minutes_ago: float, status: str = "failed", error: str | None = "ALERT_PAGE_DUPLICATE"):
    return SyncRun(
        run_id=f"run_{minutes_ago}_{status}",
        provider=ALERT_PROVIDER,
        dataset_key=ALERT_DATASET,
        status=status,
        started_at=NOW - timedelta(minutes=minutes_ago),
        error=error if status == "failed" else None,
    )


def failures(count: int, *, newest: float = 1, every: float = 5):
    return [run(newest + index * every) for index in range(count)]


@pytest.mark.parametrize(
    ("streak", "gap"),
    [(0, 5), (2, 5), (3, 10), (4, 20), (5, 40), (6, 60), (40, 60)],
)
def test_gap_doubles_from_the_threshold_and_is_capped(streak, gap):
    assert module.required_gap(streak) == timedelta(minutes=gap)


def test_below_threshold_runs_on_every_tick():
    decision = module.decide(failures(2), now=NOW)
    assert decision.allow and not decision.open and decision.streak == 2


def test_three_identical_failures_open_the_breaker_until_the_longer_gap():
    runs = failures(3, newest=5)
    blocked = module.decide(runs, now=NOW)
    assert not blocked.allow and blocked.open and blocked.streak == 3
    assert blocked.error == "ALERT_PAGE_DUPLICATE"
    # 마지막 실패 시작 5분 뒤의 tick은 건너뛰고, 10분(여유 2분) 뒤 tick에서 한 번 시도한다.
    assert blocked.retry_after == NOW - timedelta(minutes=5) + timedelta(minutes=8)
    probe = module.decide(runs, now=NOW + timedelta(minutes=5))
    assert probe.allow and probe.open


def test_long_streak_waits_an_hour_between_probes():
    runs = failures(10, newest=30, every=60)
    assert not module.decide(runs, now=NOW).allow
    assert module.decide(runs, now=NOW + timedelta(minutes=30)).allow


@pytest.mark.parametrize(
    "runs",
    [
        # 다른 오류 코드가 섞이면 같은 지문의 연속이 아니다.
        [run(1), run(6), run(11, error="ALERT_TIMEOUT"), run(16)],
        # 성공이 끼면 연속이 끊긴다.
        [run(1), run(6), run(11, status="success"), run(16), run(21)],
        # 가장 최근이 성공이면 차단하지 않는다.
        [run(1, status="success"), *failures(5, newest=6)],
        # 오류 코드가 없는 실패는 지문이 없다.
        [run(1, error=None), run(6, error=None), run(11, error=None)],
    ],
)
def test_breaker_needs_the_same_fingerprint_consecutively(runs):
    decision = module.decide(runs, now=NOW)
    assert decision.allow and not decision.open


def test_a_running_row_does_not_reset_the_streak():
    runs = [run(0.5, status="running"), *failures(3, newest=3)]
    decision = module.decide(runs, now=NOW)
    assert decision.open and decision.streak == 3 and not decision.allow


@op
def alert_op():
    pass


@job
def alert_job():
    alert_op()


class Repository:
    def __init__(self, runs=None, error=None):
        self.runs = runs or []
        self.error = error
        self.calls = []
        self.engine = SimpleNamespace(dispose=self._dispose)
        self.disposed = 0

    def _dispose(self):
        self.disposed += 1

    def list_sync_runs(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.runs


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    # NOW는 수집 시점에 정해진다. 전체 suite에서는 평가까지 수십 분이 지나므로 고정한다.
    monkeypatch.setattr(module, "kst_now", lambda: NOW)


@pytest.fixture(autouse=True)
def isolated_repository_cache():
    """프로세스 캐시를 시험마다 비운다. 실제 engine은 연결을 닫는다."""
    module._REPOSITORIES.clear()
    yield
    for repository in module._REPOSITORIES.values():
        repository.engine.dispose()
    module._REPOSITORIES.clear()


def evaluate(repository, *, active=False):
    # 캐시에 가짜 repository를 넣어 평가가 그것을 읽게 한다(DSN 키는 resource 값).
    module._REPOSITORIES["fixture-dsn"] = repository
    return evaluate_with(SimpleNamespace(database_url="fixture-dsn"), active=active)


def evaluate_with(resource, *, active=False):
    schedule = module.build_regional_alert_events_schedule(alert_job)
    with DagsterInstance.local_temp() as instance:
        if active:
            instance.create_run_for_job(
                alert_job, status=DagsterRunStatus.STARTED,
                tags={"kortravelcommon/project": "weather"},
            )
        with build_schedule_context(
            instance=instance, scheduled_execution_time=NOW,
            resources={"weather_repository": resource},
        ) as context:
            return schedule.evaluate_tick(context)


def test_schedule_definition_keeps_name_cadence_and_default_state():
    from dagster import DefaultScheduleStatus

    schedule = module.build_regional_alert_events_schedule(alert_job)
    assert schedule.name == "regional_alert_events_every_five_minutes"
    assert schedule.cron_schedule == "*/5 * * * *"
    assert schedule.execution_timezone == "Asia/Seoul"
    assert schedule.default_status == DefaultScheduleStatus.RUNNING
    assert schedule.required_resource_keys == {"weather_repository"}


def test_open_breaker_skips_the_tick_and_raises_the_gauge():
    repository = Repository(failures(4, newest=2))
    result = evaluate(repository)
    assert result.run_requests == [] and result.skip_message
    assert repository.calls == [{
        "limit": module.HISTORY_LIMIT, "provider": ALERT_PROVIDER, "dataset_key": ALERT_DATASET,
        "timeout_ms": module.HISTORY_TIMEOUT_MS,
    }]
    # engine은 tick 사이에 재사용한다(평가마다 만들고 버리지 않는다).
    assert repository.disposed == 0
    assert ALERT_COLLECTION_BREAKER_OPEN._value.get() == 1
    assert ALERT_COLLECTION_FAILURE_STREAK._value.get() == 4


def test_recovered_history_requests_a_run_and_clears_the_gauge():
    repository = Repository([run(2, status="success"), *failures(6, newest=7)])
    result = evaluate(repository)
    assert len(result.run_requests) == 1
    assert ALERT_COLLECTION_BREAKER_OPEN._value.get() == 0
    assert ALERT_COLLECTION_FAILURE_STREAK._value.get() == 0


def test_unreadable_history_does_not_stop_collection():
    repository = Repository(error=RuntimeError("postgresql://secret@db/weather down"))
    result = evaluate(repository)
    assert len(result.run_requests) == 1


class StalledRepository(Repository):
    """연결 뒤 네트워크가 멈춘 것처럼 조회가 돌아오지 않는다."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def list_sync_runs(self, **kwargs):
        self.calls.append(kwargs)
        self.release.wait(30)
        return []


def test_stalled_history_read_is_bounded_and_does_not_pile_up_threads(monkeypatch):
    monkeypatch.setattr(module, "HISTORY_BUDGET_SECONDS", 0.2)
    repository = StalledRepository()
    try:
        started = time.monotonic()
        first = evaluate(repository)
        # 멈춘 조회가 끝나지 않아도 예산 안에 실행을 요청한다(fail-open).
        assert len(first.run_requests) == 1
        second = evaluate(repository)
        assert len(second.run_requests) == 1
        assert time.monotonic() - started < 10
        # 앞 조회 thread가 slot을 쥐고 있으므로 다음 평가는 조회를 새로 띄우지 않는다.
        assert len(repository.calls) == 1
    finally:
        repository.release.set()
        assert module._HISTORY_SLOTS.acquire(timeout=5)
        module._HISTORY_SLOTS.release()


@pytest.fixture
def real_resource(weather_test_database_url, monkeypatch):
    from kortravelweather_dagster.resources import WeatherRepositoryResource

    from kortravelweather.repository import WeatherRepository

    setup = WeatherRepository(weather_test_database_url)
    try:
        setup.create_schema()
    finally:
        setup.engine.dispose()
    # 비운영 환경의 resource는 create_repository에서 create_schema까지 한다.
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_ENV", "development")
    schema_calls = []
    monkeypatch.setattr(
        WeatherRepository, "create_schema", lambda self: schema_calls.append(self),
    )
    return WeatherRepositoryResource(database_url=weather_test_database_url), schema_calls


def _record_failures(database_url, count, error="ALERT_PAGE_DUPLICATE"):
    from kortravelweather.repository import WeatherRepository

    repository = WeatherRepository(database_url)
    try:
        for _ in range(count):
            started = repository.start_sync_run(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)
            repository.finish_sync_run(started.run_id, status="failed", error=error)
    finally:
        repository.engine.dispose()


def test_real_resource_reads_history_without_schema_ddl_and_reuses_the_engine(real_resource):
    resource, schema_calls = real_resource
    _record_failures(resource.database_url, 3)
    first = evaluate_with(resource)
    assert first.run_requests == [] and first.skip_message
    (cached,) = module._REPOSITORIES.values()
    second = evaluate_with(resource)
    assert second.run_requests == [] and second.skip_message
    # tick마다 engine을 만들지 않고, 비운영 환경에서도 trigger DROP/CREATE를 하지 않는다.
    assert list(module._REPOSITORIES.values()) == [cached]
    assert schema_calls == []
    assert ALERT_COLLECTION_FAILURE_STREAK._value.get() == 3


def test_history_lock_wait_becomes_fail_open_within_budget(real_resource, monkeypatch):
    from sqlalchemy import create_engine, text

    resource, _ = real_resource
    _record_failures(resource.database_url, 3)
    monkeypatch.setattr(module, "HISTORY_TIMEOUT_MS", 200)
    blocker = create_engine(resource.database_url)
    try:
        with blocker.connect() as connection:
            transaction = connection.begin()
            # migration이 표를 잡은 상황: 이력 SELECT는 잠금을 기다린다.
            connection.execute(text("LOCK TABLE weather_sync_runs IN ACCESS EXCLUSIVE MODE"))
            started = time.monotonic()
            result = evaluate_with(resource)
            elapsed = time.monotonic() - started
            transaction.rollback()
    finally:
        blocker.dispose()
    # 차단기가 열려 있을 이력(3회 연속 실패)이지만 읽지 못했으므로 실행을 막지 않는다.
    assert len(result.run_requests) == 1
    assert elapsed < module.HISTORY_BUDGET_SECONDS


def test_queued_or_running_predecessor_coalesces_the_tick_before_reading_history():
    repository = Repository(failures(1))
    result = evaluate(repository, active=True)
    assert result.run_requests == [] and result.skip_message
    assert repository.calls == []


def test_definitions_use_the_breaker_schedule():
    from kortravelweather_dagster import definitions

    schedule = next(
        value for value in definitions.defs.schedules
        if value.name == "regional_alert_events_every_five_minutes"
    )
    assert schedule.job_name == "kma_regional_alert_events_job"
    assert schedule.required_resource_keys == {"weather_repository"}
