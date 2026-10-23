"""원천 raw를 독립 재정규화한다. 캡처 values의 자기 hash를 증거로 쓰지 않는다."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_external_providers import FakeResponse, FixtureTransport

from kortravelweather.export_heads import semantic_point_digest
from kortravelweather.providers import OpenMeteoProvider, OpenWeatherMapProvider, WeatherApiProvider
from kortravelweather.providers.base import ProviderLocation

LOCATION = ProviderLocation("export-test", 37.5, 127, {})
AT = datetime(2026, 10, 23, 8, tzinfo=UTC)


@pytest.mark.parametrize(
    "provider,payload",
    [
        (
            WeatherApiProvider,
            {
                "location": {"tz_id": "Asia/Seoul", "lat": 37.5, "lon": 127},
                "current": {"last_updated_epoch": AT.timestamp(), "temp_c": 20, "humidity": 50},
            },
        ),
        (
            OpenWeatherMapProvider,
            {"coord": {"lat": 37.5, "lon": 127}, "dt": AT.timestamp(), "main": {"temp": 20}},
        ),
        (
            OpenMeteoProvider,
            {
                "latitude": 37.5,
                "longitude": 127,
                "current": {"time": AT.isoformat(), "temperature_2m": 20},
            },
        ),
    ],
)
def test_saved_raw_revalidation_uses_same_adapter_without_http_or_live_credential(
    provider, payload
):
    live = provider(api_key="fixture-secret", transport=FixtureTransport(FakeResponse(payload)))
    response = live.fetch(LOCATION, at=AT)

    class ForbiddenTransport:
        def request(self, *args, **kwargs):
            raise AssertionError("응답 정규화 중에는 HTTP를 호출하면 안 됩니다.")

    replay = provider(transport=ForbiddenTransport())
    raw = response.source_record["payload"]
    reconstructed = replay.normalize_response(
        LOCATION,
        dataset_key=response.dataset_key,
        payload=raw["rows"][0],
        metadata=raw["response_metadata"],
        at=response.source_record["fetched_at"],
    )
    wanted = sorted(semantic_point_digest(v.model_dump(mode="json")) for v in response.values)
    proven = sorted(
        semantic_point_digest(v.model_dump(mode="json")) for v in reconstructed.values
    )
    assert wanted == proven
    assert reconstructed.source_record["source_record_key"] == response.source_record[
        "source_record_key"
    ]
    forged = reconstructed.values[0].model_copy(update={"value_number": Decimal("999")})
    assert semantic_point_digest(forged.model_dump(mode="json")) not in proven
    with pytest.raises(ValueError, match="timezone"):
        replay.normalize_response(
            LOCATION,
            dataset_key=response.dataset_key,
            payload=raw["rows"][0],
            metadata=raw["response_metadata"],
            at=AT.replace(tzinfo=None),
        )
