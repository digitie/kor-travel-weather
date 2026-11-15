"""정확 SDK 핀의 제한 응답을 실제 공개 client→격자 게시→asset 진단으로 소비한다."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from dagster import Failure, build_asset_context
from kma import AsyncTokenBucket, KmaClient, _http
from kortravelweather_dagster import definitions
from test_kma_weather import FakeRepository, _target


class CountingBucket(AsyncTokenBucket):
    def __init__(self):
        super().__init__(20)
        self.count = 0

    async def acquire(self):
        await super().acquire()
        self.count += 1


def limited(code, format_):
    if format_ == "json":
        return httpx.Response(200, headers={"Retry-After": "2"}, json={
            "response": {"header": {
                "resultCode": code, "resultMsg": "serviceKey=fixture-private-sdk-key",
            }},
        })
    return httpx.Response(200, headers={"Retry-After": "2"}, content=(
        '\ufeff <?xml version="1.0" encoding="UTF-8"?>'
        '<g:OpenAPI_ServiceResponse xmlns:g="urn:gateway"><g:cmmMsgHeader>'
        '<g:returnAuthMsg>serviceKey=fixture-private-sdk-key</g:returnAuthMsg>'
        f'<g:returnReasonCode>{code}</g:returnReasonCode>'
        '</g:cmmMsgHeader></g:OpenAPI_ServiceResponse>'
    ).encode())


def normal(request):
    query = request.url.params
    row = {
        "baseDate": query["base_date"], "baseTime": query["base_time"],
        "nx": int(query["nx"]), "ny": int(query["ny"]), "category": "T1H", "obsrValue": "23",
    }
    return httpx.Response(200, json={"response": {
        "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
        "body": {"pageNo": 1, "numOfRows": 1000, "totalCount": 1, "items": {"item": [row]}},
    }})


def run_asset(monkeypatch, handler):
    repository = FakeRepository()
    bucket = CountingBucket()
    session = httpx.AsyncClient(transport=httpx.MockTransport(handler(bucket)))
    client = KmaClient("fixture-private-sdk-key", session=session, rate_limiter=bucket, retries=2)
    monkeypatch.setattr(definitions, "_run_repository", lambda _: repository)
    monkeypatch.setattr(definitions, "_kma_grid_targets", lambda *args, **kwargs: (
        [_target()], {}, set(), None,
    ))
    resource = SimpleNamespace(create_client=lambda **kwargs: client)

    def execute():
        try:
            with build_asset_context(resources={
                "kma_client": resource, "weather_repository": repository,
            }) as context:
                return definitions.kma_ultra_short_nowcast_sync(context)
        finally:
            assert client.closed  # 앱의 기존 async 소유권·종료 경계도 통과했다.
            asyncio.run(session.aclose())

    return repository, bucket, execute


@pytest.mark.parametrize("format_", ["json", "gateway_xml"])
def test_temporary_sdk_23_recovery_publishes_one_source_and_value_once(monkeypatch, format_):
    attempts, delays = [], []

    async def bounded_test_wait(delay):
        delays.append(delay)

    # SDK 모듈의 대기만 시험에서 대체한다. 앱/SDK 제품 backoff와 전역 asyncio는 유지한다.
    monkeypatch.setattr(_http, "asyncio", SimpleNamespace(sleep=bounded_test_wait))

    def handler(bucket):
        def respond(request):
            attempts.append(bucket.count)
            return limited("23", format_) if len(attempts) == 1 else normal(request)
        return respond

    repository, bucket, execute = run_asset(monkeypatch, handler)
    result = execute()
    assert attempts == [1, 2] and bucket.count == 2 and delays == [2.0]
    assert result["status"] == result["collection_outcome"] == "success"
    assert result["requests_fetched"] == result["grids_fetched"] == result["completed_grids"] == 1
    assert result["values_loaded"] == len(repository.values) == 1
    assert len(repository.sources) == 1
    assert repository.values[0].value_number == 23
    assert repository.runs[-1].status == "success"


@pytest.mark.parametrize("code,scope,attempt_count,retryable,hint", [
    ("22", "daily", 1, False, "WAIT_QUOTA_RESET"),
    ("23", "per_second", 3, True, "RETRY_AFTER_BACKOFF"),
])
def test_sdk_limit_exhaustion_keeps_single_retry_budget_and_safe_asset_metadata(
    monkeypatch, code, scope, attempt_count, retryable, hint,
):
    from dagster._utils.error import serializable_error_info_from_exc_info

    attempts, delays = [], []

    async def bounded_test_wait(delay):
        delays.append(delay)

    monkeypatch.setattr(_http, "asyncio", SimpleNamespace(sleep=bounded_test_wait))

    def handler(bucket):
        def respond(request):
            attempts.append(bucket.count)
            return limited(code, "json")
        return respond

    repository, bucket, execute = run_asset(monkeypatch, handler)
    with pytest.raises(Failure) as caught:
        execute()
    exc = caught.value
    metadata = {key: value.value for key, value in exc.metadata.items()}
    assert attempts == list(range(1, attempt_count + 1)) and bucket.count == attempt_count
    assert delays == [2.0] * (attempt_count - 1)
    assert repository.runs[-1].status == "failed"
    assert not repository.sources and not repository.values
    assert exc.description == metadata["error_code"] == "PROVIDER_RATE_LIMIT"
    assert metadata["rate_limit_scope"] == scope
    assert metadata["retryable"] is retryable and metadata["recovery_hint"] == hint
    assert metadata["stage"] == "provider_fetch"
    # SDK의 API-envelope 오류는 status_code=None이다. 앱이 HTTP 값을 추정하지 않는다.
    assert "http_status" not in metadata
    assert metadata["published_values"] == metadata["completed_grids"] == 0
    serialized = serializable_error_info_from_exc_info((type(exc), exc, exc.__traceback__))
    assert serialized.cause is None and serialized.context is None
    assert "fixture-private-sdk-key" not in serialized.to_string() + str(metadata)
