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
    for name, job_name in (
        ("hourly_kma_ultra_short_nowcast", "kma_ultra_short_nowcast_job"),
        ("hourly_kma_ultra_short_forecast", "kma_ultra_short_forecast_job"),
    ):
        schedule = _schedule(name)
        assert schedule.cron_schedule == "0 * * * *"
        assert schedule.job_name == job_name


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
