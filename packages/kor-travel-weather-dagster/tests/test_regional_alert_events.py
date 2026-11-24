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


def body(rows, *, page=1, total=None, size=module.PAGE_SIZE):
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


async def test_actual_public_sdk_collects_whole_window_in_one_request():
    calls = []
    rows = [raw_row(i) for i in range(1, 102)]

    def handler(request):
        assert request.url.path.endswith("/WthrWrnInfoService/getPwnCd")
        assert request.url.params["stnId"] == "108"
        # 실측한 크기만 요청한다(test_alert_events_real_fixture).
        assert request.url.params["numOfRows"] == str(module.PAGE_SIZE) == "1000"
        page = int(request.url.params["pageNo"])
        calls.append(page)
        return response(body(rows))

    async with sdk(handler) as client:
        result = await module.collect_alert_pages(client)
    # 페이지를 나누지 않으므로 두 번째 페이지·첫 페이지 재대조 요청이 없다.
    assert calls == [1]
    assert result.observations == 101 and result.requests_fetched == 1
    assert len(result.records) == 1 and result.unknown_observations == 0
    assert result.duplicates_merged == result.quarantined_rows == 0
    assert "fixture-only-key" not in str(result.records)


def _conflicting_pairs(count):
    rows = []
    for seq in range(1, count + 1):
        rows += [raw_row(seq), raw_row(seq, areaName="다른 이름")]
    return rows


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("short", "ALERT_PAGE_ROWS"),
        ("over", "ALERT_TOO_MANY_ROWS"),
        ("badmeta", "ALERT_PAGE_METADATA"),
        ("capped", "ALERT_PAGE_METADATA"),
        ("conflicts", "ALERT_QUARANTINE_LIMIT"),
    ],
)
async def test_incomplete_or_untrustworthy_provider_never_publishes(repo, kind, code):
    rows = [raw_row(i) for i in range(1, 102)]

    def handler(request):
        data, total, size = rows, 101, module.PAGE_SIZE
        if kind == "short":
            data = rows[:-1]
        if kind == "over":
            total = module.PAGE_SIZE + 1
        if kind == "capped":
            # 원천이 numOfRows를 몰래 줄이면 한 페이지가 전체라는 전제가 깨진다.
            size = 100
        if kind == "conflicts":
            # 상한(최소 10행) 바로 위: 6쌍 = 12행 격리.
            data = [*rows[50:], *_conflicting_pairs(6)]
            total = len(data)
        value = body(data, total=total, size=size)
        if kind == "badmeta":
            value["pageNo"] = True
        return response(value)

    with pytest.raises(module.AlertCollectionError, match=code) as caught:
        async with sdk(handler) as client:
            await module.run_alert_event_sync_async(repository=repo, client=client)
    run = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)[0]
    assert run.status == "failed" and run.error == code
    assert repo.list_sync_run_sources(run.run_id) == []
    if kind == "conflicts":
        assert caught.value.diagnostics["quarantined_rows"] == 12
        assert caught.value.diagnostics["quarantine_limit"] == 10


@pytest.mark.parametrize(
    ("total", "returned", "size", "code"),
    [
        # 한 요청 크기를 한 행이라도 넘으면 두 번째 페이지 없이 실패한다(누락을 숨기지 않음).
        (1001, 1000, 1000, "ALERT_TOO_MANY_ROWS"),
        # 원천이 numOfRows를 줄여 돌려주면 한 페이지가 전체라는 전제가 깨진다.
        (500, 500, 500, "ALERT_PAGE_METADATA"),
        # 요청보다 큰 numOfRows도 요청과 다른 응답이다.
        (500, 500, 10000, "ALERT_PAGE_METADATA"),
    ],
)
async def test_page_size_boundary_fails_without_second_request_or_publication(
    repo, total, returned, size, code,
):
    rows = [raw_row(i) for i in range(1, returned + 1)]
    calls = []

    def handler(request):
        calls.append((request.url.params["pageNo"], request.url.params["numOfRows"]))
        return response(body(rows, total=total, size=size))

    with pytest.raises(module.AlertCollectionError, match=code) as caught:
        async with sdk(handler) as client:
            await module.run_alert_event_sync_async(repository=repo, client=client)
    assert calls == [("1", "1000")]
    assert caught.value.diagnostics["page_size"] == size
    assert caught.value.diagnostics["total_count"] == total
    run = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)[0]
    assert run.status == "failed" and run.error == code
    assert repo.list_sync_run_sources(run.run_id) == []
    assert _stored_events(repo) == set()


async def test_a_full_measured_page_is_published_in_one_request(repo):
    rows = [raw_row(i) for i in range(1, module.PAGE_SIZE + 1)]
    calls = []

    def handler(request):
        calls.append(request.url.params["pageNo"])
        return response(body(rows))

    async with sdk(handler) as client:
        result = await module.run_alert_event_sync_async(repository=repo, client=client)
    assert calls == ["1"]
    assert result["status"] == "success" and result["events_loaded"] == module.PAGE_SIZE


