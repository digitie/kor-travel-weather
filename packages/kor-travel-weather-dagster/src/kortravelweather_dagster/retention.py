"""보존 기간이 지난 fact/source 이력을 매일 정리한다."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, Protocol

from kortravelweather.models import PurgeReport

#: Same threshold as the KorTravelWeatherForwardPartitionsLow alert.
FORWARD_PARTITION_ALERT_DAYS = 3
#: 원천 정리 batch가 이만큼 연속한 실행에서 연기되면 ``partial``이다. 연기된 실행은 진 batch의
#: 위치를 다음 실행에 넘기므로, 같은 곳에서 거듭 지면 그 뒤 원천 정리가 멈춘 것이다.
SOURCES_DEFERRED_PARTIAL_RUNS = 2

RetentionStatus = Literal["ok", "deferred", "partial", "failed"]


class _PurgeableRepository(Protocol):
    def purge_expired_history(
        self,
        *,
        retention_days: int,
        ahead_days: int = ...,
        prior_unmarked: Sequence[str] = ...,
        sources_after: str | None = ...,
    ) -> PurgeReport: ...


def sources_deferred_runs(report: PurgeReport, prior: int) -> int:
    """원천 정리가 연기된 연속 실행 수. 이번에 연기되지 않았으면 0으로 돌아간다."""
    return prior + 1 if report.sources_deferred else 0


def retention_status(report: PurgeReport, *, sources_deferred_in_a_row: int = 0) -> RetentionStatus:
    """How the run ends, given what it could not do.

    Ingestion never pauses, so a partition step that loses its lock race is
    deferred to the next night and that alone is ``deferred`` -- a success
    with the names in the metadata.  The run is ``partial`` only when work
    has now been missed on consecutive nights -- an overdue expired day, or a
    forward window down to the alert threshold -- and ``failed`` when, on top
    of that, this run moved nothing in that direction.

    포인터·원천 정리 batch가 재시도 끝에도 상한에 진 것(``pointers_deferred``·
    ``sources_deferred``)도 그 자체로는 ``deferred``다. 포인터를 못 끝낸 밤의 만료 일자는
    ``partitions_deferred``로 표시되므로 연속되면 위 overdue 규칙이 실패로 올린다. 원천은
    파티션 표시가 없으므로 연기된 연속 실행 수(``sources_deferred_in_a_row``, 이번 실행 포함)가
    ``SOURCES_DEFERRED_PARTIAL_RUNS`` 이상이면 ``partial``로 올린다. 시간 예산으로 멈춘 것은
    연기가 아니다(다음 실행이 위치에서 이어 읽는 정상 진행).
    """
    forward_short = bool(report.partitions_not_created) and (
        report.forward_partition_days is None
        or report.forward_partition_days <= FORWARD_PARTITION_ALERT_DAYS
    )
    if (report.partitions_overdue and not report.partitions_dropped) or (
        forward_short and not report.partitions_created
    ):
        return "failed"
    if (
        report.partitions_overdue
        or forward_short
        or sources_deferred_in_a_row >= SOURCES_DEFERRED_PARTIAL_RUNS
    ):
        return "partial"
    if (
        report.partitions_deferred
        or report.partitions_not_created
        or report.foreign_key_validation_deferred
        or report.partition_ddl_stopped
        or report.pointers_deferred
        or report.sources_deferred
    ):
        return "deferred"
    return "ok"


def run_weather_retention_purge(
    *,
    repository: _PurgeableRepository,
    retention_days: int,
    ahead_days: int = 7,
    prior_unmarked: Sequence[str] = (),
    sources_after: str | None = None,
    prior_sources_deferred_runs: int = 0,
) -> dict[str, Any]:
    """Drop the days past the window and describe what went.

    ``rows_outside_any_partition`` is the field worth watching.  Everything else
    says what retention did; that one says whether retention can still reach the
    data at all -- rows in the DEFAULT partition are never dropped, so a
    non-zero value is the table quietly starting to grow again.

    ``prior_unmarked`` is the previous run's ``partitions_unmarked``: days it
    missed whose table mark could not be written (see ``PurgeReport``).
    ``sources_after``·``prior_sources_deferred_runs``는 앞 실행의 ``sources_resume_after``와
    ``sources_deferred_runs``다. 원천 정리는 그 위치에서 이어 읽고, 연기가 이어진 횟수를 센다.
    """
    report = repository.purge_expired_history(
        retention_days=retention_days,
        ahead_days=ahead_days,
        prior_unmarked=tuple(prior_unmarked),
        sources_after=sources_after,
    )
    deferred_runs = sources_deferred_runs(report, prior_sources_deferred_runs)
    return {
        "status": retention_status(report, sources_deferred_in_a_row=deferred_runs),
        "retention_days": retention_days,
        "cutoff": report.cutoff.isoformat(),
        "partitions_created": list(report.partitions_created),
        "partitions_not_created": list(report.partitions_not_created),
        "partitions_dropped": list(report.partitions_dropped),
        "partitions_retained_short_forecast": list(report.partitions_retained_short_forecast),
        "partitions_deferred": list(report.partitions_deferred),
        "partitions_overdue": list(report.partitions_overdue),
        "partitions_unmarked": list(report.partitions_unmarked),
        "foreign_key_validation_deferred": report.foreign_key_validation_deferred,
        "partition_ddl_stopped": report.partition_ddl_stopped,
        "pointers_deleted": report.pointers_deleted,
        "pointers_deferred": report.pointers_deferred,
        "sources_deleted": report.sources_deleted,
        "sources_deferred": report.sources_deferred,
        "sources_deferred_runs": deferred_runs,
        "sources_scanned": report.sources_scanned,
        "sources_resume_after": report.sources_resume_after,
        "rows_outside_any_partition": report.rows_outside_any_partition,
        "default_floor": report.default_floor.isoformat() if report.default_floor else None,
        "forward_partition_days": report.forward_partition_days,
    }
