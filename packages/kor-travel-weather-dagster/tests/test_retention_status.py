"""How a retention run that could not do everything finishes.

A partition that loses its lock race tonight is retried tomorrow; that alone
is not a failure (ingestion never pauses, and the window has slack).  The run
goes partial or failed only when the same work has now been missed on
consecutive runs -- an *overdue* partition, or a forward window down to the
alert threshold -- and failed when, on top of that, nothing at all moved.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from dagster import Failure, build_asset_context
from kortravelweather_dagster import definitions
from kortravelweather_dagster.retention import run_weather_retention_purge

from kortravelweather.models import PurgeReport, kst_now


class _Repository:
    def __init__(self, report: PurgeReport) -> None:
        self.report = report

    def purge_expired_history(self, **_: object) -> PurgeReport:
        return self.report


def _report(**fields: object) -> PurgeReport:
    fields.setdefault("forward_partition_days", 7)
    fields.setdefault("default_floor", kst_now())
    return PurgeReport(cutoff=kst_now(), **fields)


def _status(**fields: object) -> str:
    return run_weather_retention_purge(
        repository=_Repository(_report(**fields)), retention_days=16
    )["status"]


def test_a_complete_run_is_ok() -> None:
    assert _status(partitions_dropped=("weather_values_20260920",)) == "ok"


def test_a_first_miss_is_deferred_not_partial() -> None:
    assert _status(partitions_deferred=("weather_values_20260920",)) == "deferred"
    assert _status(partitions_not_created=("weather_values_20261015",)) == "deferred"
    assert _status(foreign_key_validation_deferred=True) == "deferred"


def test_an_overdue_partition_with_progress_is_partial() -> None:
    assert (
        _status(
            partitions_dropped=("weather_values_20260921",),
            partitions_deferred=("weather_values_20260920",),
            partitions_overdue=("weather_values_20260920",),
        )
        == "partial"
    )


def test_an_overdue_partition_and_nothing_dropped_fails() -> None:
    assert (
        _status(
            partitions_deferred=("weather_values_20260920",),
            partitions_overdue=("weather_values_20260920",),
        )
        == "failed"
    )


def test_a_forward_window_at_the_alert_threshold_is_consecutive_misses() -> None:
    # Seven days of slack down to three means four nights of missed creates.
    assert (
        _status(partitions_not_created=("weather_values_20261015",), forward_partition_days=3)
        == "failed"
    )
    assert (
        _status(
            partitions_created=("weather_values_20261014",),
            partitions_not_created=("weather_values_20261015",),
            forward_partition_days=3,
        )
        == "partial"
    )


def test_the_result_names_what_was_deferred() -> None:
    result = run_weather_retention_purge(
        repository=_Repository(
            _report(
                partitions_deferred=("weather_values_20260920",),
                partitions_not_created=("weather_values_20261015",),
            )
        ),
        retention_days=16,
    )
    assert result["partitions_deferred"] == ["weather_values_20260920"]
    assert result["partitions_not_created"] == ["weather_values_20261015"]
    assert result["partitions_overdue"] == []
    assert result["foreign_key_validation_deferred"] is False


def _materialize(report: PurgeReport):
    resource = SimpleNamespace(create_repository=lambda: _Repository(report))
    with build_asset_context(resources={"weather_repository": resource}) as context:
        return definitions.weather_retention_purge(context)


def test_the_asset_succeeds_on_a_deferral() -> None:
    result = _materialize(_report(partitions_deferred=("weather_values_20260920",)))
    assert result["status"] == "deferred"


def test_the_asset_fails_only_on_consecutive_misses_with_no_progress() -> None:
    with pytest.raises(Failure) as caught:
        _materialize(
            _report(
                partitions_deferred=("weather_values_20260920",),
                partitions_overdue=("weather_values_20260920",),
            )
        )
    assert "weather_values_20260920" in str(caught.value.description)


class _RecordingRepository:
    """Hands out one report per run and records what each run was given."""

    def __init__(self, *reports: PurgeReport) -> None:
        self.reports = list(reports)
        self.calls: list[dict[str, object]] = []

    def purge_expired_history(self, **kwargs: object) -> PurgeReport:
        self.calls.append(kwargs)
        return self.reports.pop(0)


def test_the_result_names_the_days_whose_mark_was_lost() -> None:
    repository = _RecordingRepository(
        _report(
            partitions_deferred=("weather_values_20260920",),
            partitions_unmarked=("weather_values_20260920",),
        )
    )
    result = run_weather_retention_purge(
        repository=repository,
        retention_days=16,
        prior_unmarked=("weather_values_20260919",),
    )
    assert result["partitions_unmarked"] == ["weather_values_20260920"]
    assert repository.calls[0]["prior_unmarked"] == ("weather_values_20260919",)


def test_a_lost_mark_reaches_the_next_run_through_the_run_record() -> None:
    """Review LOW2 (dd99edd): a deferral mark that could not be written
    (``COMMENT ON TABLE`` kept losing its lock race) is recorded in the run's
    own Dagster record, which needs no table lock and no migration, and the
    next run is handed it.  It must survive a run that ends ``failed`` --
    that one materializes nothing."""
    from dagster import DagsterInstance, materialize

    repository = _RecordingRepository(
        # failed: overdue and nothing dropped; one more day lost its mark.
        _report(
            partitions_deferred=("weather_values_20260920", "weather_values_20260921"),
            partitions_overdue=("weather_values_20260920",),
            partitions_unmarked=("weather_values_20260921",),
        ),
        _report(partitions_dropped=("weather_values_20260920", "weather_values_20260921")),
        _report(),
    )
    resources = {"weather_repository": SimpleNamespace(create_repository=lambda: repository)}
    with DagsterInstance.ephemeral() as instance:
        runs = [
            materialize(
                [definitions.weather_retention_purge],
                instance=instance,
                resources=resources,
                raise_on_error=False,
            )
            for _ in range(3)
        ]
    assert [run.success for run in runs] == [False, True, True]
    assert [call["prior_unmarked"] for call in repository.calls] == [
        (),
        ("weather_values_20260921",),
        (),
    ]


def test_retention_runs_in_the_quiet_hour() -> None:
    """01:45 KST: the measured low point of the ingest schedules.

    Sampled from the shared Dagster run history (2026-10-04..07): 01:45-02:55
    KST averages 0.3-1.0 concurrent weather runs, and nothing heavy starts
    before the 03:15 three-hourly external sweep.  The old 03:20 slot sat
    right on top of that sweep (about four concurrent runs).
    """
    schedule = next(
        s for s in definitions.defs.schedules if s.name == "daily_weather_retention"
    )
    assert schedule.cron_schedule == "45 1 * * *"
    assert schedule.execution_timezone == "Asia/Seoul"
