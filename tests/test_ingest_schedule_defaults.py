"""Pin which ingest schedules start on their own.

Why this exists: `default_status` is easy to flip and nothing else in the tree
records the decision.  A schedule that quietly starts itself again after a
deploy is indistinguishable from one that was never turned off, and the run
queue is the only place it shows up -- by which point it has already fetched.

The KMA and AirKorea ingests were stopped on 2026-09-09.  If they are ever
turned back on, this test is the place that has to change with them, which is
the point: the change becomes visible in review instead of only in the queue.
"""

from __future__ import annotations

from dagster import DefaultScheduleStatus

from kortravelweather_dagster.definitions import defs

#: Schedules that must not start on their own.  Names, not prefixes -- a prefix
#: would silently cover a schedule added later that nobody decided about.
STOPPED_BY_DEFAULT = frozenset(
    {
        "hourly_kma_weather",
        "hourly_airkorea_weather",
    }
)


def _schedules_by_name() -> dict[str, DefaultScheduleStatus]:
    return {
        schedule.name: schedule.default_status
        for schedule in defs.schedules or ()
    }


def test_stopped_schedules_are_real_names() -> None:
    known = set(_schedules_by_name())
    unknown = sorted(STOPPED_BY_DEFAULT - known)
    assert not unknown, (
        f"listed as stopped but no such schedule: {', '.join(unknown)} -- "
        "a typo here reads as 'already handled' while the ingest keeps running"
    )


def test_stopped_schedules_do_not_start_themselves() -> None:
    running = sorted(
        name
        for name, status in _schedules_by_name().items()
        if name in STOPPED_BY_DEFAULT and status is DefaultScheduleStatus.RUNNING
    )
    assert not running, f"expected stopped by default: {', '.join(running)}"


def test_every_other_schedule_is_accounted_for() -> None:
    """A new schedule should not slip in as RUNNING without anyone deciding."""
    expected_running = {
        "hourly_external_weather",
        "twice_daily_regional_weather",
        "daily_weather_retention",
    }
    actual_running = {
        name
        for name, status in _schedules_by_name().items()
        if status is DefaultScheduleStatus.RUNNING
    }
    assert actual_running == expected_running, (
        f"self-starting schedules changed: {sorted(actual_running)} -- "
        "add it here on purpose, or give it DefaultScheduleStatus.STOPPED"
    )
