from __future__ import annotations

import asyncio

import httpx
import pytest

from airkorea import AirKoreaClient, run_debug_method
from airkorea._http import _MAX_EXCHANGES
from tests.conftest import payload


@pytest.mark.parametrize("fail_first", [False, True])
async def test_concurrent_debug_calls_capture_only_their_own_response(fail_first: bool) -> None:
    first_started = asyncio.Event()
    second_finished = asyncio.Event()

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("stationName") == "first":
            first_started.set()
            await second_finished.wait()
            if fail_first:
                raise httpx.ConnectError("first request failed")
        return httpx.Response(200, json=payload([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as session:
        async with AirKoreaClient(service_key="test-key", session=session, retries=0) as client:
            first_task = asyncio.create_task(
                run_debug_method(client, "stations", {"station_name": "first"})
            )
            await first_started.wait()
            second = await run_debug_method(client, "sido_measurements", {"sido_name": "서울"})
            second_finished.set()
            first = await first_task
            assert client._http._exchange_scope.get() is None

    assert second.error is None
    assert second.request["query"]["sidoName"] == "서울"
    if fail_first:
        assert first.error is not None
        assert first.request == {}
        assert first.response == {}
    else:
        assert first.error is None
        assert first.request["query"]["stationName"] == "first"
        assert len([line for line in first.trace if "HTTP 200" in line]) == 1


async def test_debug_capture_survives_full_history_buffer() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload([]))),
    ) as session:
        async with AirKoreaClient(service_key="test-key", session=session) as client:
            await client.stations(station_name="old")
            previous = client._http.exchanges[-1]
            client._http._exchanges.extend([previous] * _MAX_EXCHANGES)
            run = await run_debug_method(client, "stations", {"station_name": "new"})
            assert run.request["query"]["stationName"] == "new"
            assert run.response["status_code"] == 200
            assert len(client._http.exchanges) == _MAX_EXCHANGES


async def test_echoed_key_in_plain_auth_error_is_removed_from_debug_record() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text="SERVICE_KEY_IS_NOT_REGISTERED_ERROR test-secret",
            )
        ),
    ) as session:
        async with AirKoreaClient(service_key="test-secret", session=session) as client:
            run = await run_debug_method(client, "stations")
            assert run.error is not None
            assert "test-secret" not in repr(run)
            assert "test-secret" not in repr(client._http.exchanges)
