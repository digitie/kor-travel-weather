"""export 사전 구성 job(호출 검토 A3)의 등록·실행 조건·결과 분류."""

import pytest
from dagster import DefaultScheduleStatus, Failure, build_op_context
from kortravelweather_dagster import export_maintenance, export_prebuild
from kortravelweather_dagster.definitions import defs


class _Engine:
    disposed = False

    def dispose(self):
        self.disposed = True


class _Repository:
    def __init__(self):
        self.engine = _Engine()


class _Resource:
    def __init__(self):
        self.repository = _Repository()

    def create_repository(self):
        return self.repository


@pytest.fixture
def enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_SERVICE_EXPORTS_ENABLED", "true")
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_SERVICE_EXPORT_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_SERVICE_EXPORT_PREBUILD_ENABLED", "true")


def test_job_runs_in_process_two_minutes_before_map_current():
    repository = defs.get_repository_def()
    schedule = repository.get_schedule_def(export_prebuild.SCHEDULE_NAME)
    # Map weather_state_current는 :25·:40이다. sensor에 넣지 않은 별도 유한 job이다.
    assert schedule.cron_schedule == "23,38 * * * *"
    assert schedule.execution_timezone == "Asia/Seoul"
    assert schedule.default_status == DefaultScheduleStatus.RUNNING
    job = repository.get_job(export_prebuild.JOB_NAME)
    assert job.tags["dagster/max_runtime"] == "900"
    assert job.executor_def.name == "in_process"


def test_schedule_waits_for_exports_and_the_publisher(monkeypatch, enabled):
    class Instance:
        def __init__(self, states):
            self.states = states

        def all_instigator_state(self):
            return self.states

    class State:
        instigator_name = export_maintenance.PUBLISHER_SENSOR_NAME

        class instigator_type:
            value = "SENSOR"

        class status:
            value = "RUNNING"

        class repository_selector:
            location_name = export_prebuild.LOCATION_NAME

    class Context:
        def __init__(self, states):
            self.instance = Instance(states)

    assert export_prebuild._should_execute(Context([State()]))
    assert not export_prebuild._should_execute(Context([]))
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_SERVICE_EXPORT_PREBUILD_ENABLED", "false")
    assert not export_prebuild._should_execute(Context([State()]))


def test_op_reports_built_and_fails_on_an_unfinished_build(monkeypatch, enabled):
    resource = _Resource()
    outcomes = iter([{"outcome": "built", "pages": 14}, {"outcome": "over_budget", "pages": 3}])
    monkeypatch.setattr(export_prebuild, "build_current_profile",
                        lambda repository, settings: next(outcomes))
    context = build_op_context(resources={"weather_repository": resource})
    assert export_prebuild.weather_export_prebuild(context)["outcome"] == "built"
    assert resource.repository.engine.disposed
    with pytest.raises(Failure, match="over_budget"):
        export_prebuild.weather_export_prebuild(
            build_op_context(resources={"weather_repository": _Resource()}),
        )


def test_op_hides_the_raw_error_and_skips_when_disabled(monkeypatch, enabled):
    def broken(repository, settings):
        raise RuntimeError("postgresql://secret@db/weather")

    monkeypatch.setattr(export_prebuild, "build_current_profile", broken)
    with pytest.raises(Failure) as failure:
        export_prebuild.weather_export_prebuild(
            build_op_context(resources={"weather_repository": _Resource()}),
        )
    assert "secret" not in str(failure.value.description)
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_SERVICE_EXPORTS_ENABLED", "false")
    assert export_prebuild.weather_export_prebuild(
        build_op_context(resources={"weather_repository": _Resource()}),
    ) == {"outcome": "disabled"}
