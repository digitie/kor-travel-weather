from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
from kma import DataGoKrClient
from kortravelweather_dagster import alert_events as module

from kortravelweather.alert_events import ALERT_DATASET, ALERT_PROVIDER
from kortravelweather.models import kst_now
from kortravelweather.repository import WeatherRepository


def raw_row(seq=1, **changes):
    return {
        "stnId": "108",
        "tmFc": kst_now().strftime("%Y%m%d%H%M"),
        "tmSeq": str(seq),
        "areaCode": "L1082500",
        "areaName": "부산동부",
        "warnVar": "2",
        "warnStress": "0",
        "command": "1",
        "cancel": "0",
        **changes,
    }


def body(rows, *, page=1, total=None, size=100):
    return {
        "items": {"item": rows},
        "pageNo": page,
        "numOfRows": size,
        "totalCount": len(rows) if total is None else total,
    }


def response(value):
    return httpx.Response(
        200,
        json={
            "response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                "body": value,
            },
        },
    )


@asynccontextmanager
async def sdk(handler):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as session:
        client = DataGoKrClient(
            "fixture-only-key",
            session=session,
            retries=0,
            max_rps=1000,
        )
        try:
            yield client
        finally:
            await client.aclose()


@pytest.fixture
def repo(weather_test_database_url):
    result = WeatherRepository(weather_test_database_url)
    result.create_schema()
    try:
        yield result
    finally:
        result.engine.dispose()


async def test_actual_public_sdk_collects_all_pages_and_rechecks_first():
    calls = []
    rows = [raw_row(i) for i in range(1, 102)]

    def handler(request):
        assert request.url.path.endswith("/WthrWrnInfoService/getPwnCd")
        assert request.url.params["stnId"] == "108"
        assert request.url.params["numOfRows"] == "100"
        page = int(request.url.params["pageNo"])
        calls.append(page)
        return response(body(rows[:100] if page == 1 else rows[100:], page=page, total=101))

    async with sdk(handler) as client:
        result = await module.collect_alert_pages(client)
    assert calls == [1, 2, 1]
    assert result.observations == 101 and result.requests_fetched == 3
    assert len(result.records) == 2 and result.unknown_observations == 0
    assert "fixture-only-key" not in str(result.records)


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("short", "ALERT_PAGE_ROWS"),
        ("total", "ALERT_PAGE_TOTAL_CHANGED"),
        ("duplicate", "ALERT_PAGE_DUPLICATE"),
        ("changed", "ALERT_FIRST_PAGE_CHANGED"),
        ("over", "ALERT_TOO_MANY_ROWS"),
        ("badmeta", "ALERT_PAGE_METADATA"),
    ],
)
async def test_incomplete_or_mutating_provider_never_publishes(repo, kind, code):
    calls = []
    rows = [raw_row(i) for i in range(1, 103)]

    def handler(request):
        page = int(request.url.params["pageNo"])
        calls.append(page)
        data = rows[:100] if page == 1 else rows[100:101]
        total = 101
        if kind == "short" and page == 1:
            data = data[:-1]
        if kind == "total" and page == 2:
            total, data = 102, rows[100:102]
        if kind == "duplicate" and page == 2:
            data = rows[:1]
        if kind == "changed" and len(calls) == 3:
            data = [raw_row(9999), *data[1:]]
        if kind == "over":
            total = 10001
        value = body(data, page=page, total=total)
        if kind == "badmeta":
            value["pageNo"] = True
        return response(value)

    with pytest.raises(module.AlertCollectionError, match=code):
        async with sdk(handler) as client:
            await module.run_alert_event_sync_async(repository=repo, client=client)
    run = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)[0]
    assert run.status == "failed" and run.error == code
    assert repo.list_sync_run_sources(run.run_id) == []


async def test_zero_success_is_fresh_without_inventing_clear(repo):
    async with sdk(lambda _: response(body([], size=0))) as client:
        result = await module.run_alert_event_sync_async(repository=repo, client=client)
    assert result["status"] == "success" and result["observations"] == 0
    assert result["pages_complete"] and result["events_loaded"] == 0
    assert result["requests_fetched"] == 1