def _stored_events(repo):
    from sqlalchemy import text

    with repo.engine.connect() as connection:
        return set(connection.scalars(text("SELECT event_id FROM weather_alert_events")))


def _stored_payload(repo, run_id):
    (key,) = repo.list_sync_run_sources(run_id)
    return repo.get_source_record(key)["payload"]


async def test_exact_duplicate_rows_are_merged_and_published_once(repo):
    from kortravelweather.alert_events import normalize_alert

    first, second = raw_row(1), raw_row(2)
    async with sdk(lambda _: response(body([first, second, dict(first)]))) as client:
        result = await module.run_alert_event_sync_async(repository=repo, client=client)
    assert result["status"] == "success" and result["events_loaded"] == 2
    assert result["duplicates_merged"] == 1 and result["quarantined_rows"] == 0
    assert result["collection_warning"] == ""
    assert _stored_events(repo) == {
        normalize_alert(first).event_id, normalize_alert(second).event_id,
    }
    payload = _stored_payload(repo, result["run_id"])
    assert payload["rows"] == [first, second] and payload["duplicates_merged"] == 1
    assert payload["quarantined"] == []


async def test_conflicting_rows_are_quarantined_and_the_rest_is_published(repo, caplog):
    from kortravelweather.alert_events import normalize_alert
    from kortravelweather.metrics import ALERT_ROWS_RECONCILED

    clean = raw_row(1)
    left = raw_row(2, areaCode="L1031200", areaName="당진시")
    right = raw_row(2, areaCode="L1031200", areaName="당진군")
    before = ALERT_ROWS_RECONCILED.labels(outcome="quarantined")._value.get()
    async with sdk(lambda _: response(body([left, clean, right]))) as client:
        result = await module.run_alert_event_sync_async(repository=repo, client=client)
    assert result["status"] == "success" and result["events_loaded"] == 1
    assert result["observations"] == 1
    assert result["quarantined_events"] == 1 and result["quarantined_rows"] == 2
    assert result["quarantined_area_codes"] == "L1031200"
    assert result["collection_warning"] == "ALERT_ROWS_QUARANTINED"
    assert _stored_events(repo) == {normalize_alert(clean).event_id}
    payload = _stored_payload(repo, result["run_id"])
    assert payload["rows"] == [clean]
    assert payload["quarantined"] == [
        {"event_id": normalize_alert(left).event_id, "rows": [left, right]}
    ]
    assert ALERT_ROWS_RECONCILED.labels(outcome="quarantined")._value.get() == before + 2
    assert "ALERT_ROWS_QUARANTINED events=1 rows=2 areas=L1031200" in caplog.text


async def test_zero_success_is_fresh_without_inventing_clear(repo):
    async with sdk(lambda _: response(body([], size=0))) as client:
        result = await module.run_alert_event_sync_async(repository=repo, client=client)
    assert result["status"] == "success" and result["observations"] == 0
    assert result["pages_complete"] and result["events_loaded"] == 0
    assert result["requests_fetched"] == 1


@pytest.mark.parametrize("format", ["json", "xml"])
async def test_sdk_normalized_no_data_without_page_metadata_is_complete(repo, format):
    """운영의 정상 빈 특보 응답을 같은 공개 SDK 경계로 재현한다."""
    def handler(_request):
        if format == "xml":
            return httpx.Response(200, text=(
                "<OpenAPI_ServiceResponse><cmmMsgHeader><resultCode>03</resultCode>"
                "<resultMsg>NODATA_ERROR</resultMsg></cmmMsgHeader></OpenAPI_ServiceResponse>"
            ), headers={"content-type": "application/xml"})
        return httpx.Response(200, json={"response": {
            "header": {"resultCode": "03", "resultMsg": "NODATA_ERROR"},
        }})

    async with sdk(handler) as client:
        result = await module.run_alert_event_sync_async(repository=repo, client=client)
    assert result["status"] == "success" and result["pages_complete"]
    assert result["observations"] == 0 and result["requests_fetched"] == 1
    stored = repo.list_sync_run_sources(result["run_id"])
    assert len(stored) == 1


@pytest.mark.parametrize("value,page", [
    ({"totalCount": 1, "items": {"item": []}}, 1),
    ({"totalCount": 0, "items": {"item": [raw_row()]}}, 1),
    ({"totalCount": 0, "items": {}}, 1),
    ({"totalCount": 0}, 1),
    ({"totalCount": True, "items": {"item": []}}, 1),
    ({"items": {"item": []}}, 1),
    ({"totalCount": 0, "items": {"item": []}}, 2),
    ({"totalCount": 0, "items": {"item": []}, "pageNo": True}, 1),
])
def test_no_data_exception_does_not_accept_incomplete_or_contradictory_pages(value, page):
    with pytest.raises(module.AlertCollectionError):
        module._page(value, page)


