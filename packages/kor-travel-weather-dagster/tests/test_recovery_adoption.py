"""외부 DB/공급자 없이 실제 Dagster 정의·재시도 소유권을 검증한다."""

from types import SimpleNamespace

import pytest
from dagster import (
    DagsterEvent,
    DagsterEventType,
    DagsterInstance,
    DagsterRun,
    DagsterRunStatus,
    DefaultSensorStatus,
    Definitions,
    build_sensor_context,
)
from dagster._core.events import JobFailureData, RunFailureReason
from dagster._core.execution.retries import auto_reexecution_should_retry_run
from kortravelcommon.dagster import INFRA_RETRY_PARENT_TAG, RecoveryPolicy
from kortravelweather_dagster import definitions as definitions
from kortravelweather_dagster.recovery import (
    INFRA_RETRY_ENABLED_ENV,
    LOCATION_NAME,
    NATIVE_ORIGIN_ENV,
    optional_recovery_sensors,
)


@pytest.fixture
def mapping(monkeypatch):
    monkeypatch.delenv(INFRA_RETRY_ENABLED_ENV, raising=False)
    monkeypatch.delenv(NATIVE_ORIGIN_ENV, raising=False)
    return {
        job.name: (job, definitions._job_policy(job.name)) for job in definitions._COLLECTION_JOBS
    }


def test_single_asset_jobs_in_process_and_regional_and_implicit_bounded():
    jobs = definitions._COLLECTION_JOBS
    assert len(jobs) == 18
    for job in jobs:
        if job.name == "regional_weather_job":
            assert len(job.graph.node_names()) == 4
            bounded = job.executor_def.apply_config_mapping({"config": {}})
            assert bounded.success and bounded.value["config"]["max_concurrent"] == 1
        else:
            assert len(job.graph.node_names()) == 1
            assert job.executor_def.name == "in_process"
    implicit = definitions.defs.resolve_implicit_global_asset_job_def()
    bounded = implicit.executor_def.apply_config_mapping({"config": {}})
    assert bounded.success and bounded.value["config"]["max_concurrent"] == 1


def test_defaults_keep_only_existing_app_reconciliation(mapping):
    assert optional_recovery_sensors(mapping) == []
    assert [sensor.name for sensor in definitions.defs.sensors] == ["weather_sync_recovery"]


@pytest.mark.parametrize("bad", ["TRUE", "1", " true", "true ", "", "yes"])
def test_retry_opt_in_rejects_ambiguous_values(mapping, monkeypatch, bad):
    monkeypatch.setenv(INFRA_RETRY_ENABLED_ENV, bad)
    with pytest.raises(ValueError):
        optional_recovery_sensors(mapping)


def test_one_running_retry_sensor_targets_all_declared_jobs(mapping, monkeypatch):
    monkeypatch.setenv(INFRA_RETRY_ENABLED_ENV, "true")
    (retry,) = optional_recovery_sensors(mapping)
    assert retry.name == "weather_infrastructure_retry"
    assert retry.default_status == DefaultSensorStatus.RUNNING
    assert {target.job_name for target in retry.targets} == set(mapping)
    assert retry.minimum_interval_seconds == 60


def test_native_is_exact_origin_scoped_and_stopped(mapping, monkeypatch):
    monkeypatch.setenv(NATIVE_ORIGIN_ENV, "exact-repository-origin")
    (native,) = optional_recovery_sensors(mapping)
    assert native.name == "weather_native_incarnation_recovery"
    assert native.default_status == DefaultSensorStatus.STOPPED
    monkeypatch.setenv(NATIVE_ORIGIN_ENV, " exact-repository-origin ")
    with pytest.raises(ValueError):
        optional_recovery_sensors(mapping)


def test_both_opt_ins_are_separate_sensors(mapping, monkeypatch):
    monkeypatch.setenv(INFRA_RETRY_ENABLED_ENV, "true")
    monkeypatch.setenv(NATIVE_ORIGIN_ENV, "exact-repository-origin")
    assert [s.name for s in optional_recovery_sensors(mapping)] == [
        "weather_infrastructure_retry",
        "weather_native_incarnation_recovery",
    ]


