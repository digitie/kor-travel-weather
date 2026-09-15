"""redirect의 인증·요청 의미·취소 시 자원 정리를 검증한다."""

import asyncio

import httpx
import pytest

from airkorea._httpx import send_after_token
from airkorea._ratelimit import AsyncTokenBucket

pytestmark = pytest.mark.asyncio


async def test_cross_origin_redirect_does_not_reapply_default_basic_auth():
    seen = []

    async def handler(request):
        seen.append((request.url.host, request.headers.get("authorization")))
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": "https://second.invalid/final"})
        return httpx.Response(200)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        auth=("fake-user", "fake-password"),
        follow_redirects=True,
    ) as client:
        bucket = AsyncTokenBucket(1000)
        await bucket.acquire()
        response = await send_after_token(
            client, client.build_request("GET", "https://first.invalid/start"), bucket
        )
    assert seen[0][1] is not None
    assert seen[1] == ("second.invalid", None)
    assert len(response.history) == 1


async def test_same_origin_307_preserves_auth_method_and_body():
    seen = []

    async def handler(request):
        seen.append((request.method, request.content, request.headers.get("authorization")))
        if len(seen) == 1:
            return httpx.Response(307, headers={"location": "/final"})
        return httpx.Response(200)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        auth=("fake-user", "fake-password"),
        follow_redirects=True,
    ) as client:
        bucket = AsyncTokenBucket(1000)
        await bucket.acquire()
        await send_after_token(
            client,
            client.build_request("POST", "https://first.invalid/start", content=b"body"),
            bucket,
        )
    assert seen[0] == seen[1]
    assert seen[1][0:2] == ("POST", b"body")


async def test_redirect_limit_closes_response_and_stops_sending():
    responses = []

    async def handler(request):
        response = httpx.Response(302, headers={"location": "/again"})
        responses.append(response)
        return response

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True, max_redirects=1
    ) as client:
        bucket = AsyncTokenBucket(1000)
        await bucket.acquire()
        with pytest.raises(httpx.TooManyRedirects):
            await send_after_token(
                client,
                client.build_request("GET", "https://first.invalid/start"),
                bucket,
                stream=True,
            )
    assert len(responses) == 2
    assert all(response.is_closed for response in responses)


async def test_cancelled_redirect_wait_closes_previous_response_and_does_not_send():
    responses = []
    sent = asyncio.Event()

    async def handler(request):
        response = httpx.Response(302, headers={"location": "/final"})
        responses.append(response)
        sent.set()
        return response

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        bucket = AsyncTokenBucket(0.1)
        await bucket.acquire()
        task = asyncio.create_task(
            send_after_token(
                client,
                client.build_request("GET", "https://first.invalid/start"),
                bucket,
                stream=True,
            )
        )
        await sent.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(responses) == 1
    assert responses[0].is_closed
