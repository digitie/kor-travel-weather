"""공개 읽기 경로(호출 검토 B3·B9, gzip) 회귀 시험.

- B3: DB 읽기·변환·직렬화가 event loop 밖 worker thread 하나에서 끝나고, 본문은 예전
  FastAPI ``response_model`` 경로가 만들던 bytes와 같다. OpenAPI 무변경은
  ``test_weather_api.test_checked_in_openapi_matches_runtime``이 확인한다.
- B9: handler 하나의 읽기가 연결 하나·transaction 하나를 쓰고 마감 설정은 한 번 건다.
- gzip: 허용·크기 조건에서만 worker thread에서 압축하고 ``Vary: Accept-Encoding``을 붙인다.
"""

from __future__ import annotations

import gzip
import json
import re
import threading

import anyio
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kortravelweather_api import public_response, read_limits
from kortravelweather_api.app import create_app
from kortravelweather_api.routers import alert_events, weather
from sqlalchemy import event
from test_public_read_limits import _client, _seed

from kortravelweather.models import WeatherLocation
from kortravelweather.settings import WeatherSettings

#: 공개 handler와 그 문서화된 응답 모델.
ENDPOINTS = [
    ("/v1/weather/regions", weather.RegionLocationsResponse),
    ("/v1/weather/locations", weather.LocationListResponse),
    ("/v1/weather/locations/air-a", weather.LocationResponse),
    ("/v1/weather/locations/air-a/latest", weather.WeatherValueListResponse),
    ("/v1/weather/locations/kma-a/forecast?history=true", weather.WeatherValueListResponse),
    ("/v1/weather/nearby?lat=37.5&lon=127.0&radius_km=20", weather.NearbyListResponse),
    ("/v1/weather/resolve?lat=37.5&lon=127.0", weather.ResolvedWeatherResponse),
    ("/v1/weather/markers?location_id=air-a&location_id=kma-a",
     weather.WeatherMarkerListResponse),
    ("/v1/weather/markers?location_id=air-a&location_id=kma-a&kind=observed&kind=short"
     "&kind=alert", weather.WeatherMarkerListResponse),
    ("/v1/weather/alert-events?bjd_code=11", alert_events.AlertEventsResponse),
]


def _legacy_bytes(model, body: bytes) -> bytes:
    """예전 경로: handler가 JSON 값 dict를 돌려주면 FastAPI가 response_model로 검증·직렬화."""
    legacy = FastAPI()
    content = json.loads(body)

    @legacy.get("/legacy", response_model=model)
    async def legacy_route():
        return content

    return TestClient(legacy).get("/legacy").content


def test_public_bodies_match_the_previous_response_model_serialization(repository):
    _seed(repository)
    client = _client(repository)
    for path, model in ENDPOINTS:
        response = client.get(path, headers={"accept-encoding": "identity"})
        assert response.status_code == 200, (path, response.text)
        assert response.headers["content-type"] == "application/json"
        assert "content-encoding" not in response.headers
        assert response.headers["vary"] == "Accept-Encoding"
        assert response.content == _legacy_bytes(model, response.content), path


def _without_clock(body: bytes) -> bytes:
    body = re.sub(rb'"generated_at":"[^"]*"', b'"generated_at":""', body)
    return re.sub(rb'"duration_ms":\d+', b'"duration_ms":0', body)


def test_gzip_only_when_accepted_and_large_and_decompresses_to_the_same_body(repository):
    _seed(repository)
    client = _client(repository)
    path = "/v1/weather/locations/kma-a/forecast"
    plain = client.get(path, headers={"accept-encoding": "identity", "x-request-id": "same"})
    assert len(plain.content) >= public_response.GZIP_MINIMUM_BYTES
    with client.stream(
        "GET", path, headers={"accept-encoding": "gzip", "x-request-id": "same"},
    ) as compressed:
        raw = b"".join(compressed.iter_raw())
        assert compressed.headers["content-encoding"] == "gzip"
        assert compressed.headers["vary"] == "Accept-Encoding"
        assert int(compressed.headers["content-length"]) == len(raw)
    assert len(raw) < len(plain.content)
    assert _without_clock(gzip.decompress(raw)) == _without_clock(plain.content)
    # q=0은 거부다.
    refused = client.get(path, headers={"accept-encoding": "gzip;q=0, identity"})
    assert "content-encoding" not in refused.headers
    # 임계값보다 작은 본문은 압축하지 않는다.
    small = client.get("/v1/weather/locations/air-a", headers={"accept-encoding": "gzip"})
    assert len(small.content) < public_response.GZIP_MINIMUM_BYTES
    assert "content-encoding" not in small.headers
    assert small.headers["vary"] == "Accept-Encoding"


