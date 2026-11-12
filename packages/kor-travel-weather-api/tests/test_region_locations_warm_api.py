from datetime import UTC, datetime

from fastapi.testclient import TestClient
from kortravelcommon.address_providers import VworldKakaoAddressResolver
from kortravelcommon.reverse_address import AddressTarget
from kortravelweather_api.app import create_app
from sqlalchemy import event, insert

from kortravelweather.models import WeatherLocation
from kortravelweather.repository import Base, WeatherRepository
from kortravelweather.settings import WeatherSettings


def test_ready_projection_returns_public_location_dto_without_scanning_weather_facts(
    weather_test_database_url,
):
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    try:
        repo.upsert_location(
            WeatherLocation(
                location_id="sample",
                name="대표 지점",
                latitude=37,
                longitude=127,
                metadata={"private": "not-public"},
            )
        )
        with repo.engine.begin() as connection:
            connection.execute(
                insert(Base.metadata.tables["reverse_address_entries"]),
                {
                    "provider_id": VworldKakaoAddressResolver.provider_id,
                    "target_key": "sample",
                    "coordinate_key": AddressTarget("sample", 127, 37).coordinate_key,
                    "longitude": 127,
                    "latitude": 37,
                    "status": "ok",
                    "failures": 0,
                    "checked_at": datetime.now(UTC),
                    "payload": {"region": {"sido": "경기도", "sigungu": "수원시"}},
                },
            )
        assert repo.refresh_region_locations()
        client = TestClient(
            create_app(
                WeatherSettings(
                    environment="development",
                    database_url=weather_test_database_url,
                ),
                repo,
            )
        )
        statements = []

        def record(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(repo.engine, "before_cursor_execute", record)
        try:
            response = client.get("/v1/weather/regions")
        finally:
            event.remove(repo.engine, "before_cursor_execute", record)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["ready"] and len(data["items"]) == 2 and data["calculated_at"]
        assert {row["level"] for row in data["items"]} == {"sido", "sigungu"}
        assert all(row["location"]["latitude"] == 37 for row in data["items"])
        assert all(row["location"]["metadata"] == {} for row in data["items"])
        assert all(row["location"]["sigungu_name"] == "수원시" for row in data["items"])
        assert "not-public" not in response.text
        assert not any("weather_values" in sql or "FOR UPDATE" in sql for sql in statements)
    finally:
        repo.engine.dispose()
