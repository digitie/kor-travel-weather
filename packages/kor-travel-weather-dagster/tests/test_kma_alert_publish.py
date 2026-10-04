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

from kortravelweather import metrics
from kortravelweather.models import WeatherLocation


def _skipped_metric() -> float:
    counter = getattr(metrics, "SYNC_LOCATIONS_SKIPPED", None)
    if counter is None:  # RED: the counter does not exist yet
        return 0.0
    return counter.labels(provider="python-kma-api", dataset="kma_weather_alerts")._value.get()


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
        held = self._held if self._held_calls is None or self.calls <= self._held_calls else set()
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


def test_multiple_notices_publish_before_full_national_fanout(monkeypatch):
    repository = _Repository()
    monkeypatch.setattr(kma_weather, "KMA_STAGE_VALUES", 30)
    original_match = kma_weather._warning_matches_target

    def match(item, target):
        if target.location.location_id == "loc-40":
            assert repository._committed > 0
        return original_match(item, target)

    monkeypatch.setattr(kma_weather, "_warning_matches_target", match)

    class Client(_Client):
        async def weather_warning_list(self, **kwargs):
            return [
                dict((await super().weather_warning_list(**kwargs))[0], tmSeq=str(i))
                for i in range(3)
            ]

    result = run_weather_sync(
        repository=repository,
        client=_Client(),
        targets=[],
        include_base=False,
        include_alerts=True,
        data_client=Client(),
        alert_targets=_targets(100),
        sync_run=repository.start_sync_run(),
    )
    assert result["values_loaded"] == 300


def test_later_notice_success_does_not_hide_an_earlier_notice_skip(monkeypatch):
    repository = _Repository(held={"loc-00"}, held_calls=1)
    monkeypatch.setattr(kma_weather, "KMA_STAGE_VALUES", 20)
    monkeypatch.setattr(kma_weather, "ALERT_SKIP_RETRY_ROUNDS", 0)

    class Client(_Client):
        async def weather_warning_list(self, **kwargs):
            item = (await super().weather_warning_list(**kwargs))[0]
            return [item, dict(item, tmSeq="2")]

    result = run_weather_sync(
        repository=repository,
        client=_Client(),
        targets=[],
        include_base=False,
        include_alerts=True,
        data_client=Client(),
        alert_targets=_targets(20),
        sync_run=repository.start_sync_run(),
    )
    assert result["values_loaded"] == 39
    assert result["alert_locations_skipped"] == 1
    assert "loc-00" in repository.runs[-1].error


def test_alert_flush_keeps_the_total_normalization_budget(monkeypatch):
    repository = _Repository()
    monkeypatch.setattr(kma_weather, "KMA_STAGE_VALUES", 10)
    with pytest.raises(ValueError, match="normalized fact"):
        run_weather_sync(
            repository=repository,
            client=_Client(),
            targets=[],
            include_base=False,
            include_alerts=True,
            data_client=_Client(),
            alert_targets=_targets(25),
            max_values=21,
            sync_run=repository.start_sync_run(),
        )
    assert repository.runs[-1].values_loaded == 20


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
    # One of 20 (5%) is under the partial threshold: the run is a success.
    repository = _Repository(held={"loc-03"})
    before = _skipped_metric()
    with caplog.at_level(logging.WARNING, logger=kma_weather.__name__):
        result = _run(repository, monkeypatch, targets=20)

    assert result["status"] == "success"
    assert result["values_loaded"] == 19
    assert result["alert_locations_skipped"] == 1
    assert "loc-03" not in set().union(*repository.batches)
    # Recorded on the run, with the ID, for the operator and the next tick.
    assert repository.runs[-1].status == "success"
    assert "loc-03" in repository.runs[-1].error
    assert "loc-03" in caplog.text
    # And counted, for an alert on the rate.
    assert _skipped_metric() - before == 1


def test_skipping_more_than_the_threshold_finishes_the_run_partial(monkeypatch) -> None:
    # A success hides chronic skipping from KorTravelWeatherSyncFailed, which
    # pages on failed|partial.  6 of 50 (12%) is over ALERT_PARTIAL_SKIP_SHARE
    # and at least ALERT_PARTIAL_MIN_SKIPPED.
    repository = _Repository(held={f"loc-{i:02d}" for i in range(6)})
    result = _run(repository, monkeypatch, targets=50)

    assert result["status"] == "partial"
    assert repository.runs[-1].status == "partial"
    assert result["values_loaded"] == 44


def test_one_routine_skip_under_a_small_notice_stays_a_success(monkeypatch) -> None:
    # A quiet hour: one regional notice for 5 locations, one held.  20% of a
    # small denominator is not chronic skipping and must not page.
    repository = _Repository(held={"loc-03"})
    result = _run(repository, monkeypatch, targets=5)

    assert result["status"] == "success"
    assert "loc-03" in repository.runs[-1].error


@pytest.mark.parametrize("targets", [1, 4])
def test_a_small_notice_skipped_whole_is_recorded_not_failed(monkeypatch, targets) -> None:
    # Every location skipped, but fewer than ALERT_PARTIAL_MIN_SKIPPED: the
    # note records them and starvation catches a location held tick after
    # tick; one held location does not fail the run.
    repository = _Repository(held={f"loc-{i:02d}" for i in range(targets)})
    result = _run(repository, monkeypatch, targets=targets)

    assert result["status"] == "success"
    assert result["values_loaded"] == 0
    assert result["alert_locations_skipped"] == targets
    assert "loc-00" in repository.runs[-1].error


