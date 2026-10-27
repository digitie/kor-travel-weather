"""DB 없이 실제 asset과 Failure의 정리 진행 metadata를 검증한다."""

from datetime import UTC, datetime, timedelta
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


def _key(valid_at, letter):
    return retention.ShortForecastKey(valid_at, valid_at - timedelta(days=2), letter * 64)


def _sdk_job(purge, job_name):
    repo = SimpleNamespace(
        purge_expired_short_forecast=purge,
        engine=SimpleNamespace(dispose=lambda: None, url=SimpleNamespace(database="weather_test")),
    )
    selected = Definitions(
        assets=[definitions.weather_short_forecast_retention],
        jobs=[define_asset_job(job_name)],
        resources={"weather_repository": SimpleNamespace(create_repository=lambda: repo)},
    )
    return selected.resolve_job_def(job_name)


def _latest_metadata(instance):
    event = instance.get_latest_materialization_event(AssetKey("weather_short_forecast_retention"))
    return {key: value.value for key, value in event.asset_materialization.metadata.items()}


def test_next_run_resumes_after_the_previous_runs_last_deleted_key(monkeypatch, tmp_path):
    """실제 SDK 저장소: 부분 진척 run은 SUCCESS이고, 다음 run은 그 위치에서 이어 고른다."""
    cutoff = NOW - timedelta(hours=6)
    first, second = _key(cutoff - timedelta(hours=4), "a"), _key(cutoff - timedelta(hours=3), "b")
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

    monkeypatch.setattr(definitions, "kst_now", lambda: NOW)
    job = _sdk_job(purge, "short_retention_resume_test")
    with DagsterInstance.local_temp(
        str(tmp_path), overrides={"telemetry": {"enabled": False}},
    ) as instance:
        first_run = job.execute_in_process(instance=instance, raise_on_error=False)
        assert first_run.success
        assert [call["after"] for call in calls] == [None, first]
        assert calls[0]["statement_timeout_ms"] == retention.HEAD_STATEMENT_TIMEOUT_MS
        recorded = _latest_metadata(instance)
        assert recorded["resume_policy"] == retention.RETENTION_POLICY
        assert recorded["resume_database"] == "weather_test"
        assert recorded["head_scan_at"] == NOW.isoformat()
        second_run = job.execute_in_process(instance=instance, raise_on_error=False)
        assert second_run.success
        # 두 번째 run의 첫 선택은 머리가 아니라 앞 run이 지운 마지막 키 뒤에서 시작한다.
        assert calls[2]["after"] == first
        assert calls[2]["statement_timeout_ms"] == retention.STATEMENT_TIMEOUT_MS
        metadata = _latest_metadata(instance)
        assert metadata["status"] == "complete_snapshot" and metadata["resumed"] is True
        # 이어 읽은 run은 마지막 머리 훑기 시각을 그대로 승계한다.
        assert metadata["head_scan_at"] == NOW.isoformat()
        assert retention.ShortForecastKey.from_metadata(metadata) == second


def test_behind_backlog_fails_only_after_three_runs_without_draining(monkeypatch, tmp_path):
    """뒤처진 채 줄지 않는 run만 3회 연속부터 실패하고, 다시 줄면 횟수가 0으로 돌아간다."""
    cutoff = NOW - timedelta(hours=6)
    behind = cutoff - timedelta(hours=30)

    def same_lag(letter, hours):
        # 대상시각이 같으면 지연 상한도 같다(위치는 known_at 쪽으로만 나아감).
        return retention.ShortForecastKey(behind, behind - timedelta(hours=hours), letter * 64)

    keys = [
        same_lag("a", 48), same_lag("b", 40), same_lag("c", 30), same_lag("d", 20),
        _key(behind + timedelta(hours=2), "e"),
    ]
    script, calls = [], []
    for key in keys:
        script.append(dict(_report(remaining=True, matched=1000), last_key=key))
        # 같은 run의 다음 batch는 잠금 timeout으로 연기돼 run이 끝난다.
        script.append(dict(_report(remaining=None, matched=0, deferred=True),
                           last_key=None, error_class="LockNotAvailable"))

    def purge(**kwargs):
        calls.append(kwargs)
        return script.pop(0)

    monkeypatch.setattr(definitions, "kst_now", lambda: NOW)
    job = _sdk_job(purge, "short_retention_stall_test")
    outcomes, stalls = [], []
    with DagsterInstance.local_temp(
        str(tmp_path), overrides={"telemetry": {"enabled": False}},
    ) as instance:
        for _ in keys:
            run = job.execute_in_process(instance=instance, raise_on_error=False)
            outcomes.append(run.success)
            recorded = _latest_metadata(instance)
            stalls.append(recorded["lag_stall_runs"])
            if not run.success:
                failure = next(
                    event for event in run.all_events
                    if event.event_type_value == "STEP_FAILURE"
                )
                description = failure.event_specific_data.user_failure_data.description
                assert "연속 줄지 않았습니다" in description
                # 실패 run도 진척·위치·횟수를 materialization으로 남긴다.
                assert recorded["facts_deleted"] == 1000
    assert outcomes == [True, True, False, False, True]
    assert stalls == [1, 2, 3, 4, 0]
    # 실패한 3번째 run의 위치를 4번째 run이 이어받는다.
    assert [call["after"] for call in calls[::2]] == [None, *keys[:-1]]


