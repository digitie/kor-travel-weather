"""worker 중단·lease 유효·조회 장애·늦은 publish의 복구 경계를 검증한다."""

import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from kortravelweather.repository import (
    RECONCILE_PAGE_RESERVE_SECONDS,
    RECONCILE_ROW_RESERVE_SECONDS,
    WeatherRepository,
)


@pytest.fixture
def repository():
    repo = WeatherRepository(
        os.environ.get(
            "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
            "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
        )
    )
    repo.create_schema()
    yield repo
    repo.engine.dispose()


def test_terminal_worker_is_recovered_even_with_fresh_heartbeat(repository):
    provider = f"recovery-{uuid.uuid4().hex}"
    repository.orchestrator_run_id = "terminated-worker"
    interrupted = repository.start_sync_run(provider=provider, dataset_key="test")
    repository.orchestrator_run_id = "live-worker"
    live = repository.start_sync_run(provider=provider, dataset_key="live")
    seen = []

    def terminal(owner):
        seen.append(owner)
        return owner == "terminated-worker"

    assert repository.reconcile_interrupted_sync_runs(terminal) == 1
    assert "terminated-worker" in seen
    assert repository.heartbeat_sync_run(interrupted.run_id) is False
    assert repository.heartbeat_sync_run(live.run_id) is True
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE weather_sync_runs SET heartbeat_at = now() - interval '4 hours' "
                "WHERE run_id = :run_id"
            ),
            {"run_id": live.run_id},
        )
    # 장시간 작업의 worker가 살아 있으면 heartbeat age만으로 회수하지 않는다.
    repository.reconcile_stale_sync_runs()
    assert repository.heartbeat_sync_run(live.run_id) is True
    late = repository.finish_sync_run(interrupted.run_id, status="success")
    assert late.status == "failed"
    assert repository.reconcile_interrupted_sync_runs(terminal) == 0
    replacement = repository.start_sync_run(provider=provider, dataset_key="test")
    assert replacement.status == "running"
    repository.finish_sync_run(live.run_id, status="success")
    repository.finish_sync_run(replacement.run_id, status="success")


def test_orchestrator_outage_preserves_running_record(repository):
    repository.orchestrator_run_id = "unknown-worker"
    run = repository.start_sync_run(provider=f"outage-{uuid.uuid4().hex}", dataset_key="test")

    def unavailable(owner):
        raise ConnectionError("metadata storage unavailable")

    with pytest.raises(ConnectionError):
        repository.reconcile_interrupted_sync_runs(unavailable)
    assert repository.heartbeat_sync_run(run.run_id) is True
    repository.finish_sync_run(run.run_id, status="failed")


def test_legacy_rows_still_recover_without_a_new_ingest(repository):
    run = repository.start_sync_run(provider=f"legacy-{uuid.uuid4().hex}", dataset_key="test")
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE weather_sync_runs SET heartbeat_at = now() - interval '4 hours' "
                "WHERE run_id = :run_id"
            ),
            {"run_id": run.run_id},
        )
    assert repository.reconcile_stale_sync_runs() >= 1
    assert repository.heartbeat_sync_run(run.run_id) is False


def test_lost_metadata_needs_an_expired_lease_before_recovery(repository):
    repository.orchestrator_run_id = "lost-metadata"
    run = repository.start_sync_run(provider=f"missing-{uuid.uuid4().hex}", dataset_key="test")
    assert repository.reconcile_interrupted_sync_runs(lambda owner: None) == 0
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE weather_sync_runs SET heartbeat_at = now() - interval '4 hours' "
                "WHERE run_id = :run_id"
            ),
            {"run_id": run.run_id},
        )
    assert repository.reconcile_interrupted_sync_runs(lambda owner: None) == 1
    assert repository.heartbeat_sync_run(run.run_id) is False


def test_renewed_heartbeat_wins_over_a_missing_metadata_reaper(repository):
    repository.orchestrator_run_id = "lost-but-live"
    run = repository.start_sync_run(provider=f"race-{uuid.uuid4().hex}", dataset_key="test")
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE weather_sync_runs SET heartbeat_at = now() - interval '4 hours' "
                "WHERE run_id = :run_id"
            ),
            {"run_id": run.run_id},
        )

    def lookup(owner):
        if owner == "lost-but-live":
            assert repository.heartbeat_sync_run(run.run_id)
        return

    assert repository.reconcile_interrupted_sync_runs(lookup) == 0
    assert repository.heartbeat_sync_run(run.run_id)
    repository.finish_sync_run(run.run_id, status="success")


