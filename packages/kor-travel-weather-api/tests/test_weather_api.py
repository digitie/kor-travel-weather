from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings
from kortravelweather_api.app import create_app


def test_health_and_location_admin(api_client: TestClient) -> None:
    assert api_client.get("/health").status_code == 200
    created = api_client.post(
        "/v1/admin/locations",
        json={
            "location_id": "seoul",
            "name": "서울",
            "latitude": 37.5665,
            "longitude": 126.978,
            "nx": 60,
            "ny": 127,
        },
    )
    assert created.status_code == 201
    listed = api_client.get("/v1/weather/locations")
    assert listed.status_code == 200
    assert listed.json()["data"][0]["location_id"] == "seoul"


def test_forecast_order_is_422(api_client: TestClient) -> None:
    response = api_client.get(
        "/v1/weather/locations/missing/forecast",
        params={"from": "2026-01-02T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_production_requires_admin_token(tmp_path) -> None:
    settings = WeatherSettings(
        environment="production", database_url=f"sqlite:///{tmp_path / 'weather.db'}"
    )
    try:
        create_app(settings, WeatherRepository(settings.database_url))
    except RuntimeError as exc:
        assert "ADMIN_TOKEN" in str(exc)
    else:
        raise AssertionError("production app must fail closed without admin token")


def test_revisions_are_deduped_for_public_latest(tmp_path) -> None:
    settings = WeatherSettings(database_url=f"sqlite:///{tmp_path / 'weather.db'}")
    repo = WeatherRepository(settings.database_url)
    repo.create_schema()
    repo.upsert_location(
        WeatherLocation(location_id="x", name="X", latitude=37, longitude=127, nx=1, ny=1)
    )
    common = dict(
        location_id="x",
        provider="python-kma-api",
        dataset_key="kma_short_forecast",
        weather_domain="kma_short_forecast",
        forecast_style=ForecastStyle.SHORT,
        metric_key="TMP",
        issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        valid_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        target_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
    )
    first = WeatherValue(**common, value_number=Decimal("1"), payload={"v": 1}, source_record_key="sr-1")
    second = WeatherValue(**common, value_number=Decimal("2"), payload={"v": 2}, source_record_key="sr-2")
    repo.upsert_values([first, second])
    assert len(repo.latest_values("x")) == 1
    assert repo.latest_values("x")[0].value_number == Decimal("2")
    assert len(repo.timeline("x", include_revisions=True)) == 2