@pytest.mark.parametrize(("previous", "lag_hours", "remaining", "expected"), [
    ({}, 30, True, 1),
    ({"backlog_lag_seconds": 31 * 3600.0, "lag_stall_runs": 2}, 30, True, 0),
    ({"backlog_lag_seconds": 30 * 3600.0, "lag_stall_runs": 2}, 30, True, 3),
    ({"backlog_lag_seconds": 29 * 3600.0, "lag_stall_runs": 5}, 30, True, 6),
    ({"backlog_lag_seconds": 40 * 3600.0, "lag_stall_runs": 9}, 23, True, 0),
    ({"backlog_lag_seconds": 40 * 3600.0, "lag_stall_runs": 9}, 0, False, 0),
    ({"backlog_lag_seconds": "x", "lag_stall_runs": True}, 30, True, 1),
])
def test_lag_stall_counter(previous, lag_hours, remaining, expected):
    result = {"remaining": remaining, "backlog_lag_seconds": lag_hours * 3600.0}
    assert retention.lag_stall_runs(previous, result) == expected
    assert retention.backlog_stalled({"lag_stall_runs": expected}) is (expected >= 3)


def test_behind_but_draining_run_succeeds_with_warning(monkeypatch):
    result = dict(_result("budget_exhausted", 50000), backlog_lag_seconds=30 * 3600.0)
    monkeypatch.setattr(definitions, "run_short_forecast_retention", lambda *a, **kw: result)
    monkeypatch.setattr(
        definitions, "_short_retention_previous_metadata",
        lambda _: {"backlog_lag_seconds": 31 * 3600.0, "lag_stall_runs": 2},
    )
    context, _, outputs, disposed = _context(lambda **_: None)
    assert _execute(context) is result
    assert result["lag_stall_runs"] == 0 and outputs == [result] and disposed == [True]
    assert len(context.warnings) == 1 and "lag_stall_runs=0" in context.warnings[0]
    assert context.observations == []


@pytest.mark.parametrize(("env", "expected"), [(None, 150.0), ("120", 120.0), ("180", 180.0)])
def test_retention_budget_setting_reaches_the_runner(monkeypatch, env, expected):
    name = "KOR_TRAVEL_WEATHER_SHORT_FORECAST_RETENTION_BUDGET_SECONDS"
    if env is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, env)
    seen = {}

    def run(*args, **kwargs):
        seen.update(kwargs)
        return _result("complete_snapshot", 0)

    monkeypatch.setattr(definitions, "run_short_forecast_retention", run)
    context, _, _, _ = _context(lambda **_: None)
    _execute(context)
    assert seen["budget_seconds"] == expected


@pytest.mark.parametrize("value", ["0", "181", "-1"])
def test_retention_budget_setting_is_bounded(monkeypatch, value):
    from pydantic import ValidationError

    from kortravelweather.settings import WeatherSettings

    monkeypatch.setenv("KOR_TRAVEL_WEATHER_SHORT_FORECAST_RETENTION_BUDGET_SECONDS", value)
    with pytest.raises(ValidationError):
        WeatherSettings()


def test_runner_budget_cap_follows_the_job_runtime_bound():
    def purge(**_):
        return dict(_report(remaining=False, matched=1), last_key=None)

    repository = SimpleNamespace(purge_expired_short_forecast=purge)
    cutoff = NOW - timedelta(hours=6)
    result = retention.run_short_forecast_retention(
        repository, cutoff=cutoff, dry_run=False, budget_seconds=180,
    )
    assert result["budget_seconds"] == 180 and result["status"] == "complete_snapshot"
    with pytest.raises(ValueError):
        retention.run_short_forecast_retention(
            repository, cutoff=cutoff, dry_run=False, budget_seconds=181,
        )


