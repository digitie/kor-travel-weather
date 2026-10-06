"""An overlapping collector run whose predecessor is alive ends as a skip.

The schedules fire on the clock and a slow run can still be publishing when
its next tick comes.  ``start_sync_run`` then raises ``SyncRunAlreadyActive``
(the running row's heartbeat is inside the lease).  The live run is doing the
work, so the new one records why it did nothing and finishes SUCCESS: no
``weather_sync_runs`` row, no ``failed`` in the sync metrics, one count in
``ktw_sync_runs_overlap_skipped_total``.  A predecessor whose lease expired
raises a plain ``RuntimeError`` and still fails the run (2026-10-06 owner
decision, handoff 2c).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

from dagster import AssetExecutionContext

from kortravelweather.metrics import observe_sync_overlap_skipped
from kortravelweather.repository import SyncRunAlreadyActive

_F = TypeVar("_F", bound=Callable[..., Any])

OVERLAP_SKIP_REASON = "already_running"


def overlap_skip_result(overlap: SyncRunAlreadyActive) -> dict[str, Any]:
    """The skip's metadata, and its count in the overlap metric."""
    observe_sync_overlap_skipped(overlap.provider, overlap.dataset_key)
    return {
        "provider": overlap.provider,
        "dataset_key": overlap.dataset_key,
        "skipped": True,
        "reason": OVERLAP_SKIP_REASON,
        "active_run_id": overlap.active_run_id,
        "active_heartbeat_at": (
            overlap.heartbeat_at.isoformat() if overlap.heartbeat_at else None
        ),
    }


def log_overlap_skip(context: AssetExecutionContext, result: dict[str, Any]) -> None:
    context.log.info(
        f"{result['provider']}/{result['dataset_key']}: 이전 실행 {result['active_run_id']}"
        f"(heartbeat {result['active_heartbeat_at']})이 아직 진행 중이라 이번 실행은 건너뜀"
    )


def skips_live_overlap(compute: _F) -> _F:
    """Turn ``SyncRunAlreadyActive`` from a collector asset into a recorded skip.

    Applied to every collector asset at the asset boundary, so a new
    collector cannot forget it (``tests/test_overlap_skip.py`` checks).
    """

    @functools.wraps(compute)
    def wrapper(context: AssetExecutionContext, *args: Any, **kwargs: Any) -> Any:
        try:
            return compute(context, *args, **kwargs)
        except SyncRunAlreadyActive as overlap:
            result = overlap_skip_result(overlap)
            log_overlap_skip(context, result)
            context.add_output_metadata(result)
            return result

    wrapper.skips_live_overlap = True  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]
