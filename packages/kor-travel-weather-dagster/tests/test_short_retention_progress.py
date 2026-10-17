"""DB 없이 실제 asset과 Failure의 정리 진행 metadata를 검증한다."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from dagster import Failure
from kortravelweather_dagster import definitions

from kortravelweather import retention

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)


def _context(purge):
    logs, outputs, disposed = [], [], []
    repo = SimpleNamespace(
        purge_expired_short_forecast=purge,
        engine=SimpleNamespace(dispose=lambda: disposed.append(True)),
    )
    context = SimpleNamespace(
        resources=SimpleNamespace(
            weather_repository=SimpleNamespace(create_repository=lambda: repo),
        ),
        add_output_metadata=outputs.append,
        log=SimpleNamespace(info=lambda message, *args: logs.append(message % args)),
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


@pytest.mark.parametrize("status", ["budget_exhausted", "deferred"])
def test_incomplete_failure_itself_carries_result_metadata(monkeypatch, status):
    result = dict(
        operation_id="fixture-operation", cutoff=NOW.isoformat(), dry_run=False,
        batches=1, matched_in_batches=1, pointers_deleted=1, facts_deleted=1,
        remaining=True, status=status, elapsed_seconds=1.0,
        last_batch_elapsed_seconds=0.1, remaining_budget_seconds=59.0,
    )
    monkeypatch.setattr(definitions, "run_short_forecast_retention", lambda *a, **kw: result)
    context, _, outputs, disposed = _context(lambda **_: None)
    with pytest.raises(Failure) as failure:
        _execute(context)
    assert failure.value.metadata["status"].value == status
    assert failure.value.metadata["facts_deleted"].value == 1
    assert failure.value.metadata["remaining"].value is True
    assert failure.value.metadata["operation_id"].value == "fixture-operation"
    assert outputs == [result] and disposed == [True]


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


def test_completed_batches_logged_once_each_and_result_unchanged(monkeypatch):
    reports = [_report(remaining=True, matched=3), _report(remaining=False, matched=2)]
    monkeypatch.setattr(definitions, "kst_now", lambda: NOW)
    context, logs, outputs, disposed = _context(lambda **_: reports.pop(0))
    result = _execute(context)
    assert result["status"] == "complete_snapshot" and result["facts_deleted"] == 5
    assert len(logs) == 2 and disposed == [True] and outputs == [result]
    assert "batches=1" in logs[0] and "facts_deleted=3" in logs[0]
    assert "batches=2" in logs[1] and "facts_deleted=5" in logs[1]


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
