from fastapi.testclient import TestClient
from kortravelweather_api.app import create_app
from sqlalchemy import event

from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings


def test_cold_projection_is_an_explicit_fallback_not_a_collection_request(
    weather_test_database_url,
):
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    try:
        client = TestClient(
            create_app(
                WeatherSettings(environment="development", database_url=weather_test_database_url),
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
        assert response.json()["data"] == {"ready": False, "calculated_at": None, "items": []}
        assert not any(
            "weather_values" in statement or "weather_source_records" in statement
            for statement in statements
        )
    finally:
        repo.engine.dispose()