def test_metadata_failure_carries_safe_stage_and_actual_counts():
    with pytest.raises(module.AlertCollectionError) as caught:
        module._page(body([], total=1, page=2), 1)
    assert caught.value.diagnostics == {
        "error_code": "ALERT_PAGE_METADATA", "stage": "page_validation",
        "requested_page": 1, "returned_page": 2, "page_size": 1000, "total_count": 1,
    }


def test_diagnostic_metadata_drops_unknown_text_and_secret_values():
    secret = "fixture-key-not-for-diagnostics"
    error = module.AlertCollectionError("ALERT_PAGE_METADATA", diagnostics={
        "url": f"https://example.invalid?serviceKey={secret}",
        "stage": secret, "metadata_type": secret, "returned_rows": secret,
        "http_status": True, "total_count": -1,
    })
    assert error.diagnostics == {"error_code": "ALERT_PAGE_METADATA"}
    with pytest.raises(module.AlertCollectionError) as caught:
        module._page({"totalCount": secret}, 1)
    assert caught.value.diagnostics["metadata_type"] == "string"
    assert secret not in str(caught.value) + str(caught.value.diagnostics)


def test_asset_failure_retains_code_and_safe_diagnostics_for_admin(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from dagster import Failure, build_asset_context
    from kortravelweather_dagster import definitions

    repository = SimpleNamespace(engine=SimpleNamespace(dispose=Mock()))
    monkeypatch.setattr(definitions, "_run_repository", lambda _context: repository)

    def fail(**_kwargs):
        raise module.AlertCollectionError("ALERT_PAGE_METADATA", diagnostics={
            "stage": "page_validation", "requested_page": 1, "page_size": 3,
        })

    monkeypatch.setattr(definitions, "run_alert_event_sync", fail)
    resource = SimpleNamespace(create_data_client=lambda **_: object())
    with build_asset_context(resources={"kma_client": resource, "weather_repository": object()}):
        # 직접 decorated compute를 호출해 실제 op 경계의 Failure metadata를 검산한다.
        compute = definitions.kma_regional_alert_events_sync.op.compute_fn.decorated_fn
        context = SimpleNamespace(resources=SimpleNamespace(kma_client=resource))
        with pytest.raises(Failure) as caught:
            compute(context)
    assert caught.value.description == "ALERT_PAGE_METADATA"
    assert {key: value.value for key, value in caught.value.metadata.items()} == {
        "error_code": "ALERT_PAGE_METADATA", "stage": "page_validation",
        "requested_page": 1, "page_size": 3,
    }
    repository.engine.dispose.assert_called_once()


async def test_run_preserves_fixed_public_error_but_exception_has_page_diagnostics(repo):
    async with sdk(lambda _: response({"totalCount": 1, "items": {"item": []}})) as client:
        with pytest.raises(module.AlertCollectionError) as caught:
            await module.run_alert_event_sync_async(repository=repo, client=client)
    run = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)[0]
    assert run.error == "ALERT_PAGE_METADATA"
    assert caught.value.diagnostics == {
        "error_code": "ALERT_PAGE_METADATA", "stage": "page_validation",
        "metadata_field": "pageNo", "metadata_type": "null",
        "requested_page": 1, "requests_fetched": 1,
    }


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


async def test_recovery_during_request_fences_publication(repo):
    rows = [raw_row(i) for i in range(1, 102)]

    def handler(request):
        assert int(request.url.params["pageNo"]) == 1
        run = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)[0]
        repo.finish_sync_run(run.run_id, status="failed", error="recovered_elsewhere")
        return response(body(rows))

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


