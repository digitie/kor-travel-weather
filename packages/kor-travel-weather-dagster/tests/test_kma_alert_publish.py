"""Alerts publish per location chunk, not in one transaction holding every location.

On 2026-10-01 one alerts run held the advisory locks of all 1,450 locations
for over an hour while its single batch inserted, and every other writer --
the KMA grid jobs, the external providers -- queued behind it.

On 2026-10-03 the chunked alerts run failed most hourly ticks instead: one
location lock held by another job's publish chunk for longer than the whole
retry budget failed the run.  A held location is now skipped for the tick.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from kortravelweather_dagster import kma_weather
from kortravelweather_dagster.kma_weather import WeatherTarget, run_weather_sync
from sqlalchemy.exc import OperationalError

from kortravelweather.models import WeatherLocation


def _lock_timeout() -> OperationalError:
    # What ``_retry_lock_race`` re-raises once its attempts are spent.
    orig = Exception("canceling statement due to lock timeout")
    orig.sqlstate = "55P03"  # type: ignore[attr-defined]
    return OperationalError("SELECT pg_advisory_xact_lock(hashtext(%(scope)s))", {}, orig)


class _Repository:
    def __init__(
        self,
        *,
        fail_on_batch: int | None = None,
        held: set[str] | None = None,
        held_calls: int | None = None,
        conflict: set[str] | None = None,
    ) -> None:
        self.batches: list[set[str]] = []
        self.values: list = []
        self.runs: list = []
        self.calls = 0
        self._fail_on_batch = fail_on_batch
        #: Locations another writer holds; for the first ``held_calls``
        #: publishes only, or for good.
        self._held = held or set()
        self._held_calls = held_calls
        #: Locations whose chunk spends every lock-race retry on some other
        #: lock (the whole transaction gives up).
        self._conflict = conflict or set()
        self._committed = 0

    def start_sync_run(self, **kwargs):
        run = SimpleNamespace(
            run_id=f"run-{len(self.runs) + 1}",
            status="running",
            provider="python-kma-api",
            dataset_key="kma_weather_alerts",
            error=None,
        )
        self.runs.append(run)
        self._committed = 0
        return run

    def ingest_batch(self, *, source_records, values):
        # Only the run's finish (no facts) may publish without skipping.
        assert not values, "alert facts must publish through ingest_skip_locked"
        return 0

    def ingest_skip_locked(self, *, source_records, values):
        self.calls += 1
        if self._fail_on_batch is not None and self.calls == self._fail_on_batch:
            raise RuntimeError("publish failed")
        locations = {value.location_id for value in values}
        if locations & self._conflict:
            raise _lock_timeout()
        held = (
            self._held
            if self._held_calls is None or self.calls <= self._held_calls
            else set()
        )
        kept = [value for value in values if value.location_id not in held]
        if kept:
            self.batches.append({value.location_id for value in kept})
        self.values.extend(kept)
        self._committed += len(kept)
        return len(kept), sorted(locations & held)

    def list_sync_runs(self, *, limit=50, provider=None, dataset_key=None):
        return [
            run
            for run in reversed(self.runs)
            if (provider is None or run.provider == provider)
            and (dataset_key is None or run.dataset_key == dataset_key)
        ][:limit]

    def finish_sync_run(self, run_id, **kwargs):
        run = self.runs[-1]
        run.status = kwargs["status"]
        run.error = kwargs.get("error")
        # Like the repository: ``None`` keeps the count the committed
        # publishes recorded on the run row.
        recorded = kwargs.get("values_loaded")
        run.values_loaded = self._committed if recorded is None else recorded
        return run


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
    # Alembic's fileConfig (the migration tests) disables loggers that
    # already exist, and the full suite runs them before these tests.
    monkeypatch.setattr(kma_weather.logger, "disabled", False)
    # raising=False: a RED run fails on behaviour, not on a missing name.
    monkeypatch.setattr(kma_weather, "ALERT_SKIP_RETRY_SECONDS", 0, raising=False)
    return run_weather_sync(
        repository=repository,
        client=_Client(),
        targets=[],
        include_base=False,
        include_alerts=True,
        data_client=_Client(),
        alert_targets=_targets(targets),
        sync_run=repository.start_sync_run(),
    )


def test_alerts_publish_in_location_chunks(monkeypatch) -> None:
    repository = _Repository()
    result = _run(repository, monkeypatch)

    assert result["status"] == "success"
    assert result["values_loaded"] == 5
    assert [len(batch) for batch in repository.batches] == [2, 2, 1]
    assert set().union(*repository.batches) == {f"loc-{i:02d}" for i in range(5)}
    assert result["alert_locations_skipped"] == 0
    assert repository.runs[-1].error is None


def test_a_failed_chunk_keeps_what_was_published_and_reports_it(monkeypatch) -> None:
    # The documented trade-off: alerts are no longer all-or-nothing.  What a
    # chunk published stays, and the failed run says how much that was.  A
    # non-lock error still fails the run.
    repository = _Repository(fail_on_batch=3)
    with pytest.raises(RuntimeError, match="publish failed"):
        _run(repository, monkeypatch)
    assert len(repository.values) == 4
    assert repository.runs[-1].status == "failed"
    assert repository.runs[-1].values_loaded == 4


def test_a_held_location_is_skipped_for_the_tick_not_fatal(monkeypatch, caplog) -> None:
    repository = _Repository(held={"loc-03"})
    with caplog.at_level(logging.WARNING, logger=kma_weather.__name__):
        result = _run(repository, monkeypatch)

    assert result["status"] == "success"
    assert result["values_loaded"] == 4
    assert result["alert_locations_skipped"] == 1
    assert set().union(*repository.batches) == {"loc-00", "loc-01", "loc-02", "loc-04"}
    # Recorded on the run, with the ID, for the operator and the next tick.
    assert repository.runs[-1].status == "success"
    assert "loc-03" in repository.runs[-1].error
    assert "loc-03" in caplog.text


def test_a_location_released_before_the_retry_round_publishes(monkeypatch) -> None:
    # The first pass sees loc-03 held; the retry round after the other chunks
    # finds it free and publishes it in this same tick.
    repository = _Repository(held={"loc-03"}, held_calls=3)
    result = _run(repository, monkeypatch)

    assert result["status"] == "success"
    assert result["values_loaded"] == 5
    assert result["alert_locations_skipped"] == 0
    assert repository.runs[-1].error is None


def test_a_chunk_that_times_out_skips_only_its_locations(monkeypatch) -> None:
    # A chunk whose transaction spends every lock-race retry is skipped as a
    # whole; the chunks around it still publish and the run succeeds.
    repository = _Repository(conflict={"loc-02"})
    result = _run(repository, monkeypatch)

    assert result["status"] == "success"
    assert set().union(*repository.batches) == {"loc-00", "loc-01", "loc-04"}
    assert result["alert_locations_skipped"] == 2
    assert repository.runs[-1].status == "success"
    assert "loc-02" in repository.runs[-1].error and "loc-03" in repository.runs[-1].error


def test_the_run_fails_when_no_location_could_be_published(monkeypatch) -> None:
    repository = _Repository(held={f"loc-{i:02d}" for i in range(5)})
    with pytest.raises(RuntimeError, match="lock"):
        _run(repository, monkeypatch)
    assert repository.runs[-1].status == "failed"
    assert repository.values == []


def test_a_location_skipped_three_ticks_running_is_reported_as_starved(
    monkeypatch, caplog
) -> None:
    repository = _Repository(held={"loc-03"})
    first = _run(repository, monkeypatch)
    second = _run(repository, monkeypatch)
    with caplog.at_level(logging.WARNING, logger=kma_weather.__name__):
        third = _run(repository, monkeypatch)

    assert first["alert_locations_starved"] == []
    assert second["alert_locations_starved"] == []
    assert third["alert_locations_starved"] == ["loc-03"]
    assert "loc-03" in caplog.text and "3" in caplog.text


def test_a_tick_without_skips_breaks_the_streak(monkeypatch) -> None:
    repository = _Repository(held={"loc-03"})
    _run(repository, monkeypatch)
    repository._held = set()
    _run(repository, monkeypatch)
    repository._held = {"loc-03"}
    assert _run(repository, monkeypatch)["alert_locations_starved"] == []


def test_the_default_chunk_bounds_the_locks_a_transaction_holds() -> None:
    assert 0 < kma_weather.ALERT_PUBLISH_LOCATIONS <= 100
