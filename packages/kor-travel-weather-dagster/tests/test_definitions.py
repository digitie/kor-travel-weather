"""The KMA dataset split has no other regression coverage: a typo in the

asset/job/schedule wiring only shows up as a Dagster resolution error, which
nothing else in this test suite exercises.
"""

from __future__ import annotations

import pytest
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


@pytest.mark.parametrize("dataset", ["mid", "alerts"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("projection_fails", [False, True])
def test_export_flag_on_preserves_actual_mid_and_alert_asset_collection(
    monkeypatch, tmp_path, dataset, enabled, projection_fails,
):
    from types import SimpleNamespace

    from dagster import build_asset_context
    from kortravelweather_dagster import definitions
    from kortravelweather_dagster.kma_weather import WeatherTarget
    from test_kma_weather import FakeClient, FakeRepository, _EntersAndCloses, _target

    from kortravelweather.settings import WeatherSettings

    calls = []
    issues = []

    class DataClient(_EntersAndCloses):
        async def mid_land_forecast(self, *, reg_id, tm_fc=None):
            calls.append("land")
            issues.append(tm_fc)
            return [{"tmFc": tm_fc or "202601010600", "regId": reg_id, "wf3Am": "맑음"}]

        async def mid_temperature_forecast(self, *, reg_id, tm_fc=None):
            calls.append("temperature")
            issues.append(tm_fc)
            return [{"tmFc": tm_fc or "202601010600", "regId": reg_id,
                     "taMin3": "-1", "taMax3": "4"}]

        async def weather_warning_list(self, **kwargs):
            calls.append("alerts")
            return [{"stnId": "108", "tmFc": "202601010600", "tmSeq": "1", "title": "호우주의보"}]

    repo, client, data_client = FakeRepository(), FakeClient(), DataClient()
    projection_calls = []

    def refresh_regions():
        projection_calls.append(True)
        if projection_fails:
            raise RuntimeError("fixture projection failure")
        return True

    repo.refresh_region_locations = refresh_regions
    target = WeatherTarget(
        _target().location, mid_land_region_code="11B00000", mid_temperature_region_code="11B10101",
    )
    monkeypatch.setattr(definitions, "WeatherSettings", lambda: WeatherSettings(
        environment="development", service_exports_enabled=enabled,
        service_export_artifact_root=tmp_path,
    ))
    monkeypatch.setattr(definitions, "_run_repository", lambda context: repo)
    monkeypatch.setattr(definitions, "_kma_grid_targets", lambda *args, **kwargs: (
        [target], {}, set(), None,
    ))
    monkeypatch.setattr(definitions, "_kma_alert_targets", lambda *args: [target])
    resource = SimpleNamespace(
        create_client=lambda **kwargs: client, create_data_client=lambda **kwargs: data_client,
    )
    asset = getattr(
        definitions, "kma_mid_forecast_sync" if dataset == "mid" else "kma_weather_alerts_sync"
    )
    with build_asset_context(resources={
        "kma_client": resource, "weather_repository": repo,
    }) as context:
        result = asset(context)
    assert result["status"] == "success" and repo.values
    assert repo.runs[0].status == "success"
    assert len(projection_calls) == (1 if dataset == "mid" else 0)
    if dataset == "mid":
        assert result["region_locations_updated"] is (not projection_fails)
    assert calls == (["land", "temperature"] if dataset == "mid" else ["alerts"])
    if dataset == "mid":
        assert len(issues) == 2 and issues[0] == issues[1]
        assert (issues[0] is not None) is enabled


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
        EXTERNAL_COLLECTIONS,
        EXTERNAL_RUN_GROUP,
        EXTERNAL_RUN_GROUP_TAG,
        EXTERNAL_SLOW_RUN_GROUP,
        external_weather_jobs,
    )

    # 같은 태그 키에 두 값. slow 소속은 코드가 명시하며 아래 목록이 그 정본의 사본이다.
    # 분리한 provider(W-EXP-SPLIT-01)는 current·forecast 두 part 모두 slow다: sweep은 분리 전처럼
    # 한 번에 하나만 돌고, 같은 provider의 두 run이 겹쳐 분당 호출이 두 배가 되지 않는다.
    slow = {"open_meteo", "openweathermap", "weatherapi"}
    assert set(external_weather_jobs) == {collection.slug for collection in EXTERNAL_COLLECTIONS}
    expected = {}
    for collection in EXTERNAL_COLLECTIONS:
        if collection.provider_key in slow:
            assert collection.part in {"current", "forecast"}, collection
            expected[collection.slug] = EXTERNAL_SLOW_RUN_GROUP
        else:
            assert collection.part is None, collection
            expected[collection.slug] = EXTERNAL_RUN_GROUP
    for slug, job in external_weather_jobs.items():
        assert job.run_tags.get(EXTERNAL_RUN_GROUP_TAG) == expected[slug], job.name
    assert {slug for slug, group in expected.items() if group == EXTERNAL_SLOW_RUN_GROUP} == {
        f"{key}_{part}" for key in slow for part in ("current", "forecast")
    }
    # fast 쪽이 비면 분리가 아무 run도 앞당기지 못한다.
    assert {slug for slug, group in expected.items() if group == EXTERNAL_RUN_GROUP}


def test_split_providers_partition_their_catalog_datasets_by_part() -> None:
    # W-EXP-SPLIT-01: current와 forecast가 provider dataset을 빠짐·겹침 없이 나눈다.
    # 하루 호출 수는 분리 전과 같다(각 dataset이 같은 3시간 주기로 한 번).
    from kortravelweather_dagster.definitions import (
        EXTERNAL_COLLECTIONS,
        EXTERNAL_PROVIDER_KEYS,
        SPLIT_EXTERNAL_PROVIDER_KEYS,
    )

    from kortravelweather.providers.catalog import provider_spec

    by_provider: dict[str, list] = {}
    for collection in EXTERNAL_COLLECTIONS:
        by_provider.setdefault(collection.provider_key, []).append(collection)
    assert set(by_provider) == set(EXTERNAL_PROVIDER_KEYS)
    for key, collections in by_provider.items():
        spec = provider_spec(key)
        datasets = [d for c in collections for d in c.dataset_keys()]
        assert sorted(datasets) == sorted(d.key for d in spec.datasets), key
        if key in SPLIT_EXTERNAL_PROVIDER_KEYS:
            parts = {c.part: c for c in collections}
            assert set(parts) == {"current", "forecast"}, key
            assert all(
                not d.forecast for d in spec.datasets if d.key in parts["current"].dataset_keys()
            )
            current, forecast = parts["current"], parts["forecast"]
            assert all(d.forecast for d in spec.datasets if d.key in forecast.dataset_keys())
            assert current.dataset_keys() and forecast.dataset_keys(), key
        else:
            assert [c.part for c in collections] == [None], key


def test_split_jobs_report_only_their_own_dataset_status_scopes() -> None:
    # /status의 next_scheduled_at은 schedule이 기록한다. current job이 forecast의 다음
    # 예정을 기록하면 소비자는 forecast가 곧 갱신된다고 오해한다.
    from kortravelweather_dagster import definitions

    from kortravelweather.settings import WeatherSettings

    settings = WeatherSettings(
        environment="development", service_exports_enabled=True,
        enabled_providers=["open_meteo", "wttr_in"],
    )

    def scopes(job_name):
        return {
            (key.dataset_key, key.forecast_style)
            for key in definitions.schedule_status_sources(job_name, settings)
        }

    assert scopes("open_meteo_current_weather_job") == {("open_meteo_current", "observed")}
    assert scopes("open_meteo_forecast_weather_job") == {("open_meteo_forecast", "external")}
    assert scopes("wttr_in_weather_job") == {
        ("wttr_in_current", "observed"), ("wttr_in_forecast", "external"),
    }
    # 켜지지 않은 provider와 export 꺼짐은 기록하지 않는다.
    assert scopes("weatherapi_current_weather_job") == set()
    off = WeatherSettings(environment="development", service_exports_enabled=False)
    assert definitions.schedule_status_sources("open_meteo_current_weather_job", off) == ()
    # 분리 전 이름은 남기지 않는다(두 job이 같은 dataset을 받으면 호출이 두 배가 된다).
    job_names = {job.name for job in defs.get_repository_def().get_all_jobs()}
    for key in definitions.SPLIT_EXTERNAL_PROVIDER_KEYS:
        assert f"{key}_weather_job" not in job_names
        assert {f"{key}_current_weather_job", f"{key}_forecast_weather_job"} <= job_names


def test_split_forecast_starts_after_its_current_inside_the_cycle() -> None:
    from kortravelweather_dagster.definitions import (
        EXTERNAL_COLLECTIONS,
        SPLIT_FORECAST_PHASE_OFFSET_MINUTES,
    )

    phases = {c.slug: c.phase_minutes for c in EXTERNAL_COLLECTIONS}
    for c in EXTERNAL_COLLECTIONS:
        if c.part == "forecast":
            current = phases[c.slug.removesuffix("_forecast") + "_current"]
            assert c.phase_minutes - current == SPLIT_FORECAST_PHASE_OFFSET_MINUTES >= 60
        assert 0 <= c.phase_minutes < 180, c
    assert len(set(phases.values())) == len(phases)


def test_kma_short_forecast_runtime_covers_its_publish_interval() -> None:
    # 단기예보는 3시간마다 발표한다. 포화된 odroid에서 한 회차가 2시간을 넘겨 7200초 상한에
    # 거의 매번 회수됐다(2026-10-27~11-02). 상한은 발표 주기와 같다.
    job = defs.get_repository_def().get_job("kma_short_forecast_job")
    assert job.run_tags["dagster/max_runtime"] == "10800"
    assert defs.get_repository_def().get_job("kma_ultra_short_forecast_job").run_tags[
        "dagster/max_runtime"
    ] == "7200"


def test_every_location_capped_provider_runs_in_the_slow_group() -> None:
    # 기본 location 상한이 있는 provider는 기본 설정에서 실제로 sweep하는 provider다.
    # 상한을 새로 주면서 slow 집합을 빠뜨리면 fast 그룹에서 sweep 둘이 겹친다.
    from kortravelweather_dagster.definitions import SLOW_EXTERNAL_PROVIDER_KEYS

    from kortravelweather.providers.catalog import provider_spec
    from kortravelweather.settings import WeatherSettings

    caps = WeatherSettings.model_fields["provider_location_caps"].default_factory()
    assert caps
    assert set(caps) <= SLOW_EXTERNAL_PROVIDER_KEYS
    # 주석의 요청 수(상한 × dataset 2개)가 전제하는 dataset 수.
    for key in SLOW_EXTERNAL_PROVIDER_KEYS:
        assert len(provider_spec(key).datasets) == 2, key


def test_deploy_instance_limits_both_external_run_groups() -> None:
    from pathlib import Path

    import yaml
    from kortravelweather_dagster.definitions import (
        EXTERNAL_RUN_GROUP,
        EXTERNAL_RUN_GROUP_TAG,
        EXTERNAL_SLOW_RUN_GROUP,
    )

    path = Path(__file__).resolve().parents[3] / "deploy/dagster.yaml"
    coordinator = yaml.safe_load(path.read_text(encoding="utf-8"))["run_coordinator"]
    limits = {
        entry["value"]: entry["limit"]
        for entry in coordinator["config"]["tag_concurrency_limits"]
        if entry["key"] == EXTERNAL_RUN_GROUP_TAG
    }
    assert limits[EXTERNAL_SLOW_RUN_GROUP] == 1
    assert EXTERNAL_RUN_GROUP in limits


def test_schedule_runs_inherit_the_runtime_tag() -> None:
    # Job tags reach a run only through the job's run tags; a schedule that
    # set run tags of its own would have to repeat them.
    from kortravelweather_dagster.definitions import RUN_MAX_RUNTIME_TAG, _job_tags

    repository = defs.get_repository_def()
    for schedule in repository.schedule_defs:
        job = repository.get_job(schedule.job_name)
        assert job.run_tags.get(RUN_MAX_RUNTIME_TAG) == _job_tags(job.name)[RUN_MAX_RUNTIME_TAG]
        assert RUN_MAX_RUNTIME_TAG not in (schedule.tags or {}), schedule.name


def test_every_instigator_declares_its_expected_default_state_in_code() -> None:
    # 새 metadata DB에서도 기존 수집·복구는 RUNNING이다. 새 export publisher만
    # 파일 저장소와 source 준비 확인 후 명시 start하도록 STOPPED로 등록한다.
    from dagster import DefaultScheduleStatus, DefaultSensorStatus

    repository = defs.get_repository_def()
    schedules = list(repository.schedule_defs)
    assert len(schedules) >= 17
    for schedule in schedules:
        assert schedule.default_status == DefaultScheduleStatus.RUNNING, schedule.name
    for sensor in repository.sensor_defs:
        expected = (
            DefaultSensorStatus.STOPPED
            if sensor.name == "weather_export_publisher"
            else DefaultSensorStatus.RUNNING
        )
        assert sensor.default_status == expected, sensor.name


def test_external_schedules_keep_volume_and_three_hour_freshness_across_month_boundary() -> None:
    """실제 Dagster 달력에서 같은 분 시작 집중과 주기·횟수 감소를 검출한다."""
    from collections import Counter
    from datetime import datetime, timedelta
    from itertools import pairwise
    from zoneinfo import ZoneInfo

    from dagster._utils.schedules import schedule_execution_time_iterator
    from kortravelweather_dagster.definitions import external_weather_schedules

    start = datetime(2026, 10, 31, tzinfo=ZoneInfo("Asia/Seoul"))
    end = start + timedelta(days=2)
    launches = []
    # 9 provider + 분리한 3 provider의 forecast schedule(W-EXP-SPLIT-01).
    assert len(external_weather_schedules) == 12
    for schedule in external_weather_schedules:
        times = []
        for tick in schedule_execution_time_iterator(
            start.timestamp(), schedule.cron_schedule, execution_timezone="Asia/Seoul"
        ):
            if tick >= end:
                break
            times.append(tick)
        assert len(times) == 16, schedule.name
        assert all(b - a == timedelta(hours=3) for a, b in pairwise(times))
        assert Counter(tick.date() for tick in times) == {
            start.date(): 8,
            end.date() - timedelta(days=1): 8,
        }
        launches.extend(times)
    assert max(Counter(launches).values()) == 1
    ordered = sorted(launches)
    # 기존 provider phase는 20분 간격 그대로이고 분리한 forecast는 그 사이 10분 자리다.
    assert all(b - a >= timedelta(minutes=10) for a, b in pairwise(ordered))


def test_short_forecast_retention_has_its_own_bounded_schedule() -> None:
    from dagster import DefaultScheduleStatus

    schedule = _schedule("short_forecast_retention_every_ten_minutes")
    assert schedule.cron_schedule == "*/10 * * * *"
    assert schedule.job_name == "weather_short_forecast_retention_job"
    assert schedule.execution_timezone == "Asia/Seoul"
    assert schedule.default_status == DefaultScheduleStatus.RUNNING
    job = defs.get_repository_def().get_job(schedule.job_name)
    assert job.tags["dagster/max_runtime"] == "300"