@pytest.mark.parametrize(
    ("mode", "stage"),
    [
        ("provider", "terminalize"),
        ("provider", "client_close"),
        ("cancel", "terminalize"),
        ("cancel", "client_close"),
        ("success", "client_close"),
    ],
)
async def test_cleanup_failures_preserve_safe_failure_cancellation_or_committed_success(
    repo,
    monkeypatch,
    mode,
    stage,
    caplog,
):
    from dagster._utils.error import serializable_error_info_from_exc_info
    from kma.exceptions import KmaAuthError

    from kortravelweather.metrics import ALERT_CLEANUP_FAILURES

    before = ALERT_CLEANUP_FAILURES.labels(stage=stage)._value.get()
    original_finish = repo.finish_sync_run

    class CleanupClient(BlockingClient):
        async def request_with_metadata(self, *args, **kwargs):
            if mode == "provider":
                raise KmaAuthError("provider fixture-private-value", failure_kind="auth")
            if mode == "success":
                return body([raw_row()]), None
            return await super().request_with_metadata(*args, **kwargs)

        async def aclose(self):
            self.closed = True
            if stage == "client_close":
                raise RuntimeError("close fixture-private-value")

    def failed_finish(*args, **kwargs):
        raise RuntimeError("database fixture-private-value")

    if stage == "terminalize":
        monkeypatch.setattr(repo, "finish_sync_run", failed_finish)
    client = CleanupClient()
    try:
        if mode == "cancel":
            task = asyncio.create_task(
                module.run_alert_event_sync_async(
                    repository=repo,
                    client=client,
                )
            )
            await asyncio.wait_for(client.entered.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif mode == "provider":
            with pytest.raises(module.AlertCollectionError, match="ALERT_PROVIDER_AUTH") as caught:
                await module.run_alert_event_sync_async(repository=repo, client=client)
            error = caught.value
            serialized = serializable_error_info_from_exc_info(
                (
                    type(error),
                    error,
                    error.__traceback__,
                )
            ).to_string()
            assert "fixture-private-value" not in serialized
            assert "ALERT_PROVIDER_AUTH" in serialized
        else:
            result = await module.run_alert_event_sync_async(repository=repo, client=client)
            assert result["status"] == "success" and result["events_loaded"] == 1
            run = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)[0]
            assert run.status == "success" and run.values_loaded == 1
        assert client.closed and "fixture-private-value" not in caplog.text
        assert ALERT_CLEANUP_FAILURES.labels(stage=stage)._value.get() == before + 1
    finally:
        runs = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)
        if runs:
            original_finish(runs[0].run_id, status="failed", error="fixture_cleanup")


async def test_hanging_client_close_does_not_reverse_committed_success(repo, monkeypatch):
    from kortravelweather.metrics import ALERT_CLEANUP_FAILURES

    class SlowCloseClient(BlockingClient):
        async def request_with_metadata(self, *args, **kwargs):
            return body([]), None

        async def aclose(self):
            self.entered.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(module, "CLEANUP_TIMEOUT_SECONDS", 0.05)
    before = ALERT_CLEANUP_FAILURES.labels(stage="client_close")._value.get()
    client = SlowCloseClient()
    result = await asyncio.wait_for(
        module.run_alert_event_sync_async(repository=repo, client=client),
        timeout=5,
    )
    assert result["status"] == "success" and client.entered.is_set()
    assert ALERT_CLEANUP_FAILURES.labels(stage="client_close")._value.get() == before + 1


@pytest.mark.parametrize("mode", ["provider", "cancel"])
async def test_row_locked_terminalization_returns_original_outcome_before_lock_release(
    repo,
    monkeypatch,
    mode,
):
    from kma.exceptions import KmaAuthError
    from sqlalchemy import text

    from kortravelweather.metrics import ALERT_CLEANUP_FAILURES

    monkeypatch.setattr(module, "TERMINAL_TIMEOUT_MS", 100, raising=False)
    locked = asyncio.Event()
    released = False
    blocker = transaction = None
    original_finish = repo.finish_sync_run

    class RowLockedClient(BlockingClient):
        async def request_with_metadata(self, *args, **kwargs):
            nonlocal blocker, transaction
            run = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)[0]
            blocker = repo.engine.connect()
            transaction = blocker.begin()
            blocker.execute(
                text("SELECT run_id FROM weather_sync_runs WHERE run_id=:id FOR UPDATE"),
                {"id": run.run_id},
            )
            locked.set()
            if mode == "provider":
                raise KmaAuthError("fixture-private-lock", failure_kind="auth")
            await asyncio.Event().wait()

    def unlock():
        nonlocal released
        if not released and transaction is not None:
            released = True
            transaction.rollback()
            blocker.close()

    client = RowLockedClient()
    before = ALERT_CLEANUP_FAILURES.labels(stage="terminalize")._value.get()
    task = asyncio.create_task(module.run_alert_event_sync_async(repository=repo, client=client))
    await asyncio.wait_for(locked.wait(), timeout=5)
    delayed_release = asyncio.get_running_loop().call_later(1.5, unlock)
    try:
        if mode == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(module.AlertCollectionError, match="ALERT_PROVIDER_AUTH"):
                await task
        assert not released, "종결 SQL이 잠금 해제까지 기다리면 원래 결과 반환이 지연된다"
        assert client.closed
        assert ALERT_CLEANUP_FAILURES.labels(stage="terminalize")._value.get() == before + 1
        run = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)[0]
        assert run.status == "running"
    finally:
        delayed_release.cancel()
        unlock()
        runs = repo.list_sync_runs(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)
        if runs:
            original_finish(runs[0].run_id, status="failed", error="fixture_cleanup")