def test_native_budget_uses_run_tag_over_instance_zero_for_every_job(mapping):
    for job, _ in mapping.values():
        assert job.tags["dagster/max_retries"] == "1"
        assert job.tags["dagster/retry_on_asset_or_op_failure"] == "false"
        run = DagsterRun(job_name=job.name, status=DagsterRunStatus.FAILURE, tags=job.tags)
        instance = SimpleNamespace(
            run_retries_max_retries=0,
            run_retries_retry_on_asset_or_op_failure=False,
            get_run_group=lambda _, run=run: (run.run_id, [run]),
        )
        assert auto_reexecution_should_retry_run(instance, run, RunFailureReason.RUN_WORKER_RESTART)
        assert not auto_reexecution_should_retry_run(instance, run, RunFailureReason.STEP_FAILURE)
        instance.get_run_group = lambda _, run=run: (run.run_id, [run, run])
        assert not auto_reexecution_should_retry_run(
            instance, run, RunFailureReason.RUN_WORKER_RESTART
        )


def _failure(instance, job, reason=RunFailureReason.RUN_WORKER_RESTART, **kwargs):
    run = instance.create_run_for_job(
        job,
        status=DagsterRunStatus.FAILURE,
        tags={**job.tags, "dagster/code_location": LOCATION_NAME},
        **kwargs,
    )
    instance.report_dagster_event(
        DagsterEvent(
            event_type_value=DagsterEventType.RUN_FAILURE.value,
            job_name=job.name,
            event_specific_data=JobFailureData(None, failure_reason=reason),
        ),
        run.run_id,
    )
    return run


@pytest.mark.parametrize(
    "kind", ["partial", "provider", "manual_child", "native_owner", "eligible"]
)
def test_common_factory_preserves_retry_gates_with_weather_jobs(mapping, monkeypatch, kind):
    monkeypatch.setenv(INFRA_RETRY_ENABLED_ENV, "true")
    (retry,) = optional_recovery_sensors(mapping)
    job = definitions.kma_ultra_short_nowcast_job
    # local_temp는 이 시험만의 임시 SQLite metadata이며 Weather DB resource는 실행하지 않는다.
    overrides = {"run_retries": {"enabled": kind == "native_owner", "max_retries": 0}}
    with DagsterInstance.local_temp(overrides=overrides) as instance:
        parent = _failure(
            instance,
            job,
            RunFailureReason.STEP_FAILURE
            if kind == "provider"
            else RunFailureReason.RUN_WORKER_RESTART,
            **({"op_selection": list(job.graph.node_names())} if kind == "partial" else {}),
        )
        if kind == "manual_child":
            instance.create_run_for_job(
                job,
                status=DagsterRunStatus.SUCCESS,
                tags={**job.tags, "dagster/parent_run_id": parent.run_id},
            )
        result = retry.evaluate_tick(build_sensor_context(instance=instance))
        if kind == "eligible":
            (request,) = result.run_requests
            assert request.job_name == job.name
            assert request.tags[INFRA_RETRY_PARENT_TAG] == parent.run_id
            assert request.tags["dagster/max_retries"] == "0"
            assert instance.get_run_by_id(parent.run_id).tags["dagster/max_retries"] == "0"
        else:
            assert result.run_requests == []


def test_non_idempotent_policy_is_not_registered(mapping, monkeypatch):
    monkeypatch.setenv(INFRA_RETRY_ENABLED_ENV, "true")
    name = next(iter(mapping))
    job, _ = mapping[name]
    mapping[name] = (job, RecoveryPolicy(7200))
    with pytest.raises(ValueError):
        optional_recovery_sensors(mapping)


def test_opt_in_repository_is_loadable_without_resource_execution(mapping, monkeypatch):
    monkeypatch.setenv(INFRA_RETRY_ENABLED_ENV, "true")
    monkeypatch.setenv(NATIVE_ORIGIN_ENV, "exact-repository-origin")
    loaded = Definitions(
        assets=definitions.defs.assets,
        jobs=definitions.defs.jobs,
        schedules=definitions.defs.schedules,
        resources=definitions._resources,
        sensors=[definitions.weather_sync_recovery_sensor, *optional_recovery_sensors(mapping)],
        executor=definitions._BOUNDED_MULTIPROCESS_EXECUTOR,
    )
    Definitions.validate_loadable(loaded)
    assert len(loaded.get_repository_def().sensor_defs) == 3
