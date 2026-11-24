"""``/resolve`` 기준 위치·원천 후보 선택을 SQL로 옮긴 뒤에도 예전 Python 선택과 같다(B10)."""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from kortravelweather_api.app import create_app
from kortravelweather_api.routers import weather
from sqlalchemy import event

from kortravelweather import repository as repository_module
from kortravelweather.models import WeatherLocation
from kortravelweather.settings import WeatherSettings

#: 측정점 판정의 경계 사례. 앞의 둘과 맨 끝 넷 중 공백만인 이름을 뺀 셋이 측정점이다. NUL은
#: PostgreSQL ``json``의 ``->``가 풀지 못하는 이스케이프라 따로 둔다.
METADATA = [
    {"measurement_point": {"station_name": "중구", "provider": "python-airkorea-api"}},
    {"measurement_point": {"station_name": " 종로　"}},
    {"measurement_point": {"station_name": "　\t   "}},
    {"measurement_point": {"station_name": ""}},
    {"measurement_point": {"station_name": 123}},
    {"measurement_point": {"station_name": None}},
    {"measurement_point": {"provider": "python-airkorea-api"}},
    {"measurement_point": "중구"},
    {"measurement_point": ["중구"]},
    {"measurement_point": None},
    {"note": "kma grid"},
    {},
    {"measurement_point": {"station_name": "a\x00b"}},
    {"measurement_point": {"station_name": " \x00 "}},
    # 다른 키의 NUL(이름은 공백만), 글자 그대로의 백슬래시+u0000(측정점).
    {"note": "x\x00", "measurement_point": {"station_name": " "}},
    {"measurement_point": {"station_name": "\\u0000"}},
]


def _seed_catalog(repository) -> None:
    rng = random.Random(20261124)
    for index in range(180):
        latitude = round(37.5 + rng.uniform(-0.12, 0.12), 6)
        longitude = round(127.0 + rng.uniform(-0.12, 0.12), 6)
        if index % 13 == 0:
            # 같은 좌표(거리 동률)는 location_id 순서로 갈린다.
            latitude, longitude = 37.52, 127.03
        repository.create_location(WeatherLocation(
            location_id=f"loc-{index:03d}",
            name=f"위치 {index}",
            latitude=latitude,
            longitude=longitude,
            enabled=index % 11 != 5,
            metadata=METADATA[index % len(METADATA)],
        ))


def _python_selection(repository, lat, lon, radius_km):
    """예전 handler 선택(반경 안 1만 행을 읽어 Python에서 고름)."""
    reference = SimpleNamespace(nearest_locations=repository.nearest_locations)
    return weather._resolve_anchor(reference, lat, lon, radius_km=radius_km)  # noqa: SLF001


@pytest.mark.parametrize(("lat", "lon", "radius_km"), [
    (37.5, 127.0, 100),
    (37.52, 127.03, 100),
    (37.52, 127.03, 0.001),
    (37.6, 127.1, 3),
    (37.45, 126.92, 2),
    (37.5, 127.0, 7),
    (37.3, 126.8, 20),
    (37.3, 126.8, 1),
    (33.3, 126.3, 5),
])
def test_sql_resolve_selection_matches_python_selection(repository, lat, lon, radius_km):
    _seed_catalog(repository)
    expected = _python_selection(repository, lat, lon, radius_km)
    actual = repository.resolve_locations(lat, lon, radius_km=radius_km)
    assert actual == expected


def test_station_without_a_visible_name_falls_back_to_the_nearest_location(repository):
    repository.create_location(WeatherLocation(
        location_id="blank-station", name="공백", latitude=37.5, longitude=127.0,
        metadata={"measurement_point": {"station_name": "　 "}},
    ))
    repository.create_location(WeatherLocation(
        location_id="kma", name="KMA", latitude=37.501, longitude=127.0,
    ))
    repository.create_location(WeatherLocation(
        location_id="far-station", name="먼 측정소", latitude=37.6, longitude=127.0,
        metadata={"measurement_point": {"station_name": "먼 곳"}},
    ))
    (anchor, distance), candidates = repository.resolve_locations(37.5, 127.0, radius_km=5)
    # 반경 5km 안에 측정점이 없으므로 가장 가까운 위치가 기준이고, 측정점이 아닌 것만 후보다.
    assert anchor.location_id == "blank-station" and distance == 0
    assert [candidate.location_id for candidate, _ in candidates] == ["kma"]
    (anchor, _), candidates = repository.resolve_locations(37.5, 127.0, radius_km=50)
    assert anchor.location_id == "far-station"
    # 후보 반경은 기준 거리 + 5km라 측정점이 아닌 두 위치가 모두 들어간다.
    assert [candidate.location_id for candidate, _ in candidates] == ["blank-station", "kma"]
    assert repository.resolve_locations(33.2, 126.2, radius_km=1) is None


def test_station_name_with_nul_escape_does_not_break_resolve(repository):
    """관리 API metadata로 들어온 ``\\u0000``이 반경 전체의 /resolve를 22P05로 막지 않는다."""
    repository.create_location(WeatherLocation(
        location_id="st-nul", name="NUL 측정소", latitude=37.53, longitude=127.0,
        metadata={"measurement_point": {"station_name": "a\x00b"}},
    ))
    repository.create_location(WeatherLocation(
        location_id="kma", name="KMA", latitude=37.5, longitude=127.0,
        metadata={"note": "\x00"},
    ))
    selected = repository.resolve_locations(37.5, 127.0, radius_km=100)
    assert selected == _python_selection(repository, 37.5, 127.0, 100)
    (anchor, _), candidates = selected
    # NUL은 공백이 아니므로 예전 Python 판정처럼 측정점이다.
    assert anchor.location_id == "st-nul"
    assert [candidate.location_id for candidate, _ in candidates] == ["kma"]
    client = TestClient(create_app(WeatherSettings(environment="development"), repository))
    response = client.get("/v1/weather/resolve", params={"lat": 37.5, "lon": 127.0})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["location"]["location_id"] == "st-nul"


def test_whitespace_pattern_covers_every_python_whitespace_character():
    expected = {chr(code) for code in range(0x110000) if chr(code).isspace()}
    assert set(repository_module._PY_WHITESPACE) == expected  # noqa: SLF001


def test_resolve_endpoint_selects_in_one_statement_without_loading_the_radius(
    repository, monkeypatch,
):
    _seed_catalog(repository)
    client = TestClient(create_app(WeatherSettings(environment="development"), repository))
    expected = _python_selection(repository, 37.5, 127.0, 100)

    def unexpected(*args, **kwargs):
        raise AssertionError("/resolve must not load every location in the radius")

    monkeypatch.setattr(repository, "nearest_locations", unexpected)
    statements: list[str] = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if "FROM weather_locations" in statement:
            statements.append(statement)

    event.listen(repository.engine, "before_cursor_execute", capture)
    try:
        response = client.get("/v1/weather/resolve", params={"lat": 37.5, "lon": 127.0})
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    assert len(statements) == 1 and "resolve_anchor" in statements[0]
    body = response.json()["data"]
    assert body["location"]["location_id"] == expected[0][0].location_id
    assert [item["location_id"] for item in body["source_locations"]] == [
        expected[0][0].location_id, *(candidate.location_id for candidate, _ in expected[1]),
    ]
