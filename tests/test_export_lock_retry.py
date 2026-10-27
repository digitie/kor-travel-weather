"""같은 run의 원본 ingest가 run 행을 잡고 있어도 export 준비가 버려지지 않는다.

운영 실측(2026-10-26, kma_short_forecast run): export의 ``SELECT weather_sync_runs ...
FOR UPDATE``가 같은 run의 ingest transaction(INSERT weather_values) 뒤에서 2.7~2.8초
기다리다 3초 lock_timeout으로 취소됐고 attempt는 사유 없이 PREPARE_FAILED가 됐다.

export 단계는 원본 ingest 재시도 정책(20회 × 3초 대기 + 3초 sleep, 단계당 약 2분)을 쓰지
않는다. 짧게 다시 시도하되 단계당·run당 쓸 시간을 묶고, 다 쓰면 ``:lock_timeout``으로
거절한다.
"""

import logging
import os
import threading
import time
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from kortravelweather import export_collector
from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_collector import KmaNowcastExportCollector, failure_reason
from kortravelweather.models import WeatherLocation
from kortravelweather.repository import WeatherRepository


@pytest.fixture(scope="module")
def repository():
    result = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    result.create_schema()
    yield result
    result.engine.dispose()


@pytest.fixture
def run_and_location(repository):
    key = "lock-" + uuid.uuid4().hex
    repository.create_location(
        WeatherLocation(location_id=key, name="잠금", latitude=37.5, longitude=127, nx=61, ny=128)
    )
    run = repository.start_sync_run(
        provider="python-kma-api", dataset_key="kma_ultra_short_nowcast"
    )
    yield run, key
    repository.finish_sync_run(run.run_id, status="failed")


def _hold_run_row(repository, run_id, seconds, locked):
    with repository.engine.connect() as connection, connection.begin():
        connection.execute(
            text("SELECT run_id FROM weather_sync_runs WHERE run_id = :run_id FOR UPDATE"),
            {"run_id": run_id},
        )
        locked.set()
        time.sleep(seconds)


def _watch_export_log(monkeypatch, caplog):
    # Alembic의 fileConfig(앞선 migration 시험)가 이미 있는 logger를 끈다.
    # 이 시험들은 우리 호출이 남기는 문장만 본다(tests/test_kma_missing_values.py와 같은 방식).
    monkeypatch.setattr(export_collector.logger, "disabled", False)
    caplog.set_level(logging.WARNING, logger=export_collector.logger.name)


def test_prepare_outlasts_the_same_runs_ingest_lock(
    repository, tmp_path, run_and_location, caplog, monkeypatch
):
    run, key = run_and_location
    collector = KmaNowcastExportCollector(repository, run_id=run.run_id, artifact_root=tmp_path)
    _watch_export_log(monkeypatch, caplog)
    locked = threading.Event()
    # lock_timeout(3초)보다 오래 잡아 첫 시도는 반드시 취소되게 한다.
    holder = threading.Thread(target=_hold_run_row, args=(repository, run.run_id, 4.5, locked))
    holder.start()
    assert locked.wait(10)
    try:
        prepared = collector.prepare(
            entity_id="grid:61:128", nx=61, ny=128, location_ids=[key],
        )
    finally:
        holder.join()
    assert prepared is not None
    attempts = collector.heads.lifecycle.attempts
    with repository.engine.connect() as connection:
        status = connection.scalar(
            select(attempts.c.status).where(attempts.c.attempt_id == prepared.identity.attempt_id)
        )
    assert status == "preparing"
    # 실제 PostgreSQL의 잠금 취소(55P03)를 경합으로 보고 다시 시도했다.
    assert "weather_export_lock_retry" in caplog.text and "sqlstate=55P03" in caplog.text


def test_rejection_records_only_a_sanitized_reason(
    repository, tmp_path, run_and_location, caplog, monkeypatch
):
    run, _ = run_and_location
    collector = KmaNowcastExportCollector(repository, run_id=run.run_id, artifact_root=tmp_path)
    identity = repository.begin_export_attempt(
        run_id=run.run_id, scope_key="lock-test", planned_sources=["a" * 64],
        execution_mode="native",
    )

    class LockNotAvailable(Exception):
        sqlstate = "55P03"

    error = OperationalError(
        "SELECT secret_table FOR UPDATE", {"password": "secret-value"}, LockNotAvailable()
    )
    _watch_export_log(monkeypatch, caplog)
    collector._reject(identity, "PREPARE_FAILED", error)
    attempts = collector.heads.lifecycle.attempts
    with repository.engine.connect() as connection:
        row = connection.execute(
            select(attempts.c.status, attempts.c.error_code).where(
                attempts.c.attempt_id == identity.attempt_id
            )
        ).one()
    assert tuple(row) == ("failed", "PREPARE_FAILED:lock_timeout")
    assert "lock_timeout" in caplog.text and "secret" not in caplog.text