def test_a_location_released_before_the_retry_round_publishes(monkeypatch) -> None:
    # The first pass sees loc-03 held; the retry round after the other chunks
    # finds it free and publishes it in this same tick.
    repository = _Repository(held={"loc-03"}, held_calls=3)
    result = _run(repository, monkeypatch)

    assert result["status"] == "success"
    assert result["values_loaded"] == 5
    assert result["alert_locations_skipped"] == 0
    assert repository.runs[-1].error is None


def test_a_chunk_that_times_out_on_another_lock_fails_the_run_at_once(monkeypatch) -> None:
    # Locations are only tried, so a chunk that spends its whole lock-race
    # budget waited on something else -- partition DDL, the run row.  Every
    # later chunk would spend ~2 minutes the same way, so the run stops there
    # (the behaviour before skipping) and says it was not a location lock.
    repository = _Repository(conflict={"loc-02"})
    with pytest.raises(RuntimeError, match="location lock이 아닌") as raised:
        _run(repository, monkeypatch)

    assert isinstance(raised.value.__cause__, OperationalError)
    assert repository.calls == 2  # no later chunk, no retry round
    assert set().union(*repository.batches) == {"loc-00", "loc-01"}
    assert repository.runs[-1].status == "failed"
    assert "location lock이 아닌" in repository.runs[-1].error


def test_the_run_fails_when_no_location_could_be_published(monkeypatch) -> None:
    repository = _Repository(held={f"loc-{i:02d}" for i in range(5)})
    with pytest.raises(RuntimeError, match="lock"):
        _run(repository, monkeypatch)
    assert repository.runs[-1].status == "failed"
    assert repository.values == []


def test_a_location_skipped_three_ticks_running_is_reported_as_starved(monkeypatch, caplog) -> None:
    # Under the share threshold each time, so only starvation turns the third
    # run partial -- and pages through KorTravelWeatherSyncFailed.
    repository = _Repository(held={"loc-03"})
    first = _run(repository, monkeypatch, targets=20)
    second = _run(repository, monkeypatch, targets=20)
    with caplog.at_level(logging.WARNING, logger=kma_weather.__name__):
        third = _run(repository, monkeypatch, targets=20)

    assert first["alert_locations_starved"] == []
    assert second["alert_locations_starved"] == []
    assert third["alert_locations_starved"] == ["loc-03"]
    assert "loc-03" in caplog.text and "3" in caplog.text
    assert [run.status for run in repository.runs] == ["success", "success", "partial"]


def test_a_tick_without_skips_breaks_the_streak(monkeypatch) -> None:
    repository = _Repository(held={"loc-03"})
    _run(repository, monkeypatch, targets=20)
    repository._held = set()
    _run(repository, monkeypatch, targets=20)
    repository._held = {"loc-03"}
    assert _run(repository, monkeypatch, targets=20)["alert_locations_starved"] == []


def test_a_run_that_skipped_everything_continues_the_streak(monkeypatch) -> None:
    # The middle run publishes nothing and fails; it skipped loc-03 too.
    repository = _Repository(held={"loc-03"})
    _run(repository, monkeypatch, targets=20)
    repository._held = {f"loc-{i:02d}" for i in range(20)}
    with pytest.raises(RuntimeError):
        _run(repository, monkeypatch, targets=20)
    repository._held = {"loc-03"}
    assert _run(repository, monkeypatch, targets=20)["alert_locations_starved"] == ["loc-03"]


def test_an_unrelated_failed_run_neither_counts_nor_breaks_the_streak(monkeypatch) -> None:
    repository = _Repository(held={"loc-03"})
    _run(repository, monkeypatch, targets=20)
    repository._fail_on_batch = repository.calls + 1
    with pytest.raises(RuntimeError, match="publish failed"):
        _run(repository, monkeypatch, targets=20)
    repository._fail_on_batch = None
    assert _run(repository, monkeypatch, targets=20)["alert_locations_starved"] == []
    assert _run(repository, monkeypatch, targets=20)["alert_locations_starved"] == ["loc-03"]


def test_the_skip_note_round_trips_any_id_and_fits_a_failure_message() -> None:
    awkward = ["a,b", "c]d", "e f", "plain"]
    note = kma_weather._alert_skip_note(sorted(awkward))
    assert kma_weather._alert_skips_in(note) == set(awkward)
    # 1,450 station IDs: the note stays inside the 2,000 characters a failed
    # run keeps of its error, and the IDs it lists still parse.
    many = sorted(f"airkorea-station-{i:06d}" for i in range(1450))
    note = kma_weather._alert_skip_note(many)
    assert len(note) <= 2000
    listed = kma_weather._alert_skips_in(note[:2000])
    assert listed and listed <= set(many)
    assert "1450" in note


def test_the_default_chunk_bounds_the_locks_a_transaction_holds() -> None:
    assert 0 < kma_weather.ALERT_PUBLISH_LOCATIONS <= 100
