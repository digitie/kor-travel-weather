"""보존 기간이 지난 fact/source 이력을 매일 정리한다."""

from __future__ import annotations

from typing import Any, Protocol

from kortravelweather.models import PurgeReport


class _PurgeableRepository(Protocol):
    def purge_expired_history(
        self, *, retention_days: int, batch_rows: int = ..., max_batches: int = ...
    ) -> PurgeReport: ...


def run_weather_retention_purge(
    *,
    repository: _PurgeableRepository,
    retention_days: int,
    max_batches: int,
) -> dict[str, Any]:
    """Delete history past the window and describe what went.

    The interesting field is ``truncated``.  The deleted counts alone cannot
    distinguish "nothing was due" from "the run hit its cap and gave up with a
    backlog", and those two need very different responses -- the second means
    the window is shorter than the ingest rate and the table will keep growing
    no matter how often this runs.
    """
    report = repository.purge_expired_history(
        retention_days=retention_days, max_batches=max_batches
    )
    return {
        "retention_days": retention_days,
        "cutoff": report.cutoff.isoformat(),
        "values_deleted": report.values_deleted,
        "sources_deleted": report.sources_deleted,
        "run_sources_deleted": report.run_sources_deleted,
        "truncated": report.truncated,
    }
