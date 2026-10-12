"""The KMA dataset split has no other regression coverage: a typo in the

asset/job/schedule wiring only shows up as a Dagster resolution error, which
nothing else in this test suite exercises.
"""

from __future__ import annotations

from kortravelweather_dagster.definitions import defs


def _schedule(name: str):
    for schedule in defs.get_repository_def().schedule_defs:
        if schedule.name == name:
            return schedule
    raise AssertionError(f"schedule not found: {name}")


def test_definitions_resolve_without_error() -> None:
    # Importing the module already runs every _resolve_job() call; asking for
    # the repository definition forces Dagster to validate the whole graph.
    assert defs.get_repository_def() is not None


def test_kma_is_split_into_one_job_per_dataset() -> None:
    job_names = {job.name for job in defs.get_repository_def().get_all_jobs()}
    assert {
        "kma_ultra_short_nowcast_job",
        "kma_ultra_short_forecast_job",
        "kma_short_forecast_job",
        "kma_mid_forecast_job",
        "kma_weather_alerts_job",
    } <= job_names
    assert "kma_weather_job" not in job_names


def test_ultra_short_datasets_stay_hourly() -> None:
    for name, job_name, cron in (
        ("hourly_kma_ultra_short_forecast", "kma_ultra_short_forecast_job", "0 * * * *"),
        ("hourly_kma_ultra_short_nowcast", "kma_ultra_short_nowcast_job", "20 * * * *"),
    ):
        schedule = _schedule(name)
        assert schedule.cron_schedule == cron
        assert schedule.job_name == job_name


def _hourly_minute(name: str) -> int:
    minute, *rest = _schedule(name).cron_schedule.split()
    assert rest == ["*", "*", "*", "*"], name
    return int(minute)


def test_station_lock_jobs_run_at_staggered_minutes() -> None:
    # 초단기예보, 초단기실황 and AirKorea publish into the same station
    # anchors under ``location:<id>`` advisory locks.  On one tick the second
    # KMA job trailed the first by seconds and lost the lock wait on the same
    # station (13 of 96 nowcast runs, 2026-10-08..12).  The minutes leave
    # each previous job's measured p95 (13.9 / 7.1 / 12.0 min) plus margin;
    # this pins the minutes, not the run durations.
    assert (
        _hourly_minute("hourly_kma_ultra_short_forecast"),
        _hourly_minute("hourly_kma_ultra_short_nowcast"),
        _hourly_minute("hourly_airkorea_weather"),
    ) == (0, 20, 35)


def test_nowcast_tick_fetches_the_published_base_the_top_of_hour_did() -> None:
    # Moving the nowcast off :00 must not make it ask for an observation KMA
    # has not served yet, nor fall a base behind.  The base choice is
    # python-kma-api's (AGENTS: do not reimplement KMA publish times).
    from datetime import datetime, timedelta, timezone

    from kma import KmaEndpoint, base_available_at
    from kma.time_utils import latest_ultra_srt_ncst_base

    kst = timezone(timedelta(hours=9))
    minute = _hourly_minute("hourly_kma_ultra_short_nowcast")
    for hour in range(24):
        top = datetime(2026, 10, 12, hour, 0, tzinfo=kst)
        tick = top.replace(minute=minute)
        base = latest_ultra_srt_ncst_base(tick)
        assert base == latest_ultra_srt_ncst_base(top), tick
        assert base_available_at(KmaEndpoint.ULTRA_SRT_NCST, *base) <= tick, tick


def test_airkorea_waits_for_the_current_hours_station_readings() -> None:
    # AirKorea posts HH:00 station readings around HH:10-HH:13 (2026-10-11..12:
    # a :10 fetch got the previous hour for about half the stations, a :13
    # fetch got the current hour for all).  There is no library constant for
    # it, so this pins the measured floor.
    assert _hourly_minute("hourly_airkorea_weather") >= 15


