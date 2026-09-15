from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from airkorea import AirKoreaClient, AsyncTokenBucket, run_debug_method
from tests.conftest import payload


async def test_public_client_shares_tokens_across_retry_redirect_and_services() -> None:
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if len(sent) == 1:
            raise httpx.ConnectError("temporary")
        if len(sent) == 2:
            return httpx.Response(302, headers={"location": "/redirected"})
        return httpx.Response(200, json=payload([]))

    bucket = AsyncTokenBucket(100)
    acquire = AsyncMock(wraps=bucket.acquire)
    bucket.acquire = acquire  # type: ignore[method-assign]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True,
    ) as session:
        async with AirKoreaClient(
            service_key="test-secret", session=session, retry_backoff=0, rate_limiter=bucket,
        ) as client:
            await client.stations()
            run = await run_debug_method(client, "sido_measurements", {"sido_name": "서울"})
            assert run.error is None
            assert "test-secret" not in repr(run)
    assert len(sent) == acquire.await_count == 4


async def test_public_concurrent_requests_obey_configured_tps() -> None:
    sent: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(time.monotonic())
        return httpx.Response(200, json=payload([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as session:
        async with AirKoreaClient(
            service_key="test-secret", session=session,
            rate_limiter=AsyncTokenBucket(20, capacity=1),
        ) as client:
            await asyncio.gather(*(client.stations() for _ in range(5)))
    assert len(sent) == 5
    assert sent[-1] - sent[0] >= 0.195


def test_sync_session_rejected_before_it_can_block() -> None:
    class SyncSession:
        def get(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("synchronous session was executed")

    with pytest.raises(TypeError, match="session.get must be async"):
        AirKoreaClient(service_key="test-secret", session=SyncSession())  # type: ignore[arg-type]


@pytest.mark.parametrize("max_rps", [0, -1, float("inf"), float("nan"), True])
def test_invalid_max_rps_rejected_at_construction(max_rps: float) -> None:
    with pytest.raises(ValueError, match="max_rps"):
        AirKoreaClient(service_key="test-secret", max_rps=max_rps)


async def test_sole_public_client_has_async_lifecycle() -> None:
    async with AirKoreaClient(service_key="test-secret") as client:
        assert not hasattr(client, "close")
        assert not hasattr(client, "aio")
    assert client.closed
    await client.aclose()