def test_live_first_page_does_not_starve_a_later_terminal_worker(repository):
    provider = f"pages-{uuid.uuid4().hex}"
    repository.orchestrator_run_id = "first-live"
    first = repository.start_sync_run(provider=provider, dataset_key="first")
    repository.orchestrator_run_id = "later-terminal"
    later = repository.start_sync_run(provider=provider, dataset_key="later")
    # 같은 started_at도 run_id tie-breaker로 정확히 순회한다.
    with repository.engine.begin() as connection:
        connection.execute(
            text("UPDATE weather_sync_runs SET started_at = :started_at WHERE run_id = :run_id"),
            {"started_at": first.started_at, "run_id": later.run_id},
        )
    assert (
        repository.reconcile_interrupted_sync_runs(lambda owner: owner == "later-terminal", limit=1)
        == 1
    )
    assert repository.heartbeat_sync_run(first.run_id)
    assert not repository.heartbeat_sync_run(later.run_id)
    repository.finish_sync_run(first.run_id, status="success")


def _set_started_at(repository, run_id, started_at):
    with repository.engine.begin() as connection:
        connection.execute(
            text("UPDATE weather_sync_runs SET started_at = :started_at WHERE run_id = :run_id"),
            {"started_at": started_at, "run_id": run_id},
        )


def _status(repository, run_id):
    with repository.engine.connect() as connection:
        return connection.execute(
            text("SELECT status FROM weather_sync_runs WHERE run_id = :run_id"),
            {"run_id": run_id},
        ).scalar_one()


def test_budgeted_walk_stops_between_rows_and_resumes_without_losing_rows(repository):
    provider = f"budget-{uuid.uuid4().hex}"
    runs = []
    for index in range(3):
        repository.orchestrator_run_id = f"terminal-{index}"
        runs.append(repository.start_sync_run(provider=provider, dataset_key=f"d{index}"))
    base = runs[0].started_at
    for index, run in enumerate(runs):
        _set_started_at(repository, run.run_id, base + timedelta(seconds=index))
    classified = []

    def terminal(owner):
        classified.append(owner)
        return owner.startswith("terminal-")

    def first_row_only():
        # 행 하나를 분류하면 행 예비 시간 아래로 떨어진다.
        if not classified:
            return RECONCILE_PAGE_RESERVE_SECONDS
        return RECONCILE_ROW_RESERVE_SECONDS - 1

    first = repository.reconcile_interrupted_sync_runs_within(terminal, remaining=first_row_only)
    assert first.recovered == 1
    assert first.resume_after == (base, runs[0].run_id)
    assert [_status(repository, run.run_id) for run in runs] == ["failed", "running", "running"]

    second = repository.reconcile_interrupted_sync_runs_within(
        terminal, remaining=lambda: 40.0, after=first.resume_after
    )
    assert second.recovered == 2 and second.resume_after is None
    assert [_status(repository, run.run_id) for run in runs] == ["failed"] * 3

    # 위치 앞(더 이른 started_at)에 늦게 들어온 행은 처음으로 돌아간 다음 바퀴가 본다.
    repository.orchestrator_run_id = "terminal-late"
    late = repository.start_sync_run(provider=provider, dataset_key="late")
    _set_started_at(repository, late.run_id, base - timedelta(hours=1))
    third = repository.reconcile_interrupted_sync_runs_within(terminal, remaining=lambda: 40.0)
    assert third.recovered == 1 and _status(repository, late.run_id) == "failed"


def test_budgeted_walk_does_not_start_a_page_without_its_reserve(repository):
    repository.orchestrator_run_id = "terminal"
    run = repository.start_sync_run(provider=f"reserve-{uuid.uuid4().hex}", dataset_key="d")

    def never(owner):
        raise AssertionError("예비 시간이 없으면 orchestrator를 조회하지 않는다")

    progress = repository.reconcile_interrupted_sync_runs_within(
        never, remaining=lambda: RECONCILE_PAGE_RESERVE_SECONDS - 0.1
    )
    assert progress.recovered == 0 and progress.resume_after is None
    assert _status(repository, run.run_id) == "running"


def test_stuck_sync_runs_lists_only_running_rows_past_the_lease(repository):
    provider = f"stuck-{uuid.uuid4().hex}"
    repository.orchestrator_run_id = "wedged"
    wedged = repository.start_sync_run(provider=provider, dataset_key="wedged")
    fresh = repository.start_sync_run(provider=provider, dataset_key="fresh")
    done = repository.start_sync_run(provider=provider, dataset_key="done")
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE weather_sync_runs SET heartbeat_at = now() - interval '4 hours' "
                "WHERE run_id IN (:wedged, :done)"
            ),
            {"wedged": wedged.run_id, "done": done.run_id},
        )
    repository.finish_sync_run(done.run_id, status="failed")
    stuck = repository.stuck_sync_runs()
    assert [row.run_id for row in stuck] == [wedged.run_id]
    assert stuck[0].provider == provider and stuck[0].orchestrator_run_id == "wedged"
    repository.finish_sync_run(fresh.run_id, status="success")


