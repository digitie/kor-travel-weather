"""worker 중단·lease 유효·조회 장애·늦은 publish의 복구 경계를 검증한다."""

import os
import uuid

import pytest
from sqlalchemy import text

from kortravelweather.repository import WeatherRepository


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