def test_short_forecast_follows_kmas_real_publish_hours() -> None:
    # python-kma-api's VILAGE_PUBLISH_HOURS: 02/05/08/11/14/17/20/23 KST, offset
    # past its 10-minute VILAGE_FCST_DELAY.
    schedule = _schedule("kma_short_forecast_publish_hours")
    assert schedule.cron_schedule == "15 2,5,8,11,14,17,20,23 * * *"
    assert schedule.job_name == "kma_short_forecast_job"


def test_mid_forecast_follows_kmas_publish_hours() -> None:
    # python-kma-api's MID_FCST_PUBLISH_HOURS: 06/18 KST.
    schedule = _schedule("kma_mid_forecast_publish_hours")
    assert schedule.cron_schedule == "30 6,18 * * *"
    assert schedule.job_name == "kma_mid_forecast_job"


def test_alerts_are_hourly_but_offset_from_the_other_kma_schedules() -> None:
    schedule = _schedule("hourly_kma_weather_alerts")
    assert schedule.cron_schedule == "5 * * * *"
    assert schedule.job_name == "kma_weather_alerts_job"


def _jobs():
    # ``__ASSET_JOB`` is Dagster's implicit job for ad-hoc materializations
    # from the asset graph. It takes no tags from Definitions, so such a
    # manual launch runs under the instance default unless the launcher adds
    # the tag; every scheduled run goes through one of the named jobs below.
    jobs = [
        job
        for job in defs.get_repository_def().get_all_jobs()
        if not job.name.startswith("__ASSET_JOB")
    ]
    # Guards the loops below from passing on an empty repository.
    assert len(jobs) >= 10
    return jobs


def test_every_run_carries_its_own_max_runtime() -> None:
    # A shared Dagster instance has one instance-wide max_runtime_seconds for
    # every project; weather's measured long sweeps need their own bound on
    # the run itself, not in an instance file.
    from kortravelweather_dagster.definitions import RUN_MAX_RUNTIME_TAG, _job_tags

    for job in _jobs():
        assert job.run_tags.get(RUN_MAX_RUNTIME_TAG) == _job_tags(job.name)[RUN_MAX_RUNTIME_TAG]
        assert job.run_tags.get("dagster/max_retries") == "1", job.name
        assert job.run_tags.get("kortravelcommon/project") == "weather", job.name


def test_external_jobs_keep_their_run_group_next_to_the_runtime_tag() -> None:
    from kortravelweather_dagster.definitions import (
        EXTERNAL_RUN_GROUP,
        EXTERNAL_RUN_GROUP_TAG,
        external_weather_jobs,
    )

    assert external_weather_jobs
    for job in external_weather_jobs.values():
        assert job.run_tags.get(EXTERNAL_RUN_GROUP_TAG) == EXTERNAL_RUN_GROUP, job.name


def test_schedule_runs_inherit_the_runtime_tag() -> None:
    # Job tags reach a run only through the job's run tags; a schedule that
    # set run tags of its own would have to repeat them.
    from kortravelweather_dagster.definitions import RUN_MAX_RUNTIME_TAG, _job_tags

    repository = defs.get_repository_def()
    for schedule in repository.schedule_defs:
        job = repository.get_job(schedule.job_name)
        assert job.run_tags.get(RUN_MAX_RUNTIME_TAG) == _job_tags(job.name)[RUN_MAX_RUNTIME_TAG]
        assert RUN_MAX_RUNTIME_TAG not in (schedule.tags or {}), schedule.name


def test_every_instigator_declares_its_running_state_in_code() -> None:
    # The on/off state of a schedule or sensor lives in Dagster's metadata DB
    # only when someone toggled it by hand. A fresh DB -- the shared Dagster
    # instance starts from one -- brings every instigator up in its *declared*
    # default, so anything production runs must declare RUNNING here, never
    # rely on a toggle stored somewhere else.
    from dagster import DefaultScheduleStatus, DefaultSensorStatus

    repository = defs.get_repository_def()
    schedules = list(repository.schedule_defs)
    assert len(schedules) >= 17
    for schedule in schedules:
        assert schedule.default_status == DefaultScheduleStatus.RUNNING, schedule.name
    for sensor in repository.sensor_defs:
        assert sensor.default_status == DefaultSensorStatus.RUNNING, sensor.name
