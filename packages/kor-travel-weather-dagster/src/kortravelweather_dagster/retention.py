"""보존 기간이 지난 fact/source 이력을 매일 정리한다."""

from __future__ import annotations

from typing import Any, Protocol

from kortravelweather.models import PurgeReport


class _PurgeableRepository(Protocol):
    def purge_expired_history(
        self, *, retention_days: int, ahead_days: int = ...
    ) -> PurgeReport: ...


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
        "retention_days": retention_days,
        "cutoff": report.cutoff.isoformat(),
        "partitions_dropped": list(report.partitions_dropped),
        "pointers_deleted": report.pointers_deleted,
        "sources_deleted": report.sources_deleted,
        "rows_outside_any_partition": report.rows_outside_any_partition,
    }
