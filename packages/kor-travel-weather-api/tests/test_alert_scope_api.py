from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from kortravelweather_api.app import create_app

from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.settings import WeatherSettings


@pytest.mark.parametrize(("area", "expected", "unverified"), [
    ("land-seoul", 1, False), ("sea-other", 0, False), (None, 0, True),
])
@pytest.mark.parametrize("path", [
    "/v1/weather/markers?location_id=seoul",
    "/v1/weather/locations/seoul/latest",
    "/v1/weather/nearby?lat=37.5&lon=127&limit=1",
    "/v1/weather/resolve?lat=37.5&lon=127",
    "/v1/weather/resolve?lat=37.50001&lon=127",
])
def test_public_location_alerts_are_scoped(path, area, expected, unverified):
    loc = WeatherLocation(location_id="seoul", name="서울", latitude=37.5, longitude=127,
                          region_code="mid-forecast-code",
                          metadata={"kma_alert_area_codes": ["land-seoul"]})
    now = datetime.now(UTC)
    alert = WeatherValue(location_id="seoul", provider="python-kma-api",
                         dataset_key="kma_weather_alerts", weather_domain="weather_alert",
                         forecast_style="observed", metric_key="ALERT", target_at=now,
                         value_text="풍랑주의보 발표", payload={"stn_id": "108", "raw": {
                             **({"areaCode": area} if area else {})}}, source_record_key="notice")
    repo = SimpleNamespace(
        create_schema=lambda: None,
        get_location=lambda *a, **k: loc,
        get_locations_by_ids=lambda *a, **k: [loc],
        list_locations=lambda *a, **k: [loc],
        nearest_locations=lambda *a, **k: [(loc, 0.0)],
        latest_values=lambda *a, **k: [alert],
        latest_values_many=lambda *a, **k: {"seoul": [alert]},
        marker_values_many=lambda *a, **k: {"seoul": [alert]},
        timeline_many=lambda *a, **k: {"seoul": []},
        forecast_values_many=lambda *a, **k: {"seoul": []},
        alert_values_many=lambda *a, **k: {"seoul": [alert]},
    )
    with TestClient(create_app(WeatherSettings(environment="development"), repo)) as client:
        response = client.get(path)
    assert response.status_code == 200, response.text
    body = response.json()
    if "lat=37.50001" in path:
        expected, unverified = 0, True
    if "/latest" in path:
        assert len(body["data"]) == expected
        assert body["meta"]["alert_scope_unverified"] is unverified
    else:
        row = body["data"] if "/resolve" in path else body["data"][0]
        assert len(row["alerts"]) == expected
        assert row["alert_scope_unverified"] is unverified
