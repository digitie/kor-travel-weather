"""weather_sync_recovery sensor의 tick 예산·이어 가기·engine 재사용·정체 경보.

repository는 가짜로 두고 실제 common `reconciliation_sensor` 정의를 tick 단위로 평가한다.
실제 DB에서의 정체 시나리오는 `tests/test_sync_worker_recovery.py`가 맡는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import kortravelcommon.dagster as common_dagster
import pytest
from dagster import DagsterInstance, build_sensor_context
from kortravelweather_dagster import definitions, sync_recovery

from kortravelweather.metrics import SYNC_RECOVERY_STALLED
from kortravelweather.repository import InterruptedReconcileProgress, StuckSyncRun


class FakeRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.resume_after: tuple[datetime, str] | None = None
        self.stuck: list[StuckSyncRun] = []
        self.error: Exception | None = None

    def reconcile_stale_sync_runs(self) -> int:
        self.calls.append(("stale", None))
        if self.error is not None:
            raise self.error
        return 1

    def reconcile_interrupted_sync_runs_within(self, is_terminal, *, remaining, after):
        assert 0 < remaining() <= sync_recovery.BUDGET_SECONDS
        self.calls.append(("interrupted", after))
        return InterruptedReconcileProgress(2, self.resume_after)

    def stuck_sync_runs(self) -> list[StuckSyncRun]:
        self.calls.append(("stuck", None))
        return self.stuck


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def harness(monkeypatch):
    repository = FakeRepository()
    created: list[FakeRepository] = []

    def create_repository() -> FakeRepository:
        created.append(repository)
        return repository

    resource = SimpleNamespace(database_url="fake://weather", create_repository=create_repository)
    clock = Clock()
    monkeypatch.setattr(sync_recovery, "_STATES", {})
    monkeypatch.setattr(common_dagster, "wall_clock", clock)
    monkeypatch.setattr(sync_recovery, "_now", clock)
    SYNC_RECOVERY_STALLED.set(0)
    instance = DagsterInstance.ephemeral()
    state = {"cursor": None}

    def tick():
        result = sync_recovery.weather_sync_recovery_sensor.evaluate_tick(
            build_sensor_context(
                instance=instance,
                cursor=state["cursor"],
                resources={"weather_repository": resource},
            )
        )
        state["cursor"] = result.cursor
        return result

    yield SimpleNamespace(
        repository=repository, created=created, clock=clock, tick=tick, state=state
    )
    instance.dispose()


def _stuck(hours: float) -> StuckSyncRun:
    return StuckSyncRun(
        run_id=f"sync-{hours}",
        provider="python-kma-api",
        dataset_key="kma_ultra_short_nowcast",
        orchestrator_run_id=f"dagster-{hours}",
        heartbeat_at=datetime.now(UTC) - timedelta(hours=hours),
    )


def _stalled() -> float:
    return SYNC_RECOVERY_STALLED._value.get()


class AlertLog:
    """Dagster context.log은 stderr handler로 나가며 python logging으로 전파되지 않는다."""

    def __init__(self, capfd) -> None:
        self.capfd = capfd
        self.text = ""

    def lines(self) -> list[str]:
        self.text += self.capfd.readouterr().err
        marker = f"{sync_recovery.STALL_EVENT} key="
        return [line for line in self.text.splitlines() if marker in line]


def test_definition_uses_budget_and_stall_alert():
    assert definitions.weather_sync_recovery_sensor is sync_recovery.weather_sync_recovery_sensor
    # Dagster sensor 평가 상한(기본 60초) 안에 예산과 경보 기록이 들어간다.
    assert 0 < sync_recovery.BUDGET_SECONDS <= 45
    assert sync_recovery.STALL_AFTER_SECONDS == 900
    assert sync_recovery.REALERT_AFTER_SECONDS == 3600


def test_engine_is_reused_and_walk_resumes_where_the_budget_stopped(harness):
    position = (datetime(2026, 10, 21, tzinfo=UTC), "sync-b")
    harness.repository.resume_after = position
    first = harness.tick()
    harness.repository.resume_after = None
    second = harness.tick()
    third = harness.tick()

    assert len(harness.created) == 1
    walks = [after for name, after in harness.repository.calls if name == "interrupted"]
    # 첫 tick은 처음부터, 둘째는 첫 tick이 멈춘 위치에서, 끝에 닿은 뒤에는 다시 처음부터.
    assert walks == [None, position, None]
    for result in (first, second, third):
        assert result.skip_message == "중단된 실행 기록 3건 회수"
        assert result.cursor == "stall:null"


def test_failed_repository_creation_is_retried_next_tick(harness, monkeypatch):
    attempts = []

    def broken():
        attempts.append(1)
        raise ConnectionError("database unavailable")

    resource = SimpleNamespace(database_url="fake://broken", create_repository=broken)
    context = build_sensor_context(
        instance=DagsterInstance.ephemeral(), resources={"weather_repository": resource}
    )
    for _ in range(2):
        result = sync_recovery.weather_sync_recovery_sensor.evaluate_tick(context)
        assert result.skip_message == "회수 실패 기록: ConnectionError"
    assert len(attempts) == 2
    assert "fake://broken" not in sync_recovery._STATES


def test_wedged_runs_alert_after_the_stall_window_and_clear_on_recovery(harness, capfd):
    """deploy/dagster.yaml의 18시간 정체: 프로세스가 살아 멈춘 run 10개가 lease를 넘겼다.

    회수는 그 행을 건드리지 못하고(orchestrator가 아직 끝나지 않았다고 답한다) 수집 schedule은
    max_concurrent_runs에 막힌다. 예전 sensor는 매 tick "0건 회수"로 성공했다.
    """
    log = AlertLog(capfd)
    harness.repository.stuck = [_stuck(3 + index * 15 / 9) for index in range(10)]

    first = harness.tick()
    assert first.skip_message == "회수 실패 기록: SyncRunsStuck"
    assert harness.state["cursor"].startswith("stall:{")
    # 회수 자체는 그 tick에도 끝까지 했다.
    assert [name for name, _ in harness.repository.calls] == ["stale", "interrupted", "stuck"]

    for _ in range(14):  # 60초 tick, 15분 직전까지는 경보하지 않는다.
        harness.clock.now += 60
        harness.tick()
        assert _stalled() == 0
    assert log.lines() == []

    harness.clock.now += 60  # 첫 실패 뒤 900초
    harness.tick()
    assert _stalled() == 1
    alerts = log.lines()
    assert len(alerts) == 1
    message = alerts[0]
    assert "key=weather_sync_recovery" in message
    assert "stalled_seconds=900" in message
    assert "last_failure=SyncRunsStuck" in message

    # 재경보는 한 시간 뒤에만 한다.
    harness.clock.now += 3599
    harness.tick()
    assert len(log.lines()) == 1
    harness.clock.now += 1
    harness.tick()
    assert len(log.lines()) == 2

    # worker가 끝나 회수되면 다음 tick이 시계와 gauge를 지운다.
    harness.repository.stuck = []
    recovered = harness.tick()
    assert recovered.cursor == "stall:null"
    assert _stalled() == 0


def test_database_outage_alerts_through_the_same_clock(harness, capfd):
    log = AlertLog(capfd)
    harness.repository.error = ConnectionError("connection refused")
    harness.tick()
    harness.clock.now += 900
    result = harness.tick()
    assert result.skip_message == "회수 실패 기록: ConnectionError"
    assert _stalled() == 1
    alerts = log.lines()
    assert len(alerts) == 1 and "last_failure=ConnectionError" in alerts[0]
    # 원문 오류 메시지는 cursor에 들어가지 않는다.
    assert "connection refused" not in harness.state["cursor"]


def test_stuck_error_names_rows_without_credentials():
    error = sync_recovery.SyncRunsStuck([_stuck(4), _stuck(18)], recovered=0)
    text = str(error)
    assert "2건" in text and "python-kma-api/kma_ultra_short_nowcast" in text
    assert "dagster-4" in text and "dagster-18" in text
