"""Grid facts publish per location chunk, not in one transaction holding every location.

On 2026-10-02 a ``kma_short_forecast_job`` run published its whole sweep in
one ``publish_and_finish`` transaction: 4h03m holding 1,103 location advisory
locks, with three other KMA jobs queued 2h+ behind it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from kortravelweather_dagster import kma_weather
from kortravelweather_dagster.kma_weather import WeatherTarget, run_weather_sync

from kortravelweather.models import WeatherLocation


class _Repository:
    """Records what each transaction would hold, like ``WeatherRepository``."""

    def __init__(self, *, fail_on_batch: int | None = None) -> None:
        self.transactions: list[dict] = []
        self.runs: list = []
        self._fail_on_batch = fail_on_batch

    def start_sync_run(self, **kwargs):
        run = SimpleNamespace(run_id="run-1", status="running")
        self.runs.append(run)
        return run

    def _transaction(self, source_records, values) -> int:
        if self._fail_on_batch is not None and len(self.transactions) + 1 == self._fail_on_batch:
            raise RuntimeError("publish failed")
        self.transactions.append(
            {
                "locations": {value.location_id for value in values},
                "values": list(values),
                "sources": {record["source_record_key"] for record in source_records},
                "run_ids": {record.get("run_id") for record in source_records},
            }
        )
        return len(values)

    def ingest_batch(self, *, source_records, values):
        return self._transaction(source_records, values)

    def publish_and_finish(self, *, run_id, source_records, values, **kwargs):
        loaded = self._transaction(source_records, values) + kwargs["values_loaded_offset"]
        self.runs[-1].status = "success"
        self.runs[-1].values_loaded = loaded
        return loaded, SimpleNamespace(run_id=run_id, status="success")

    def finish_sync_run(self, run_id, **kwargs):
        self.runs[-1].status = kwargs["status"]
        self.runs[-1].values_loaded = kwargs.get("values_loaded")
        return self.runs[-1]


class _Client:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def now(self, **kwargs):
        return SimpleNamespace(
            nx=kwargs["nx"],
            ny=kwargs["ny"],
            raw={
                "items": [
                    {
                        "baseDate": "20260101",
                        "baseTime": "0100",
                        "nx": kwargs["nx"],
                        "ny": kwargs["ny"],
                        "category": "T1H",
                        "obsrValue": "3",
                    }
                ]
            },
        )


def _targets(grids: int, per_grid: int) -> list[WeatherTarget]:
    return [
        WeatherTarget(
            WeatherLocation(
                location_id=f"g{grid:02d}-{index}",
                name=f"g{grid:02d}-{index}",
                latitude=37.5,
                longitude=127.0,
                nx=50 + grid,
                ny=127,
            )
        )
        for grid in range(grids)
        for index in range(per_grid)
    ]


def _run(repository, monkeypatch, *, locations: int = 2, grids: int = 3, per_grid: int = 2):
    monkeypatch.setattr(kma_weather, "GRID_PUBLISH_LOCATIONS", locations)
    return run_weather_sync(
        repository=repository,
        client=_Client(),
        targets=_targets(grids, per_grid),
        base_datasets=frozenset({kma_weather.KMA_ULTRA_SHORT_NOWCAST}),
    )


def test_grid_facts_publish_in_location_chunks(monkeypatch) -> None:
    repository = _Repository()
    result = _run(repository, monkeypatch)

    assert result["status"] == "success"
    assert result["values_loaded"] == 6
    publishes = [t for t in repository.transactions if t["values"]]
    assert [len(t["locations"]) for t in publishes] == [2, 2, 2]
    assert set().union(*(t["locations"] for t in publishes)) == {
        f"g{grid:02d}-{index}" for grid in range(3) for index in range(2)
    }
    # The run's finish carries no facts, so it holds no location lock.
    assert repository.transactions[-1]["values"] == []


def test_every_chunk_carries_its_cited_sources_for_the_ownership_check(monkeypatch) -> None:
    repository = _Repository()
    _run(repository, monkeypatch, locations=3)
    for transaction in repository.transactions:
        if not transaction["values"]:
            continue
        cited = {value.source_record_key for value in transaction["values"]}
        assert cited <= transaction["sources"]
        assert transaction["run_ids"] == {"run-1"}


def test_locations_sharing_a_grid_publish_together(monkeypatch) -> None:
    # First-appearance order keeps a grid's locations -- and its one source
    # record -- in one chunk, so the source is not re-sent per location.
    repository = _Repository()
    _run(repository, monkeypatch, locations=2, grids=3, per_grid=2)
    publishes = [t for t in repository.transactions if t["values"]]
    assert all(len(t["sources"]) == 1 for t in publishes)


def test_value_cap_splits_chunks_but_never_a_location(monkeypatch) -> None:
    monkeypatch.setattr(kma_weather, "GRID_PUBLISH_VALUES", 1)
    repository = _Repository()
    _run(repository, monkeypatch, locations=50)
    publishes = [t for t in repository.transactions if t["values"]]
    assert [len(t["locations"]) for t in publishes] == [1] * 6


def test_a_failed_grid_chunk_keeps_what_was_published_and_reports_it(monkeypatch) -> None:
    repository = _Repository(fail_on_batch=2)
    with pytest.raises(RuntimeError, match="publish failed"):
        _run(repository, monkeypatch)
    assert repository.runs[-1].status == "failed"
    assert repository.runs[-1].values_loaded == 2


def test_the_default_chunk_bounds_the_locks_a_transaction_holds() -> None:
    assert 0 < kma_weather.GRID_PUBLISH_LOCATIONS <= 100
    assert 0 < kma_weather.GRID_PUBLISH_VALUES <= 10_000
