"""지도 요청 폭주가 인증 worker를 고갈시키거나 취소된 배치를 재시작하지 않는지 검증."""

import asyncio
from threading import Event, Lock

import anyio
import httpx
import pytest
from kortravelweather_api.app import create_app
from starlette.requests import Request

from kortravelweather.models import WeatherLocation
from kortravelweather.settings import WeatherSettings


class Repository:
    def __init__(self):
        self.gate = Event()
        self.lock = Lock()
        self.calls = 0
        self.active = 0
        self.peak = 0
        self.fail = False
        self.started = anyio.Event()

    def create_schema(self):
        pass

    def get_locations_by_ids(self, ids, **kwargs):
        return [WeatherLocation(location_id=key, name=key, latitude=37, longitude=127)
                for key in ids]

    def marker_values_many(self, ids, **kwargs):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
        if self.active == 2:
            anyio.from_thread.run_sync(self.started.set)
        try:
            assert self.gate.wait(10), "시험 marker gate를 해제하지 못했습니다."
            if self.fail:
                raise RuntimeError("fixture failure")
            return {key: [] for key in ids}
        finally:
            with self.lock:
                self.active -= 1

    def is_admin_session_revoked(self, session):
        return False


def app_for(repo, database_url):
    return create_app(settings=WeatherSettings(
        env="development", database_url=database_url, admin_token="test-token",
    ), repository=repo)


@pytest.mark.asyncio
async def test_marker_admission_reserves_auth_workers_and_skips_disconnected_queue(
    weather_test_database_url, monkeypatch,
):
    repo = Repository()
    app = app_for(repo, weather_test_database_url)

    async def disconnected(request):
        return request.headers.get("x-test-disconnected") == "true"

    monkeypatch.setattr(Request, "is_disconnected", disconnected)
    default = anyio.to_thread.current_default_thread_limiter()
    original_tokens = default.total_tokens
    default.total_tokens = 2
    tasks = []
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://test") as client:
            for key in ("one", "two"):
                tasks.append(asyncio.create_task(client.get(
                    "/v1/weather/markers", params={"location_id": key},
                )))
            with anyio.fail_after(3):
                await repo.started.wait()
            tasks.append(asyncio.create_task(client.get(
                "/v1/weather/markers", params={"location_id": "cancelled"},
                headers={"x-test-disconnected": "true"},
            )))
            # 일반 worker 둘이 모두 점유돼도 인증 확인은 별도 limiter에서 완료한다.
            rejected = await asyncio.wait_for(client.post(
                "/v1/admin/session-revocations/check", json={"session": "fixture-session"},
                headers={"x-admin-token": "wrong-token"},
            ), timeout=3)
            assert rejected.status_code == 401
            auth = await asyncio.wait_for(client.post(
                "/v1/admin/session-revocations/check", json={"session": "fixture-session"},
                headers={"x-admin-token": "test-token"},
            ), timeout=3)
            assert auth.status_code == 200 and auth.json() == {"revoked": False}
            invalid = await asyncio.wait_for(client.get(
                "/v1/weather/markers", params=[("location_id", "one")] * 501,
            ), timeout=3)
            assert invalid.status_code == 422
            assert repo.calls == repo.peak == 2
            repo.gate.set()
            responses = await asyncio.gather(*tasks)
            assert [response.status_code for response in responses] == [200, 200, 499]
            assert repo.calls == 2
    finally:
        repo.gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        default.total_tokens = original_tokens


@pytest.mark.asyncio
async def test_marker_failure_releases_admission_for_the_next_batch(weather_test_database_url):
    repo = Repository()
    repo.gate.set()
    repo.fail = True
    app = app_for(repo, weather_test_database_url)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test",
    ) as client:
        assert (await client.get("/v1/weather/markers?location_id=one")).status_code == 500
        repo.fail = False
        assert (await client.get("/v1/weather/markers?location_id=one")).status_code == 200
        assert app.state.marker_limiter.borrowed_tokens == 0


@pytest.mark.asyncio
async def test_real_disconnect_reaches_marker_admission_through_request_middleware(
    weather_test_database_url,
):
    """monkeypatch 없이 실제 ASGI http.disconnect가 middleware를 지나 499로 끝난다."""
    repo = Repository()
    repo.gate.set()
    app = app_for(repo, weather_test_database_url)
    messages = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": "/v1/weather/markers", "raw_path": b"/v1/weather/markers",
        "root_path": "", "query_string": b"location_id=one", "headers": [(b"host", b"test")],
        "client": ("127.0.0.1", 50000), "server": ("test", 80),
    }, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    assert start["status"] == 499
    assert repo.calls == 0
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://test") as client:
        echoed = await client.get("/health", headers={"x-request-id": "trace-echo"})
        generated = await client.get("/v1/weather/markers", params={"location_id": "one"})
    assert echoed.status_code == 200 and echoed.headers["x-request-id"] == "trace-echo"
    assert int(echoed.headers["x-duration-ms"]) >= 0
    assert generated.status_code == 200 and generated.headers["x-request-id"]
    assert repo.calls == 1
