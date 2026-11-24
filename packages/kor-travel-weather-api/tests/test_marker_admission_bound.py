"""마커 자리 대기 상한(호출 검토 B11): 넘으면 503·``retry-after``로 곧바로 끝난다."""

from __future__ import annotations

import asyncio
from time import monotonic

import anyio
import anyio.lowlevel
import httpx
import pytest
from kortravelweather_api.routers import weather
from test_public_read_path import _GatedMarkers, _marker_app


@pytest.mark.asyncio
async def test_marker_admission_wait_is_bounded_with_503_retry_after(
    weather_test_database_url, monkeypatch,
):
    monkeypatch.setattr(weather, "MARKER_ADMISSION_SECONDS", .2)
    repo = _GatedMarkers()
    app = _marker_app(repo, weather_test_database_url)
    tasks = []
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://test") as client:
            tasks = [asyncio.create_task(client.get(
                "/v1/weather/markers", params={"location_id": key},
            )) for key in ("one", "two")]
            with anyio.fail_after(3):
                await repo.started.wait()
            started = monotonic()
            busy = await client.get("/v1/weather/markers", params={"location_id": "three"})
            elapsed = monotonic() - started
            assert busy.status_code == 503
            assert busy.json()["code"] == "MARKER_BUSY"
            assert busy.headers["retry-after"] == "1"
            assert busy.headers["content-type"].startswith("application/problem+json")
            assert .2 <= elapsed < 2
            # 거절된 요청은 DB 읽기를 시작하지 않고 자리도 남기지 않는다.
            assert repo.calls == 2
            assert app.state.marker_limiter.borrowed_tokens == 2
            assert app.state.marker_limiter.statistics().tasks_waiting == 0
            repo.gate.set()
            assert [r.status_code for r in await asyncio.gather(*tasks)] == [200, 200]
        assert app.state.marker_limiter.borrowed_tokens == 0
    finally:
        repo.gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_marker_slot_freed_within_the_bound_is_still_served(
    weather_test_database_url, monkeypatch,
):
    """상한 안에 자리가 나면 예전처럼 기다렸다가 처리한다(자리 수는 늘리지 않는다)."""
    monkeypatch.setattr(weather, "MARKER_ADMISSION_SECONDS", 3)
    repo = _GatedMarkers()
    app = _marker_app(repo, weather_test_database_url)
    tasks = []
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://test") as client:
            tasks = [asyncio.create_task(client.get(
                "/v1/weather/markers", params={"location_id": key},
            )) for key in ("one", "two")]
            with anyio.fail_after(3):
                await repo.started.wait()
            queued = asyncio.create_task(
                client.get("/v1/weather/markers", params={"location_id": "three"}),
            )
            with anyio.fail_after(3):
                while app.state.marker_limiter.statistics().tasks_waiting != 1:
                    await anyio.lowlevel.checkpoint()
            repo.gate.set()
            responses = await asyncio.gather(*tasks, queued)
            assert [response.status_code for response in responses] == [200, 200, 200]
        assert repo.calls == 3
        assert app.state.marker_limiter.borrowed_tokens == 0
    finally:
        repo.gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)
