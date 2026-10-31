from datetime import UTC, datetime

import pytest

from kortravelweather.alerts import alert_location_scope
from kortravelweather.models import WeatherLocation, WeatherValue


def location(codes=None):
    return WeatherLocation(location_id="seoul", name="서울", latitude=37.5, longitude=127,
                           region_code="mid-forecast-code",
                           metadata={} if codes is None else {"kma_alert_area_codes": codes})


def warning(payload):
    return WeatherValue(location_id="seoul", provider="python-kma-api",
                        dataset_key="kma_weather_alerts", weather_domain="weather_alert",
                        forecast_style="observed", metric_key="ALERT", value_text="풍랑주의보 발표",
                        target_at=datetime.now(UTC), payload=payload)


@pytest.mark.parametrize(("codes", "payload", "expected"), [
    (["land-a"], {"raw": {"areaCode": "land-a"}}, "matched"),
    (["land-a"], {"raw": {"areaCode": "sea-a"}}, "different"),
    (["land-a"], {"raw": {"areaCode": "land-a-extra"}}, "different"),
    (["land-a"], {"stn_id": "108"}, "unverified"),
    (None, {"stn_id": "108", "raw": {"areaCode": "mid-forecast-code"}}, "unverified"),
    ([], {"raw": {"areaName": "서울"}}, "unverified"),
    (["land-a"], {"areaCode": "land-a", "raw": {"areaCode": "sea-a"}}, "unverified"),
])
def test_warning_requires_exact_verified_area(codes, payload, expected):
    assert alert_location_scope(warning(payload), location(codes)) == expected
