"""지역 수집의 원천별 job 분리, 보충 tick, 일시 네트워크 오류 재시도.

2026-11-22 20:30Z krex DNS 오류 하나가 ``regional_weather_job`` run 전체를 FAILURE로 만들어
성공한 해수욕장·산악·청정넷까지 24시간 승격되지 못했다(수집 실패 감사 §2.5 P0 #3).
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from dagster import (
    DagsterInstance,
    DagsterRunStatus,
    Failure,
    RetryRequested,
    asset,
    materialize,
)
from dagster._core.storage.dagster_run import DagsterRun
from kortravelweather_dagster import definitions, transient_retry
from kortravelweather_dagster.definitions import (
    REGIONAL_COLLECTIONS,
    _regional_catch_up_gate,
    defs,
    last_regular_tick,
    regional_weather_jobs,
    regional_weather_schedules,
    schedule_status_sources,
)
from kortravelweather_dagster.transient_retry import (
    TRANSIENT_RETRY_MAX,
    backoff_seconds,
    is_transient_network_error,
    retries_transient_network,
)

from kortravelweather.providers.krex import UpstreamBlocked

SEOUL = ZoneInfo("Asia/Seoul")


def _blocked() -> UpstreamBlocked:
    return UpstreamBlocked(
        "python-krex-api", "krex_restarea_weather", {"failure_kind": "blocked", "http_status": 403}
    )


# --- job/schedule 분리 ---------------------------------------------------------


def test_each_regional_source_is_its_own_single_asset_job() -> None:
    assert {collection.job_name for collection in REGIONAL_COLLECTIONS} == {
        "krforest_mountain_job", "khoa_beach_index_job", "krex_restarea_job", "krforest_dust_job",
    }
    repository = defs.get_repository_def()
    for collection in REGIONAL_COLLECTIONS:
        job = regional_weather_jobs[collection.job_name]
        assert repository.get_job(collection.job_name) is not None
        # 한 job에 한 원천: 다른 원천의 실패가 이 run의 SUCCESS(=승격 조건)를 막지 못한다.
        assert job.graph.node_names() == [collection.asset.key.path[-1]]
        assert job.executor_def.name == "in_process"
        assert job.tags["dagster/max_runtime"] == "7200"
    assert not repository.has_job("regional_weather_job")
    assert "twice_daily_regional_weather" not in {s.name for s in repository.schedule_defs}


def test_regional_schedules_keep_twice_daily_and_add_catch_up_hours() -> None:
    by_job = {schedule.job_name: schedule for schedule in regional_weather_schedules}
    assert set(by_job) == {collection.job_name for collection in REGIONAL_COLLECTIONS}
    minutes = set()
    for collection in REGIONAL_COLLECTIONS:
        schedule = by_job[collection.job_name]
        assert schedule.name == collection.schedule_name
        assert schedule.execution_timezone == "Asia/Seoul"
        assert schedule.cron_schedule == f"{collection.minute} 5,6,8,17,18,20 * * *"
        assert collection.regular_cron_schedule == f"{collection.minute} 5,17 * * *"
        minutes.add(collection.minute)
    # 네 원천의 시작을 흩는다(한꺼번에 run worker 넷이 뜨지 않게).
    assert len(minutes) == len(REGIONAL_COLLECTIONS)


def test_regional_status_scopes_name_only_their_own_dataset(monkeypatch) -> None:
    settings = SimpleNamespace(
        service_exports_enabled=True,
        enabled_providers=["python-khoa-api", "python-krforest-api", "python-krex-api"],
    )
    for collection in REGIONAL_COLLECTIONS:
        scopes = schedule_status_sources(collection.job_name, settings)
        assert [(key.provider, key.dataset_key) for key in scopes] == [
            (collection.provider_key, collection.dataset_key)
        ]
    settings.enabled_providers = ["python-khoa-api"]
    assert schedule_status_sources("krex_restarea_job", settings) == ()


# --- 보충 tick gate --------------------------------------------------------------


def test_last_regular_tick_crosses_midnight() -> None:
    assert last_regular_tick(datetime(2026, 11, 24, 6, 30, tzinfo=SEOUL), 30) == datetime(
        2026, 11, 24, 5, 30, tzinfo=SEOUL
    )
    assert last_regular_tick(datetime(2026, 11, 24, 20, 45, tzinfo=SEOUL), 45) == datetime(
        2026, 11, 24, 17, 45, tzinfo=SEOUL
    )
    assert last_regular_tick(datetime(2026, 11, 24, 2, 0, tzinfo=SEOUL), 30) == datetime(
        2026, 11, 23, 17, 30, tzinfo=SEOUL
    )


class _Runs:
    def __init__(self, runs):
        self.runs = runs
        self.filters = []

    def get_runs(self, *, filters, limit):
        self.filters.append((filters, limit))
        return self.runs


def _gate_context(instance, scheduled):
    logs = []
    return SimpleNamespace(
        scheduled_execution_time=scheduled,
        instance=instance,
        log=SimpleNamespace(info=lambda *args: logs.append(args)),
    ), logs


def test_regular_tick_always_runs_without_asking_the_instance() -> None:
    collection = REGIONAL_COLLECTIONS[0]
    instance = _Runs([object()])
    context, _ = _gate_context(
        instance, datetime(2026, 11, 24, 17, collection.minute, tzinfo=SEOUL)
    )
    assert _regional_catch_up_gate(collection)(context) is True
    assert instance.filters == []


def test_catch_up_tick_skips_after_a_success_since_the_regular_tick() -> None:
    collection = REGIONAL_COLLECTIONS[2]
    instance = _Runs([object()])
    scheduled = datetime(2026, 11, 24, 18, collection.minute, tzinfo=SEOUL)
    context, logs = _gate_context(instance, scheduled)
    assert _regional_catch_up_gate(collection)(context) is False
    [(filters, limit)] = instance.filters
    assert limit == 1
    assert filters.job_name == collection.job_name
    assert list(filters.statuses) == [DagsterRunStatus.SUCCESS]
    assert filters.created_after == datetime(2026, 11, 24, 17, collection.minute, tzinfo=SEOUL)
    assert logs == []


def test_catch_up_tick_runs_when_the_regular_run_did_not_succeed() -> None:
    collection = REGIONAL_COLLECTIONS[2]
    context, logs = _gate_context(
        _Runs([]), datetime(2026, 11, 25, 8, collection.minute, tzinfo=SEOUL)
    )
    assert _regional_catch_up_gate(collection)(context) is True
    assert logs and collection.job_name in logs[0]


def test_catch_up_gate_reads_real_run_storage() -> None:
    """RunsFilter 조합이 실제 run storage에서 의도대로 동작한다."""
    collection = REGIONAL_COLLECTIONS[1]
    gate = _regional_catch_up_gate(collection)
    now = datetime.now(UTC).astimezone(SEOUL)
    scheduled = last_regular_tick(now, collection.minute) + timedelta(hours=1)
    with DagsterInstance.ephemeral() as instance:
        context = SimpleNamespace(
            scheduled_execution_time=scheduled, instance=instance,
            log=SimpleNamespace(info=lambda *args: None),
        )
        instance.add_run(DagsterRun(job_name=collection.job_name, status=DagsterRunStatus.FAILURE))
        instance.add_run(DagsterRun(job_name="krex_restarea_job", status=DagsterRunStatus.SUCCESS))
        assert gate(context) is True
        instance.add_run(DagsterRun(job_name=collection.job_name, status=DagsterRunStatus.SUCCESS))
        assert gate(context) is False


# --- 일시 네트워크 오류 재시도 -----------------------------------------------------


def test_backoff_doubles_to_a_cap_with_jitter() -> None:
    for attempt, (low, high) in enumerate([(30, 60), (60, 120), (120, 240), (240, 480)]):
        assert backoff_seconds(attempt, rng=lambda: 0.0) == low
        assert backoff_seconds(attempt, rng=lambda: 1.0) == high
    assert backoff_seconds(10, rng=lambda: 1.0) == 600
    with pytest.raises(ValueError):
        backoff_seconds(-1)


def _transient_errors():
    import httpx
    from khoa.exceptions import KhoaRequestError
    from krex.exceptions import KrexConnectionError, KrexTimeoutError
    from krforest.exceptions import ForestRequestError

    dns = KrexConnectionError("[Errno -5] No address associated with hostname")
    wrapped = RuntimeError("publish failed")
    wrapped.__cause__ = dns
    return [
        dns,
        KrexTimeoutError("timed out"),
        ForestRequestError("request failed: ...", failure_kind="network"),
        KhoaRequestError("HTTP transport error: ...", failure_kind="request", retryable=True),
        socket.gaierror(-5, "No address associated with hostname"),
        ConnectionResetError(),
        httpx.ConnectError("boom"),
        wrapped,
    ]


def _deterministic_errors():
    from khoa.exceptions import KhoaRequestError, KhoaServerError
    from krex.exceptions import KrexAuthError, KrexParseError
    from krforest.exceptions import ForestRequestError

    return [
        KrexAuthError("bad key"),
        KrexParseError("bad payload"),
        ForestRequestError("HTTP 400", failure_kind="request"),
        KhoaRequestError("HTTP 400", status_code=400, failure_kind="request", retryable=False),
        KhoaServerError("HTTP 500", status_code=500, failure_kind="server"),
        _blocked(),
        Failure(description="blocked"),
        ValueError("x"),
    ]


def test_only_network_errors_are_transient() -> None:
    for exc in _transient_errors():
        assert is_transient_network_error(exc), repr(exc)
    for exc in _deterministic_errors():
        assert not is_transient_network_error(exc), repr(exc)


def test_upstream_block_stays_deterministic_even_over_a_network_cause() -> None:
    from krex.exceptions import KrexConnectionError

    blocked = _blocked()
    blocked.__cause__ = KrexConnectionError("reset")
    assert not is_transient_network_error(blocked)


def _decorated(raise_exc):
    @retries_transient_network
    def compute(context):
        raise raise_exc

    return compute


def _context(retry_number):
    return SimpleNamespace(
        retry_number=retry_number,
        log=SimpleNamespace(warning=lambda *_: None, error=lambda *_: None),
    )


def test_transient_error_requests_a_delayed_op_retry() -> None:
    from krex.exceptions import KrexConnectionError

    with pytest.raises(RetryRequested) as raised:
        _decorated(KrexConnectionError("dns"))(_context(0))
    assert raised.value.max_retries == TRANSIENT_RETRY_MAX
    assert 30 <= raised.value.seconds_to_wait <= 60
    assert isinstance(raised.value.__cause__, KrexConnectionError)


def test_last_attempt_and_deterministic_errors_fail_as_is() -> None:
    from krex.exceptions import KrexAuthError, KrexConnectionError

    with pytest.raises(KrexConnectionError):
        _decorated(KrexConnectionError("dns"))(_context(TRANSIENT_RETRY_MAX))
    with pytest.raises(KrexAuthError):
        _decorated(KrexAuthError("bad key"))(_context(0))


def test_every_regional_asset_retries_transient_network_errors() -> None:
    for collection in REGIONAL_COLLECTIONS:
        compute = collection.asset.op.compute_fn.decorated_fn
        assert getattr(compute, "retries_transient_network", False), collection.asset.key
        # skips_live_overlap도 그대로 남는다(감싼 순서가 바뀌어도 속성은 wraps로 전달).
        assert getattr(compute, "skips_live_overlap", False), collection.asset.key


def test_dagster_actually_retries_in_process_and_succeeds(monkeypatch) -> None:
    """in-process executor가 RetryRequested를 받아 같은 run에서 다시 실행한다."""
    from krex.exceptions import KrexConnectionError

    monkeypatch.setattr(transient_retry, "backoff_seconds", lambda attempt: 0.0)
    attempts = []

    @asset(name="flaky_regional_probe")
    @retries_transient_network
    def flaky(context):
        attempts.append(context.retry_number)
        if len(attempts) < 3:
            raise KrexConnectionError("[Errno -5] No address associated with hostname")
        return {"ok": True}

    result = materialize([flaky])
    assert result.success
    assert attempts == [0, 1, 2]


def test_definitions_module_exposes_no_bundled_regional_job() -> None:
    assert not hasattr(definitions, "regional_weather_job")
