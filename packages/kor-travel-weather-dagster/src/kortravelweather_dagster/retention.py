"""보존 기간이 지난 fact/source 이력을 매일 정리한다."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from kortravelweather.models import PurgeReport

#: Same threshold as the KorTravelWeatherForwardPartitionsLow alert.
FORWARD_PARTITION_ALERT_DAYS = 3

RetentionStatus = Literal["ok", "deferred", "partial", "failed"]


class _PurgeableRepository(Protocol):
    def purge_expired_history(
        self, *, retention_days: int, ahead_days: int = ...
    ) -> PurgeReport: ...


def retention_status(report: PurgeReport) -> RetentionStatus:
    """How the run ends, given what it could not do.

    Ingestion never pauses, so a partition step that loses its lock race is
    deferred to the next night and that alone is ``deferred`` -- a success
    with the names in the metadata.  The run is ``partial`` only when work
    has now been missed on consecutive nights -- an overdue expired day, or a
    forward window down to the alert threshold -- and ``failed`` when, on top
    of that, this run moved nothing in that direction.
    """
    forward_short = bool(report.partitions_not_created) and (
        report.forward_partition_days is None
        or report.forward_partition_days <= FORWARD_PARTITION_ALERT_DAYS
    )
    if (report.partitions_overdue and not report.partitions_dropped) or (
        forward_short and not report.partitions_created
    ):
        return "failed"
    if report.partitions_overdue or forward_short:
        return "partial"
    if (
        report.partitions_deferred
        or report.partitions_not_created
        or report.foreign_key_validation_deferred
        or report.partition_ddl_stopped
    ):
        return "deferred"
    return "ok"


def run_weather_retention_purge(
    *,
    repository: _PurgeableRepository,
    retention_days: int,
    ahead_days: int = 7,
) -> dict[str, Any]:
    """Drop the days past the window and describe what went.

    ``rows_outside_any_partition`` is the field worth watching.  Everything else
    says what retention did; that one says whether retention can still reach the
    data at all -- rows in the DEFAULT partition are never dropped, so a
    non-zero value is the table quietly starting to grow again.
    """
    report = repository.purge_expired_history(
        retention_days=retention_days, ahead_days=ahead_days
    )
    return {
        "status": retention_status(report),
        "retention_days": retention_days,
        "cutoff": report.cutoff.isoformat(),
        "partitions_created": list(report.partitions_created),
        "partitions_not_created": list(report.partitions_not_created),
        "partitions_dropped": list(report.partitions_dropped),
        "partitions_deferred": list(report.partitions_deferred),
        "partitions_overdue": list(report.partitions_overdue),
        "foreign_key_validation_deferred": report.foreign_key_validation_deferred,
        "partition_ddl_stopped": report.partition_ddl_stopped,
        "pointers_deleted": report.pointers_deleted,
        "sources_deleted": report.sources_deleted,
        "rows_outside_any_partition": report.rows_outside_any_partition,
        "default_floor": report.default_floor.isoformat() if report.default_floor else None,
        "forward_partition_days": report.forward_partition_days,
    }
