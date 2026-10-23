"""실제 tick metadata가 기존 예약 실행/중복 억제에 영향을 주지 않는다."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from dagster import (
    DagsterInstance,
    DagsterRunStatus,
    DefaultScheduleStatus,
    build_schedule_context,
    job,
    op,
)
from kortravelweather_dagster import export_schedule_status as module

from kortravelweather.export_status import SourceKey

NOW = datetime(2026, 10, 23, 11, 20, tzinfo=UTC)
KEY = SourceKey("python-kma-api", "kma_ultra_short_nowcast", "nowcast")


@op(config_schema={"value": int})
def scheduled_op():
    pass


@job
def scheduled_job():
    scheduled_op()


@pytest.fixture
def projection(monkeypatch):
    calls = []

    class Engine:
        disposed = False

        @contextmanager
        def begin(self):
            yield object()

        def dispose(self):
            self.disposed = True

    engine = Engine()
    factory_calls = []

    def factory(context):
        factory_calls.append(context)
        return SimpleNamespace(engine=engine)

    class Statuses:
        def __init__(self, metadata):
            pass

        def record_schedule(self, connection, key, **kwargs):
            calls.append((key, kwargs))

    @contextmanager
    def transaction(connection):
        yield

    monkeypatch.setattr(module, "ExportSourceStatuses", Statuses)
    monkeypatch.setattr(module, "status_transaction", transaction)
    return SimpleNamespace(factory=factory, engine=engine, calls=calls, factory_calls=factory_calls)


def schedule(projection, **kwargs):
    options = {
        "project": "weather",
        "location_name": "weather-code",
        "source_keys": (KEY,),
        "repository_factory": projection.factory,
        "name": "weather_nowcast",
        "cron_schedule": "20 * * * *",
        "execution_timezone": "Asia/Seoul",
        "job": scheduled_job,
        "default_status": DefaultScheduleStatus.RUNNING,
    }
    options.update(kwargs)
    return module.schedule_with_export_status(**options)


def test_definition_load_has_no_repository_or_status_side_effect(projection):
    value = schedule(projection)
    assert value.name == "weather_nowcast"
    assert value.cron_schedule == "20 * * * *"
    assert value.execution_timezone == "Asia/Seoul"
    assert value.default_status == DefaultScheduleStatus.RUNNING
    assert projection.factory_calls == projection.calls == []
    assert not projection.engine.disposed


def test_public_evaluation_preserves_run_request_config_tags_and_callbacks(projection):
    callbacks = []

    def eligible(context):
        callbacks.append("eligible")
        return True

    def config(context):
        callbacks.append("config")
        return {"ops": {"scheduled_op": {"config": {"value": 7}}}}

    def tags(context):
        callbacks.append("tags")
        return {"custom": "preserved"}

    value = schedule(projection, should_execute=eligible, run_config_fn=config, tags_fn=tags)
    with (
        DagsterInstance.local_temp() as instance,
        build_schedule_context(instance=instance, scheduled_execution_time=NOW) as context,
    ):
        result = value.evaluate_tick(context)
    assert result.skip_message is None and len(result.run_requests) == 1
    request = result.run_requests[0]
    assert request.run_config == {"ops": {"scheduled_op": {"config": {"value": 7}}}}
    assert request.tags["custom"] == "preserved"
    assert request.tags["dagster/schedule_name"] == "weather_nowcast"
    assert callbacks == ["eligible", "config", "tags"]
    assert len(projection.calls) == 1 and projection.engine.disposed
    key, clock = projection.calls[0]
    assert key == KEY and clock["next_scheduled_at"] > clock["evaluated_at"]
    assert clock["next_scheduled_at"].minute == 20


def test_active_run_is_still_coalesced_and_also_records_next_tick(projection):
    eligible_calls = []
    value = schedule(
        projection,
        should_execute=lambda context: eligible_calls.append(context) or True,
        run_config={"ops": {"scheduled_op": {"config": {"value": 3}}}},
    )
    with DagsterInstance.local_temp() as instance:
        instance.create_run_for_job(
            scheduled_job,
            run_config={"ops": {"scheduled_op": {"config": {"value": 3}}}},
            status=DagsterRunStatus.STARTED,
            tags={"kortravelcommon/project": "weather"},
        )
        with build_schedule_context(instance=instance, scheduled_execution_time=NOW) as context:
            result = value.evaluate_tick(context)
    assert result.run_requests == [] and result.skip_message
    assert eligible_calls == []
    assert len(projection.calls) == 1
    assert projection.calls[0][1]["next_scheduled_at"] is not None


@pytest.mark.parametrize("running", [False, None])
def test_stopped_or_unknown_status_records_null_without_inventing_next_tick(projection, running):
    value = schedule(
        projection,
        schedule_running=lambda context: running,
        run_config={"ops": {"scheduled_op": {"config": {"value": 3}}}},
    )
    with (
        DagsterInstance.local_temp() as instance,
        build_schedule_context(instance=instance, scheduled_execution_time=NOW) as context,
    ):
        assert len(value.evaluate_tick(context).run_requests) == 1
    assert projection.calls[0][1]["next_scheduled_at"] is None


def test_manual_evaluation_without_tick_has_unknown_next_time(projection):
    value = schedule(projection, run_config={"ops": {"scheduled_op": {"config": {"value": 3}}}})
    with (
        DagsterInstance.local_temp() as instance,
        build_schedule_context(instance=instance) as context,
    ):
        assert len(value.evaluate_tick(context).run_requests) == 1
    assert projection.calls[0][1]["next_scheduled_at"] is None


def test_status_failure_does_not_break_run_or_log_sensitive_exception(
    projection, monkeypatch, capsys
):
    @contextmanager
    def unavailable(connection):
        raise RuntimeError("private-database-password")
        yield

    monkeypatch.setattr(module, "status_transaction", unavailable)
    value = schedule(
        projection,
        tags={"static": "preserved"},
        run_config={"ops": {"scheduled_op": {"config": {"value": 3}}}},
    )
    with (
        DagsterInstance.local_temp() as instance,
        build_schedule_context(instance=instance, scheduled_execution_time=NOW) as context,
    ):
        result = value.evaluate_tick(context)
    assert len(result.run_requests) == 1
    assert result.run_requests[0].tags["static"] == "preserved"
    assert projection.engine.disposed
    logs = capsys.readouterr().err
    assert "weather_export_schedule_status_deferred" in logs
    assert "private-database-password" not in logs


def test_original_evaluation_failure_is_not_hidden_or_replaced_by_status_write(projection):
    def fail(context):
        raise RuntimeError("original-evaluation-failed")

    value = schedule(projection, should_execute=fail)
    with (
        DagsterInstance.local_temp() as instance,
        build_schedule_context(instance=instance, scheduled_execution_time=NOW) as context,
        pytest.raises(Exception, match="should_execute"),
    ):
        value.evaluate_tick(context)
    assert projection.factory_calls == []


def test_dynamic_scope_selection_and_empty_scope_avoid_extra_repository(projection):
    value = schedule(projection, source_keys=lambda context: ())
    with (
        DagsterInstance.local_temp() as instance,
        build_schedule_context(instance=instance, scheduled_execution_time=NOW) as context,
    ):
        value.evaluate_tick(context)
    assert projection.factory_calls == projection.calls == []


def test_multiple_scopes_stop_after_metadata_budget_without_losing_run(projection, monkeypatch):
    other = SourceKey(KEY.provider, "another_dataset", KEY.forecast_style)
    moments = iter((0, 0, 6))
    monkeypatch.setattr(module, "monotonic", lambda: next(moments))
    value = schedule(projection, source_keys=lambda context: (KEY, other))
    with (
        DagsterInstance.local_temp() as instance,
        build_schedule_context(instance=instance, scheduled_execution_time=NOW) as context,
    ):
        result = value.evaluate_tick(context)
    assert len(result.run_requests) == 1
    assert len(projection.calls) == 1 and projection.calls[0][0] == KEY
    assert projection.engine.disposed


@pytest.mark.parametrize(
    "cron,zone,expected",
    [
        ("20 * * * *", "Asia/Seoul", NOW + timedelta(hours=1)),
        ("15 2,5,8,11,14,17,20,23 * * *", "Asia/Seoul", NOW.replace(hour=14, minute=15)),
        (("0 0 * * *", "*/30 * * * *"), "UTC", NOW.replace(minute=30)),
        ("0 12 * * *", None, NOW.replace(hour=12, minute=0)),
    ],
)
def test_next_time_uses_registered_cron_timezone_and_union(cron, zone, expected):
    assert module.next_schedule_time(cron, zone, after=NOW) == expected


def test_next_time_rejects_naive_clock_and_unbounded_cron_list():
    with pytest.raises(ValueError):
        module.next_schedule_time("* * * * *", "UTC", after=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        module.next_schedule_time(["* * * * *"] * 33, "UTC", after=NOW)