@pytest.mark.parametrize(("header", "expected"), [
    ("gzip", True), ("GZIP, br", True), ("br;q=1.0, gzip;q=0.5", True),
    ("gzip;q=0", False), ("gzip; q=0.0", False), ("identity", False), ("", False),
    ("gzip;q=x", False), ("x-gzip", False),
])
def test_accepts_gzip(header, expected):
    assert public_response.accepts_gzip(header) is expected


def test_public_handler_uses_one_connection_and_arms_limits_once(repository, monkeypatch):
    _seed(repository)
    client = _client(repository)
    checkouts: list[int] = []
    arms: list[float] = []
    original = read_limits._needs_arm

    def counting(armed, wanted):
        result = original(armed, wanted)
        if result:
            arms.append(wanted)
        return result

    # 느린 CI에서도 transaction당 횟수만 보도록 재설정 여유를 넉넉히 둔다(여유 판정은 따로 시험).
    monkeypatch.setattr(read_limits, "_REARM_SLACK_SECONDS", 10.0)
    monkeypatch.setattr(read_limits, "_needs_arm", counting)
    listener = lambda *args: checkouts.append(1)  # noqa: E731
    event.listen(repository.engine, "checkout", listener)
    try:
        for path, _ in ENDPOINTS:
            if "regions" in path or "alert-events" in path or path.endswith("air-a"):
                continue  # 읽기 함수 하나뿐인 handler
            checkouts.clear()
            arms.clear()
            assert client.get(path).status_code == 200, path
            # 예전: 읽기 함수마다 checkout·pre-ping·transaction·문장마다 SET LOCAL.
            assert len(checkouts) == 1, path
            assert len(arms) == 1, path
    finally:
        event.remove(repository.engine, "checkout", listener)
    assert repository.engine.pool.checkedout() == 0


class _GatedMarkers:
    """두 마커 묶음이 자리를 잡고 gate가 열릴 때까지 쥔다."""

    def __init__(self):
        self.gate = threading.Event()
        self.calls = 0
        self.lock = threading.Lock()
        self.started = anyio.Event()
        self.threads: list[int] = []

    def create_schema(self):
        pass

    def get_locations_by_ids(self, ids, **kwargs):
        self.threads.append(threading.get_ident())
        return [WeatherLocation(location_id=key, name=key, latitude=37, longitude=127)
                for key in ids]

    def marker_values_many(self, ids, **kwargs):
        self.threads.append(threading.get_ident())
        with self.lock:
            self.calls += 1
            calls = self.calls
        if calls == 2:
            anyio.from_thread.run_sync(self.started.set)
        assert self.gate.wait(10), "시험 gate를 열지 못했습니다."
        return {key: [] for key in ids}


def _marker_app(repo, database_url):
    return create_app(settings=WeatherSettings(
        env="development", database_url=database_url, admin_token="test-token",
    ), repository=repo)


@pytest.mark.asyncio
async def test_marker_reads_and_serialization_share_one_worker_thread_off_the_loop(
    weather_test_database_url, monkeypatch,
):
    repo = _GatedMarkers()
    repo.gate.set()
    app = _marker_app(repo, weather_test_database_url)
    loop_thread = threading.get_ident()
    render_threads: list[int] = []
    original = weather.render

    def recording(*args, **kwargs):
        render_threads.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(weather, "render", recording)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://test") as client:
        response = await client.get("/v1/weather/markers", params={"location_id": "one"})
    assert response.status_code == 200
    assert response.json()["data"][0]["location_id"] == "one"
    assert len(render_threads) == 1
    assert set(repo.threads) == set(render_threads)
    assert loop_thread not in render_threads
