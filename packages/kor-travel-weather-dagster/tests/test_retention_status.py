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


def test_a_purge_batch_that_kept_losing_is_deferred_and_named() -> None:
    """포인터·원천 정리 batch가 재시도 끝에도 상한에 지면 그 밤은 연기다(2026-11-24 L4)."""
    assert _status(pointers_deferred=True) == "deferred"
    assert _status(sources_deferred=True) == "deferred"
    result = run_weather_retention_purge(
        repository=_Repository(_report(pointers_deferred=True, pointers_deleted=3)),
        retention_days=16,
    )
    assert result["pointers_deferred"] is True and result["pointers_deleted"] == 3
    assert result["sources_deferred"] is False


def test_sources_deferred_on_consecutive_runs_is_partial() -> None:
    """원천 정리는 파티션 표시가 없으므로 연기가 이어진 실행 수로 올린다.

    연기된 실행은 진 batch 위치를 다음 실행에 넘긴다. 같은 곳에서 거듭 지면 그 뒤 원천 정리가
    멈춘 것이므로 둘째 연속부터 ``partial``이다. 연기가 끊기면 0으로 돌아간다.
    """

    def run(prior: int, **fields: object) -> dict[str, object]:
        return run_weather_retention_purge(
            repository=_Repository(_report(**fields)),
            retention_days=16,
            prior_sources_deferred_runs=prior,
        )

    first = run(0, sources_deferred=True, sources_resume_after="sr_b")
    assert first["status"] == "deferred" and first["sources_deferred_runs"] == 1
    assert first["sources_resume_after"] == "sr_b"
    second = run(1, sources_deferred=True, sources_resume_after="sr_b")
    assert second["status"] == "partial" and second["sources_deferred_runs"] == 2
    recovered = run(2, sources_scanned=4, sources_resume_after="sr_d")
    # 예산으로 멈춘 것(위치만 있고 연기 아님)은 정상 진행이다.
    assert recovered["status"] == "ok" and recovered["sources_deferred_runs"] == 0
    assert recovered["sources_scanned"] == 4


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


def test_the_source_position_and_deferrals_reach_the_next_run_through_the_run_record() -> None:
    """원천 정리 위치와 연기 횟수도 같은 observation으로 다음 실행에 간다(2026-11-24 L4 리뷰).

    예전에는 실행마다 처음부터 읽어, 같은 위치의 batch가 매번 지면 그 뒤 원천은 경보 없이
    정리되지 않았다. 이제 다음 실행이 그 위치부터 다시 하고, 거듭 지면 ``partial``이다. 실패한
    실행(파티션 overdue)도 위치를 넘긴다.
    """
    from dagster import DagsterInstance, materialize

    repository = _RecordingRepository(
        # 예산으로 멈춤: 위치만 넘긴다.
        _report(sources_scanned=1000, sources_resume_after="sr_1"),
        # 연기 + 파티션 overdue로 failed: 위치와 횟수는 그래도 다음 실행에 간다.
        _report(
            sources_deferred=True,
            sources_resume_after="sr_1",
            partitions_deferred=("weather_values_20260920",),
            partitions_overdue=("weather_values_20260920",),
        ),
        # 같은 위치에서 또 연기: 둘째 연속이라 partial.
        _report(
            sources_deferred=True,
            sources_resume_after="sr_1",
            partitions_dropped=("weather_values_20260920",),
        ),
        # 끝까지 읽음: 다음 실행은 처음부터.
        _report(sources_scanned=10),
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
            for _ in range(5)
        ]
    assert [run.success for run in runs] == [True, False, True, True, True]
    assert [call["sources_after"] for call in repository.calls] == [
        None, "sr_1", "sr_1", "sr_1", None,
    ]
    third = runs[2].output_for_node("weather_retention_purge")
    assert third["status"] == "partial" and third["sources_deferred_runs"] == 2
    assert runs[3].output_for_node("weather_retention_purge")["sources_deferred_runs"] == 0


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
