"""DB 없이 실제 asset과 Failure의 정리 진행 metadata를 검증한다."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from dagster import AssetKey, DagsterInstance, Definitions, Failure, define_asset_job
from kortravelweather_dagster import definitions
from sqlalchemy.exc import OperationalError

from kortravelweather import retention

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)


def _context(purge):
    logs, outputs, disposed, observations, warnings = [], [], [], [], []
    repo = SimpleNamespace(
        purge_expired_short_forecast=purge,
        engine=SimpleNamespace(dispose=lambda: disposed.append(True)),
    )
    context = SimpleNamespace(
        resources=SimpleNamespace(
            weather_repository=SimpleNamespace(create_repository=lambda: repo),
        ),
        asset_key=AssetKey("weather_short_forecast_retention"),
        log_event=observations.append,
        observations=observations,
        warnings=warnings,
        add_output_metadata=outputs.append,
        log=SimpleNamespace(
            info=lambda message, *args: logs.append(message % args),
            warning=lambda message, *args: warnings.append(message % args),
        ),
    )
    return context, logs, outputs, disposed


def _report(*, remaining, matched=1, deferred=False):
    return dict(
        matched_in_batch=matched, pointers_deleted=matched,
        facts_deleted=matched, remaining=remaining, deferred=deferred,
        elapsed_seconds=0.1,
    )


def _execute(context):
    return definitions.weather_short_forecast_retention.op.compute_fn.decorated_fn(context)


def _result(status, facts_deleted):
    return dict(
        operation_id="fixture-operation", cutoff=NOW.isoformat(), dry_run=False,
        batches=1, matched_in_batches=facts_deleted, pointers_deleted=facts_deleted,
        facts_deleted=facts_deleted, remaining=True, status=status, elapsed_seconds=1.0,
        last_batch_elapsed_seconds=0.1, remaining_budget_seconds=59.0,
        error_class=None, backlog_lag_seconds=3600.0,
    )


@pytest.mark.parametrize("status", ["budget_exhausted", "deferred"])
def test_zero_progress_failure_itself_carries_result_metadata(monkeypatch, status):
    result = _result(status, 0)
    monkeypatch.setattr(definitions, "run_short_forecast_retention", lambda *a, **kw: result)
    context, _, outputs, disposed = _context(lambda **_: None)
    with pytest.raises(Failure) as failure:
        _execute(context)
    assert "진척 없이" in failure.value.description
    assert failure.value.metadata["status"].value == status
    assert failure.value.metadata["facts_deleted"].value == 0
    assert failure.value.metadata["remaining"].value is True
    assert failure.value.metadata["operation_id"].value == "fixture-operation"
    assert outputs == [result] and disposed == [True]


@pytest.mark.parametrize("status", ["budget_exhausted", "deferred"])
def test_partial_progress_with_backlog_is_success_not_failure(monkeypatch, status):
    """운영 2026-10-20~26: 매 실행 약1만 행을 지우고도 Failure였다. 진척은 성공이다."""
    result = _result(status, 10000)
    monkeypatch.setattr(definitions, "run_short_forecast_retention", lambda *a, **kw: result)
    context, _, outputs, disposed = _context(lambda **_: None)
    assert _execute(context) is result
    assert outputs == [result] and disposed == [True]
    assert len(context.warnings) == 1 and "backlog" in context.warnings[0]
    assert "facts_deleted=10000" in context.warnings[0]


def test_sql_exception_keeps_prior_committed_progress_without_success(monkeypatch):
    calls = []

    def purge(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise TimeoutError("fixture SQL failure")
        return _report(remaining=True, matched=3)

    monkeypatch.setattr(definitions, "kst_now", lambda: NOW)
    context, logs, outputs, disposed = _context(purge)
    with pytest.raises(TimeoutError, match="fixture SQL failure"):
        _execute(context)
    assert len(calls) == 2 and disposed == [True] and outputs == []
    assert len(logs) == 1
    assert "batches=1" in logs[0] and "facts_deleted=3" in logs[0]
    assert "pointers_deleted=3" in logs[0]
    assert "fixture SQL failure" not in logs[0]
    assert len(context.observations) == 1
    observation = context.observations[0]
    assert observation.asset_key == context.asset_key
    assert observation.metadata["facts_deleted"].value == 3
    assert observation.metadata["phase"].value == "BATCH_COMMITTED"


def test_completed_batches_logged_once_each_and_result_unchanged(monkeypatch):
    reports = [_report(remaining=True, matched=3), _report(remaining=False, matched=2)]
    monkeypatch.setattr(definitions, "kst_now", lambda: NOW)
    context, logs, outputs, disposed = _context(lambda **_: reports.pop(0))
    result = _execute(context)
    assert result["status"] == "complete_snapshot" and result["facts_deleted"] == 5
    assert len(logs) == 2 and disposed == [True] and outputs == [result]
    assert "batches=1" in logs[0] and "facts_deleted=3" in logs[0]
    assert "batches=2" in logs[1] and "facts_deleted=5" in logs[1]
    assert [item.metadata["batches"].value for item in context.observations] == [1, 2]
    assert [item.metadata["facts_deleted"].value for item in context.observations] == [3, 5]


def test_empty_locked_selection_defers_and_failure_is_observable(monkeypatch):
    calls = []

    def purge(**kwargs):
        calls.append(kwargs)
        return _report(remaining=True, matched=0)

    monkeypatch.setattr(definitions, "kst_now", lambda: NOW)
    context, logs, _, disposed = _context(purge)
    with pytest.raises(Failure) as failure:
        _execute(context)
    assert failure.value.metadata["status"].value == "deferred"
    assert failure.value.metadata["facts_deleted"].value == 0
    assert len(calls) == len(logs) == 1 and disposed == [True]


def test_runner_never_turns_budget_exhaustion_into_completion(monkeypatch):
    ticks = iter([0.0, 61.0, 61.0])
    monkeypatch.setattr(retention, "monotonic", lambda: next(ticks))
    result = retention.run_short_forecast_retention(
        SimpleNamespace(purge_expired_short_forecast=lambda **_: pytest.fail("budget exhausted")),
        cutoff=NOW, dry_run=False,
    )
    assert result["status"] == "budget_exhausted" and result["batches"] == 0


def test_advisory_deferral_does_not_invent_remaining_or_deleted_rows(monkeypatch):
    monkeypatch.setattr(definitions, "kst_now", lambda: NOW)
    context, logs, _, disposed = _context(
        lambda **_: _report(remaining=None, matched=0, deferred=True),
    )
    with pytest.raises(Failure) as failure:
        _execute(context)
    assert failure.value.metadata["status"].value == "deferred"
    assert failure.value.metadata["remaining"].value is None
    assert failure.value.metadata["facts_deleted"].value == 0
    assert len(logs) == 1 and disposed == [True]


@pytest.mark.parametrize("log_level", ["ERROR", "WARN"])
def test_committed_observation_survives_sql_failure_and_instance_reopen(
    monkeypatch, tmp_path, log_level,
):
    """실제 SDK 저장소는 INFO 차단/다음 SQL 실패와 무관하게 앞 commit을 보존한다."""
    calls, disposed = [], []

    def purge(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise OperationalError("fixture SQL", {}, RuntimeError("fixture failure"))
        return _report(remaining=True, matched=3)

    repo = SimpleNamespace(
        purge_expired_short_forecast=purge,
        engine=SimpleNamespace(dispose=lambda: disposed.append(True)),
    )
    monkeypatch.setattr(definitions, "kst_now", lambda: NOW)
    selected_asset = definitions.weather_short_forecast_retention
    job_name = "short_retention_persisted_progress_test"
    selected = Definitions(
        assets=[selected_asset],
        jobs=[define_asset_job(job_name)],
        resources={"weather_repository": SimpleNamespace(create_repository=lambda: repo)},
    )
    with DagsterInstance.local_temp(
        str(tmp_path), overrides={
            "python_logs": {"python_log_level": log_level}, "telemetry": {"enabled": False},
        },
    ) as instance:
        result = selected.resolve_job_def(job_name).execute_in_process(
            instance=instance, raise_on_error=False,
        )
        assert not result.success
        run_id, ref = result.run_id, instance.get_ref()
        assert len(calls) == 2 and disposed == [True]
        # INFO 자체가 저장되지 않는 조건이므로 로그 문자열만 보는 테스트가 아니다.
        assert not any(
            "BATCH_COMMITTED" in record.user_message
            for record in instance.all_logs(run_id)
        )

    with DagsterInstance.from_ref(ref) as reopened:
        records = reopened.all_logs(run_id)
        assert reopened.get_run_by_id(run_id).status.value == "FAILURE"
        events = [record.dagster_event for record in records if record.is_dagster_event]
        observations = [
            event.event_specific_data.asset_observation for event in events
            if event.event_type_value == "ASSET_OBSERVATION"
        ]
        assert len(observations) == 1
        observation = observations[0]
        assert observation.asset_key == AssetKey("weather_short_forecast_retention")
        values = {key: value.value for key, value in observation.metadata.items()}
        assert set(values) == {
            "phase", "operation_id", "batches", "matched_in_batches",
            "facts_deleted", "pointers_deleted", "remaining", "elapsed_seconds",
        }
        assert values["phase"] == "BATCH_COMMITTED"
        assert values["batches"] == 1 and values["matched_in_batches"] == 3
        assert values["facts_deleted"] == values["pointers_deleted"] == 3
        assert values["remaining"] is True
        assert isinstance(values["operation_id"], str) and values["operation_id"]
        assert isinstance(values["elapsed_seconds"], float)
        assert values["elapsed_seconds"] >= 0
        kinds = [event.event_type_value for event in events]
        assert kinds.index("ASSET_OBSERVATION") < kinds.index("STEP_FAILURE")
        failure = next(event for event in events if event.event_type_value == "STEP_FAILURE")
        assert failure.event_specific_data.user_failure_data is None
        assert failure.event_specific_data.error.cause.cls_name == "OperationalError"
        assert not any(event.event_type_value == "STEP_OUTPUT" for event in events)


def test_next_run_resumes_after_the_previous_runs_last_deleted_key(monkeypatch, tmp_path):
    """실제 SDK 저장소: 부분 진척 run은 SUCCESS이고, 다음 run은 그 위치에서 이어 고른다."""
    first = retention.ShortForecastKey(
        datetime(2026, 10, 15, 3, tzinfo=UTC), datetime(2026, 10, 13, 2, tzinfo=UTC), "a" * 64,
    )
    second = retention.ShortForecastKey(
        datetime(2026, 10, 15, 4, tzinfo=UTC), datetime(2026, 10, 13, 5, tzinfo=UTC), "b" * 64,
    )
    calls = []

    def purge(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return dict(_report(remaining=True, matched=1000), last_key=first)
        if len(calls) == 2:
            # 같은 run의 다음 batch가 잠금 timeout으로 연기됐다. 앞 commit은 진척이다.
            return dict(_report(remaining=None, matched=0, deferred=True),
                        last_key=None, error_class="LockNotAvailable")
        return dict(_report(remaining=False, matched=5), last_key=second)

    repo = SimpleNamespace(
        purge_expired_short_forecast=purge, engine=SimpleNamespace(dispose=lambda: None),
    )
    monkeypatch.setattr(definitions, "kst_now", lambda: NOW)
    job_name = "short_retention_resume_test"
    selected = Definitions(
        assets=[definitions.weather_short_forecast_retention],
        jobs=[define_asset_job(job_name)],
        resources={"weather_repository": SimpleNamespace(create_repository=lambda: repo)},
    )
    with DagsterInstance.local_temp(
        str(tmp_path), overrides={"telemetry": {"enabled": False}},
    ) as instance:
        job = selected.resolve_job_def(job_name)
        first_run = job.execute_in_process(instance=instance, raise_on_error=False)
        assert first_run.success
        assert [call["after"] for call in calls] == [None, first]
        assert calls[0]["statement_timeout_ms"] == retention.HEAD_STATEMENT_TIMEOUT_MS
        second_run = job.execute_in_process(instance=instance, raise_on_error=False)
        assert second_run.success
        # 두 번째 run의 첫 선택은 머리가 아니라 앞 run이 지운 마지막 키 뒤에서 시작한다.
        assert calls[2]["after"] == first
        assert calls[2]["statement_timeout_ms"] == retention.STATEMENT_TIMEOUT_MS
        event = instance.get_latest_materialization_event(
            AssetKey("weather_short_forecast_retention"),
        )
        metadata = {
            key: value.value for key, value in event.asset_materialization.metadata.items()
        }
        assert metadata["status"] == "complete_snapshot" and metadata["resumed"] is True
        assert retention.ShortForecastKey.from_metadata(metadata) == second


def test_only_committed_whitelisted_progress_becomes_an_observation(monkeypatch):
    progress = dict(
        phase="BATCH_COMMITTED", operation_id="fixture-operation", batches=1,
        matched_in_batches=3, facts_deleted=3, pointers_deleted=2,
        remaining=True, elapsed_seconds=0.1, raw_payload="fixture must not escape",
    )
    result = {"status": "complete_snapshot"}

    def run(*args, on_progress, **kwargs):
        on_progress({**progress, "phase": "BATCH_INTENT"})
        on_progress({**progress, "phase": "DRY_RUN_SNAPSHOT"})
        on_progress(progress)
        return result

    monkeypatch.setattr(definitions, "run_short_forecast_retention", run)
    context, logs, outputs, disposed = _context(lambda **_: None)
    assert _execute(context) == result
    assert len(context.observations) == len(logs) == 1
    values = {key: value.value for key, value in context.observations[0].metadata.items()}
    assert values == {key: value for key, value in progress.items() if key != "raw_payload"}
    assert outputs == [result] and disposed == [True]