def test_head_sweep_timeout_continues_from_the_stored_position():
    """주기 머리 훑기가 문장 예산을 넘어도 같은 run이 저장 위치에서 이어 정리한다."""
    cutoff = NOW - timedelta(hours=6)
    stored, later = _key(cutoff - timedelta(hours=3), "g"), _key(cutoff - timedelta(hours=2), "h")
    calls = []

    def purge(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return dict(_report(remaining=None, matched=0, deferred=True),
                        last_key=None, error_class="QueryCanceled")
        return dict(_report(remaining=False, matched=5), last_key=later)

    result = retention.run_short_forecast_retention(
        SimpleNamespace(purge_expired_short_forecast=purge), cutoff=cutoff, dry_run=False,
        fallback=stored, now=NOW,
    )
    assert calls[0]["after"] is None
    assert calls[0]["statement_timeout_ms"] == retention.HEAD_STATEMENT_TIMEOUT_MS
    assert calls[1]["after"] == stored
    assert calls[1]["statement_timeout_ms"] == retention.STATEMENT_TIMEOUT_MS
    assert result["head_sweep_timed_out"] is True and result["resumed"] is True
    assert result["status"] == "complete_snapshot" and retention.made_progress(result)
    # 훑기 시도 시각을 남겨 다음 시도는 다음 주기에 한다.
    assert result["head_scan_at"] == NOW.isoformat()
    assert retention.ShortForecastKey.from_metadata(result) == later


def test_head_sweep_does_not_fall_back_on_purge_lock_contention():
    cutoff = NOW - timedelta(hours=6)
    calls = []

    def purge(**kwargs):
        calls.append(kwargs)
        return dict(_report(remaining=None, matched=0, deferred=True), last_key=None)

    result = retention.run_short_forecast_retention(
        SimpleNamespace(purge_expired_short_forecast=purge), cutoff=cutoff, dry_run=False,
        fallback=_key(cutoff - timedelta(hours=3), "i"), now=NOW,
    )
    assert len(calls) == 1 and result["status"] == "deferred"
    assert result["head_sweep_timed_out"] is False and not retention.made_progress(result)


@pytest.mark.parametrize(("changes", "reason", "fallback"), [
    ({"resume_policy": "5h:other"}, "policy_changed", False),
    ({"resume_database": "restored_copy"}, "database_changed", False),
    ({"head_scan_at": (NOW - timedelta(hours=24)).isoformat()}, "periodic_head_sweep", True),
    ({"head_scan_at": None}, "periodic_head_sweep", True),
])
def test_untrusted_previous_position_restarts_from_the_head(changes, reason, fallback):
    cutoff = NOW - timedelta(hours=6)
    key = _key(cutoff - timedelta(hours=1), "e")
    previous = {
        **key.as_metadata(), "resume_policy": retention.RETENTION_POLICY,
        "resume_database": "weather_test", "head_scan_at": (NOW - timedelta(hours=1)).isoformat(),
    }
    trusted = retention.resume_position(previous, cutoff=cutoff, database="weather_test", now=NOW)
    assert trusted == retention.ResumeDecision(key, NOW - timedelta(hours=1), None)
    # 주기 훑기만 해당하면 위치는 믿을 만하므로 머리 탐색 실패 때의 fallback으로 남긴다.
    assert retention.resume_position(
        {**previous, **changes}, cutoff=cutoff, database="weather_test", now=NOW,
    ) == retention.ResumeDecision(None, None, reason, key if fallback else None)


def test_position_ahead_of_the_cutoff_is_discarded():
    """시계 역행 등으로 경계보다 앞선 위치는 버리고 머리부터 시작한다(그 앞 행을 놓치지 않음)."""
    cutoff = NOW - timedelta(hours=6)
    ahead = _key(cutoff + timedelta(minutes=1), "f")
    previous = {
        **ahead.as_metadata(), "resume_policy": retention.RETENTION_POLICY,
        "resume_database": "weather_test", "head_scan_at": NOW.isoformat(),
    }
    assert retention.resume_position(
        previous, cutoff=cutoff, database="weather_test", now=NOW,
    ) == retention.ResumeDecision(None, None, "ahead_of_cutoff")
    calls = []

    def purge(**kwargs):
        calls.append(kwargs)
        return dict(_report(remaining=False, matched=1), last_key=None)

    result = retention.run_short_forecast_retention(
        SimpleNamespace(purge_expired_short_forecast=purge), cutoff=cutoff, dry_run=False,
        after=ahead, now=NOW,
    )
    assert calls[0]["after"] is None and calls[0]["statement_timeout_ms"] == 45000
    assert result["resume_discarded"] == "ahead_of_cutoff" and result["resumed"] is False
    assert result["head_scan_at"] == NOW.isoformat()


def test_behind_and_flat_third_run_fails_and_records_materialization(monkeypatch):
    result = dict(_result("budget_exhausted", 10000), backlog_lag_seconds=30 * 3600.0)
    monkeypatch.setattr(definitions, "run_short_forecast_retention", lambda *a, **kw: result)
    monkeypatch.setattr(
        definitions, "_short_retention_previous_metadata",
        lambda _: {"backlog_lag_seconds": 30 * 3600.0, "lag_stall_runs": 2},
    )
    context, _, outputs, disposed = _context(lambda **_: None)
    with pytest.raises(Failure) as failure:
        _execute(context)
    assert "3회 연속" in failure.value.description
    assert failure.value.metadata["facts_deleted"].value == 10000
    assert failure.value.metadata["lag_stall_runs"].value == 3
    # 실패 전에 위치와 횟수를 materialization으로 남긴다.
    assert [type(event).__name__ for event in context.observations] == ["AssetMaterialization"]
    assert outputs == [result] and disposed == [True]


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