@pytest.mark.parametrize(
    "error,reason",
    [
        (ExportArtifactError("증명 실패"), "proof"),
        (ValueError("raw payload"), "ValueError"),
    ],
)
def test_failure_reason_is_a_closed_classification(error, reason):
    assert failure_reason(error) == reason


class _Clock:
    """export 모듈의 시계·sleep 대역. 쉬는 시간을 실제로 기다리지 않고 잰다."""

    def __init__(self):
        self.now, self.pauses = 0.0, []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.pauses.append(seconds)
        self.now += seconds


def _db_error(sqlstate):
    driver_error = type("DriverError", (Exception,), {"sqlstate": sqlstate})()
    return OperationalError("SELECT run_id FROM weather_sync_runs FOR UPDATE", {}, driver_error)


@pytest.fixture
def clocked(repository, tmp_path, run_and_location, monkeypatch, caplog):
    """매 시도가 lock_timeout(3초)까지 기다리다 55P03으로 취소되는 export 단계."""
    run, _ = run_and_location
    collector = KmaNowcastExportCollector(repository, run_id=run.run_id, artifact_root=tmp_path)
    clock = _Clock()
    monkeypatch.setattr(export_collector, "time", clock)
    _watch_export_log(monkeypatch, caplog)
    attempts = []

    def contended():
        attempts.append(clock.now)
        clock.now += 3.0
        raise _db_error("55P03")

    return collector, clock, attempts, contended


def test_a_contended_export_step_gives_up_within_its_own_short_bound(clocked, caplog):
    collector, clock, attempts, contended = clocked
    with pytest.raises(OperationalError):
        collector._retry_lock(contended)
    # 원본 ingest 정책(20회, 약 2분)이 아니라 단계 상한(15초) 안에서 몇 번만 다시 시도한다.
    assert 2 <= len(attempts) <= 5
    assert clock.now <= export_collector.EXPORT_LOCK_STEP_SECONDS + 3.0
    low, high = export_collector.EXPORT_LOCK_PAUSE_SECONDS
    assert all(low <= pause <= high for pause in clock.pauses)
    retries = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("weather_export_lock_retry ")
    ]
    assert len(retries) == len(attempts) - 1
    assert all("sqlstate=55P03" in message for message in retries)
    assert "weather_export_lock_exhausted" in caplog.text and "limit=step_cap" in caplog.text


def _exhausted(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("weather_export_lock_exhausted ")
    ]


def test_contended_steps_stop_retrying_once_the_run_budget_is_spent(clocked, caplog):
    collector, clock, attempts, contended = clocked
    per_step = []
    for _ in range(60):
        before = len(attempts)
        with pytest.raises(OperationalError):
            collector._retry_lock(contended)
        per_step.append(len(attempts) - before)
        assert per_step[-1] <= 5
        if per_step[-1] == 1:
            break
    # run당 예산을 다 쓴 뒤의 경합 단계는 다시 시도하지 않고 곧바로 거절된다.
    assert per_step[0] > 1 and per_step[-1] == 1
    assert clock.now <= export_collector.EXPORT_LOCK_RUN_SECONDS + 2 * 3.0
    # 포기 로그는 어느 한도였는지와 run이 쓴 시간을 남긴다.
    exhausted = _exhausted(caplog)
    assert len(exhausted) == len(per_step)
    assert "limit=step_cap" in exhausted[0]
    assert "limit=run_budget" in exhausted[-1] and "run_budget_s=120" in exhausted[-1]
    assert "run_spent_s=" in exhausted[-1]
    # 예산을 다 써도 경합하지 않는 단계는 그대로 진행한다.
    assert collector._retry_lock(lambda: "sealed") == "sealed"


@pytest.mark.parametrize("error", [_db_error("57014"), ValueError("not a lock race")])
def test_other_failures_are_not_retried(clocked, error):
    collector, clock, _, _ = clocked
    calls = []

    def failing():
        calls.append(clock.now)
        raise error

    with pytest.raises(type(error)):
        collector._retry_lock(failing)
    assert len(calls) == 1 and not clock.pauses