async def test_provider_auth_error_is_safe_in_run_and_exception(repo):
    with pytest.raises(module.AlertCollectionError) as caught:
        async with sdk(
            lambda _: httpx.Response(
                403,
                text="denied https://example.invalid/?serviceKey=fixture-only-key",
            )
        ) as client:
            await module.run_alert_event_sync_async(repository=repo, client=client)
    run = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)[0]
    assert run.error == "ALERT_PROVIDER_AUTH" and run.status == "failed"
    assert "fixture-only-key" not in str(caught.value)
    from dagster._utils.error import serializable_error_info_from_exc_info

    error = caught.value
    serialized = serializable_error_info_from_exc_info(
        (
            type(error),
            error,
            error.__traceback__,
        )
    )
    assert "fixture-only-key" not in serialized.to_string()
    assert "ALERT_PROVIDER_AUTH" in serialized.to_string()
    assert serialized.context is None and serialized.cause is None


class BlockingClient:
    def __init__(self):
        self.entered = asyncio.Event()
        self.closed = False

    async def request_with_metadata(self, *args, **kwargs):
        self.entered.set()
        await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


async def test_cancellation_propagates_and_terminalizes_without_publication(repo):
    client = BlockingClient()
    task = asyncio.create_task(module.run_alert_event_sync_async(repository=repo, client=client))
    await asyncio.wait_for(client.entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    run = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)[0]
    assert run.status == "cancelled" and client.closed
    assert repo.list_sync_run_sources(run.run_id) == []


async def test_collection_wall_clock_timeout_is_bounded(repo, monkeypatch):
    monkeypatch.setattr(module, "COLLECTION_TIMEOUT_SECONDS", 0.1)
    client = BlockingClient()
    with pytest.raises(module.AlertCollectionError, match="ALERT_TIMEOUT"):
        await module.run_alert_event_sync_async(repository=repo, client=client)
    run = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)[0]
    assert run.error == "ALERT_TIMEOUT" and run.status == "failed" and client.closed


async def test_recovery_between_pages_fences_future_publication(repo):
    rows = [raw_row(i) for i in range(1, 102)]

    def handler(request):
        assert int(request.url.params["pageNo"]) == 1
        run = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)[0]
        repo.finish_sync_run(run.run_id, status="failed", error="recovered_elsewhere")
        return response(body(rows[:100], total=101))

    with pytest.raises(module.AlertCollectionError, match="ALERT_RUN_OWNERSHIP_LOST"):
        async with sdk(handler) as client:
            await module.run_alert_event_sync_async(repository=repo, client=client)
    run = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)[0]
    assert run.error == "recovered_elsewhere"
    assert repo.list_sync_run_sources(run.run_id) == []


async def test_cancellation_while_start_result_is_pending_does_not_orphan_running_run(
    repo,
    monkeypatch,
):
    import threading

    created = threading.Event()
    release = threading.Event()
    returned = threading.Event()
    original = repo.start_sync_run

    def pending_start(**kwargs):
        result = original(**kwargs)
        created.set()
        try:
            assert release.wait(5)
            return result
        finally:
            returned.set()

    monkeypatch.setattr(repo, "start_sync_run", pending_start)
    client = BlockingClient()
    task = asyncio.create_task(module.run_alert_event_sync_async(repository=repo, client=client))
    try:
        assert await asyncio.to_thread(created.wait, 5)
        task.cancel()
        asyncio.get_running_loop().call_later(0.05, release.set)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(returned.wait, 5)
        run = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)[0]
        assert run.status == "cancelled"
        assert client.closed and repo.list_sync_run_sources(run.run_id) == []
    finally:
        release.set()


async def test_public_sdk_and_real_postgres_publish_replay_once(repo):
    row = raw_row()
    for expected in [1, 0]:
        async with sdk(lambda _: response(body([row]))) as client:
            result = await module.run_alert_event_sync_async(repository=repo, client=client)
        assert result["events_loaded"] == expected and result["status"] == "success"
    runs = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)
    assert len(runs) == 2 and runs[0].status == runs[1].status == "success"
    assert repo.list_sync_run_sources(runs[0].run_id) == repo.list_sync_run_sources(runs[1].run_id)
