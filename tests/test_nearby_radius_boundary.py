"""반경 사전 필터가 실제 구면 거리 안의 동쪽 경계 지점을 버리지 않는다."""

from fastapi.testclient import TestClient
from kortravelweather_api.app import create_app

from kortravelweather.models import WeatherLocation
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings


def seed(database_url):
    repo = WeatherRepository(database_url)
    repo.create_schema()
    for key, lat, lon in [
        ("near", 37.001, 126.001),
        # 중심37N/126E에서499.9km. 반경500의 기존 근사 bbox 동쪽 밖이다.
        ("edge", 37.133369, 131.632523),
        ("outside", 41.3, 130),
    ]:
        repo.upsert_location(
            WeatherLocation(
                location_id=key,
                name=key,
                latitude=lat,
                longitude=lon,
            )
        )
    return repo


def test_exact_radius_boundary_survives_sql_prefilter_and_limit(weather_test_database_url):
    repo = seed(weather_test_database_url)
    try:
        rows = repo.nearest_locations(37, 126, radius_km=500, limit=2)
        assert [location.location_id for location, _ in rows] == ["near", "edge"]
        assert 499.8 < rows[1][1] < 500
        assert len(repo.nearest_locations(37, 126, radius_km=500, limit=1)) == 1
    finally:
        repo.engine.dispose()


def test_nearby_api_returns_inside_edge_without_outside_point(weather_test_database_url):
    repo = seed(weather_test_database_url)
    try:
        client = TestClient(
            create_app(
                WeatherSettings(
                    environment="development",
                    database_url=weather_test_database_url,
                ),
                repo,
            )
        )
        response = client.get(
            "/v1/weather/nearby",
            params={
                "lat": 37,
                "lon": 126,
                "radius_km": 500,
                "limit": 2,
            },
        )
        assert response.status_code == 200
        assert [row["location_id"] for row in response.json()["data"]] == ["near", "edge"]
        assert all(row["distance_km"] <= 500 for row in response.json()["data"])
    finally:
        repo.engine.dispose()
