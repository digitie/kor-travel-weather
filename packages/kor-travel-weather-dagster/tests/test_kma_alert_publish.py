"""Alerts publish per location chunk, not in one transaction holding every location.

On 2026-10-01 one alerts run held the advisory locks of all 1,450 locations
for over an hour while its single batch inserted, and every other writer --
the KMA grid jobs, the external providers -- queued behind it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from kortravelweather_dagster import kma_weather
from kortravelweather_dagster.kma_weather import WeatherTarget, run_weather_sync

from kortravelweather.models import WeatherLocation


class _Repository:
    def __init__(self, *, fail_on_batch: int | None = None) -> None:
        self.batches: list[set[str]] = []
        self.values: list = []
        self.runs: list = []
        self._fail_on_batch = fail_on_batch
        self._committed = 0

    def start_sync_run(self, **kwargs):
        run = SimpleNamespace(run_id="run-1", status="running")
        self.runs.append(run)
        return run

    def ingest_batch(self, *, source_records, values):
        if self._fail_on_batch is not None and len(self.batches) + 1 == self._fail_on_batch:
            raise RuntimeError("publish failed")
        if values:
            self.batches.append({value.location_id for value in values})
        self.values.extend(values)
        self._committed += len(values)
        return len(values)

    def finish_sync_run(self, run_id, **kwargs):
        self.runs[-1].status = kwargs["status"]
        # Like the repository: ``None`` keeps the count the committed
        # publishes recorded on the run row.
        recorded = kwargs.get("values_loaded")
        self.runs[-1].values_loaded = self._committed if recorded is None else recorded
        return self.runs[-1]


class _Client:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def weather_warning_list(self, **kwargs):
        return [{"stnId": "108", "tmFc": "202601010600", "tmSeq": "1", "title": "호우주의보"}]


def _targets(count: int) -> list[WeatherTarget]:
    return [
        WeatherTarget(
            WeatherLocation(
                location_id=f"loc-{index:02d}",
                name=f"loc-{index:02d}",
                latitude=37.5,
                longitude=127.0,
                nx=60,
                ny=127,
            )
        )
        for index in range(count)
    ]


def _run(repository, monkeypatch, *, targets: int = 5, chunk: int = 2):
    monkeypatch.setattr(kma_weather, "ALERT_PUBLISH_LOCATIONS", chunk)
    return run_weather_sync(
        repository=repository,
        client=_Client(),
        targets=[],
        include_base=False,
        include_alerts=True,
        data_client=_Client(),
        alert_targets=_targets(targets),
    )


def test_alerts_publish_in_location_chunks(monkeypatch) -> None:
    repository = _Repository()
    result = _run(repository, monkeypatch)

    assert result["status"] == "success"
    assert result["values_loaded"] == 5
    assert [len(batch) for batch in repository.batches] == [2, 2, 1]
    assert set().union(*repository.batches) == {f"loc-{i:02d}" for i in range(5)}


def test_a_failed_chunk_keeps_what_was_published_and_reports_it(monkeypatch) -> None:
    # The documented trade-off: alerts are no longer all-or-nothing.  What a
    # chunk published stays, and the failed run says how much that was.
    repository = _Repository(fail_on_batch=3)
    with pytest.raises(RuntimeError, match="publish failed"):
        _run(repository, monkeypatch)
    assert len(repository.values) == 4
    assert repository.runs[-1].status == "failed"
    assert repository.runs[-1].values_loaded == 4


def test_the_default_chunk_bounds_the_locks_a_transaction_holds() -> None:
    assert 0 < kma_weather.ALERT_PUBLISH_LOCATIONS <= 100
