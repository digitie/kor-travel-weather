"""A second start of the same provider/dataset: skip when the first is alive.

``start_sync_run`` used to raise a bare ``RuntimeError`` for every overlap, so
a slow run still publishing when the next tick fired turned that tick red.
Now a live predecessor (heartbeat inside the lease) raises
``SyncRunAlreadyActive`` -- which the Dagster boundary turns into a skip --
while one whose lease has expired keeps failing as before: that run is not
doing the work any more, and somebody should look.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

from kortravelweather.repository import SyncRunAlreadyActive, WeatherRepository


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


def _statuses(repository: WeatherRepository, provider: str) -> list[str]:
    with repository.engine.connect() as connection:
        return list(
            connection.execute(
                text("SELECT status FROM weather_sync_runs WHERE provider = :p ORDER BY 1"),
                {"p": provider},
            ).scalars()
        )


def test_a_live_predecessor_makes_the_overlap_a_skip(repository) -> None:
    provider = f"overlap-{uuid.uuid4().hex}"
    repository.orchestrator_run_id = "worker-live"
    first = repository.start_sync_run(provider=provider, dataset_key="d")

    repository.orchestrator_run_id = "worker-next"
    with pytest.raises(SyncRunAlreadyActive) as caught:
        repository.start_sync_run(provider=provider, dataset_key="d")

    overlap = caught.value
    # Still a RuntimeError: any caller that only knows that one keeps stopping.
    assert isinstance(overlap, RuntimeError)
    assert overlap.provider == provider
    assert overlap.dataset_key == "d"
    assert overlap.active_run_id == first.run_id
    assert overlap.heartbeat_at is not None
    # Nothing recorded for the skipped start: it is not a run, let alone a failed one.
    assert _statuses(repository, provider) == ["running"]
    repository.finish_sync_run(first.run_id, status="success")
    assert _statuses(repository, provider) == ["success"]


def test_a_predecessor_with_an_expired_lease_still_fails(repository) -> None:
    provider = f"stale-{uuid.uuid4().hex}"
    repository.orchestrator_run_id = "worker-silent"
    first = repository.start_sync_run(provider=provider, dataset_key="d")
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE weather_sync_runs SET heartbeat_at = now() - interval '4 hours', "
                "started_at = now() - interval '5 hours' WHERE run_id = :r"
            ),
            {"r": first.run_id},
        )

    repository.orchestrator_run_id = "worker-next"
    with pytest.raises(RuntimeError) as caught:
        repository.start_sync_run(provider=provider, dataset_key="d")
    assert not isinstance(caught.value, SyncRunAlreadyActive)
    assert "이미 진행 중" in str(caught.value)
    repository.finish_sync_run(first.run_id, status="failed", error="test cleanup")