def test_eighteen_hour_wedge_raises_the_recovery_stall_alert(repository, monkeypatch, capfd):
    """deploy/dagster.yaml:8-9·60-66의 사고를 실제 DB와 실제 sensor로 재현한다.

    worker 10개가 프로세스는 살아 있고 socket read에서 멈춰 3~18시간 STARTED로 남았다.
    heartbeat는 멈췄지만 orchestrator는 끝나지 않았다고 답하므로 회수할 수 없고,
    max_concurrent_runs가 차서 모든 schedule이 멈췄다. 예전 sensor는 매 tick
    "0건 회수"로 성공해 아무도 몰랐다. 이제는 15분 뒤 경보한다.
    """
    import kortravelcommon.dagster as common_dagster
    from dagster import DagsterInstance, DagsterRun, DagsterRunStatus, build_sensor_context
    from kortravelweather_dagster import sync_recovery
    from kortravelweather_dagster.resources import WeatherRepositoryResource

    from kortravelweather.metrics import SYNC_RECOVERY_STALLED

    now = [1_800_000_000.0]
    monkeypatch.setattr(common_dagster, "wall_clock", lambda: now[0])
    monkeypatch.setattr(sync_recovery, "_now", lambda: now[0])
    monkeypatch.setattr(sync_recovery, "_STATES", {})
    SYNC_RECOVERY_STALLED.set(0)
    resource = WeatherRepositoryResource(
        database_url=repository.engine.url.render_as_string(hide_password=False)
    )
    instance = DagsterInstance.ephemeral()
    provider = f"wedge-{uuid.uuid4().hex}"
    wedged = []
    for index in range(10):
        owner = f"wedged-{index}"
        instance.add_run(
            DagsterRun(
                job_name="kma_ultra_short_nowcast_job",
                run_id=owner,
                status=DagsterRunStatus.STARTED,
            )
        )
        repository.orchestrator_run_id = owner
        run = repository.start_sync_run(provider=provider, dataset_key=f"d{index}")
        # heartbeat가 3시간 1분~17시간 41분 전에 멈췄다.
        with repository.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE weather_sync_runs SET heartbeat_at = now() - make_interval("
                    "mins => :minutes) WHERE run_id = :run_id"
                ),
                {"minutes": 181 + index * 98, "run_id": run.run_id},
            )
        wedged.append(run)
    cursor = [None]

    def tick():
        result = sync_recovery.weather_sync_recovery_sensor.evaluate_tick(
            build_sensor_context(
                instance=instance,
                cursor=cursor[0],
                resources={"weather_repository": resource},
            )
        )
        cursor[0] = result.cursor
        return result

    stderr = [""]

    def alerts():
        # Dagster context.log은 stderr handler로 나가며 python logging으로 전파되지 않는다.
        stderr[0] += capfd.readouterr().err
        marker = f"{sync_recovery.STALL_EVENT} key="
        return [line for line in stderr[0].splitlines() if marker in line]

    try:
        assert tick().skip_message == "회수 실패 기록: SyncRunsStuck"
        for _ in range(14):
            now[0] += 60
            tick()
        assert alerts() == [] and SYNC_RECOVERY_STALLED._value.get() == 0
        now[0] += 60
        tick()
        assert len(alerts()) == 1 and "last_failure=SyncRunsStuck" in alerts()[0]
        assert SYNC_RECOVERY_STALLED._value.get() == 1
        # 살아 있는 worker의 행은 경보 뒤에도 회수하지 않는다.
        assert {_status(repository, run.run_id) for run in wedged} == {"running"}

        # 운영자가 멈춘 run을 끝내면 다음 tick이 행을 회수하고 경보를 푼다.
        for index in range(10):
            instance.report_run_failed(instance.get_run_by_id(f"wedged-{index}"))
        now[0] += 60
        result = tick()
        assert result.skip_message == "중단된 실행 기록 10건 회수"
        assert result.cursor == "stall:null"
        assert SYNC_RECOVERY_STALLED._value.get() == 0
        assert {_status(repository, run.run_id) for run in wedged} == {"failed"}
    finally:
        for state in sync_recovery._STATES.values():
            state.repository.engine.dispose()
        instance.dispose()
