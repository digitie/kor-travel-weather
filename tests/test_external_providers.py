from __future__ import annotations

import os
from datetime import UTC
from decimal import Decimal
from typing import Any

import pytest
from kortravelweather_dagster.external_weather import run_external_weather_sync

from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.providers import (
    AccuWeatherProvider,
    OpenMeteoProvider,
    OpenWeatherMapProvider,
    ProviderError,
    ProviderLocation,
    TomorrowIoProvider,
    VisualCrossingProvider,
    WeatherApiProvider,
    WeatherbitProvider,
    WeatherstackProvider,
    WttrInProvider,
)
from kortravelweather.providers.base import CredentialError, make_source_record, request_json
from kortravelweather.providers.external import HttpWeatherProvider
from kortravelweather.providers.factory import create_configured_provider
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self.text = "fixture"

    def json(self) -> Any:
        return self.payload


class FixtureTransport:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


LOCATION = ProviderLocation("seoul", 37.5665, 126.978, {})


@pytest.mark.parametrize(
    ("provider_type", "payload", "key", "metadata"),
    [
        (
            WeatherApiProvider,
            {
                "location": {"tz_id": "Asia/Seoul"},
                "current": {
                    "last_updated": "2026-08-30 12:00",
                    "temp_c": 25,
                    "humidity": 50,
                    "wind_kph": 36,
                    "condition": {"code": 1000},
                },
            },
            "weather-api-secret",
            {},
        ),
        (
            OpenWeatherMapProvider,
            {
                "dt": 1788091200,
                "main": {"temp": 25, "humidity": 50},
                "wind": {"speed": 2},
                "weather": [{"id": 1}],
            },
            "owm-secret",
            {},
        ),
        (
            OpenMeteoProvider,
            {
                "current": {
                    "time": "2026-08-30T03:00:00Z",
                    "temperature_2m": 25,
                    "relative_humidity_2m": 50,
                }
            },
            None,
            {},
        ),
        (
            VisualCrossingProvider,
            {
                "timezone": "Asia/Seoul",
                "currentConditions": {
                    "datetime": "12:00:00",
                    "datetimeEpoch": 1788066000,
                    "temp": 25,
                },
            },
            "vc-secret",
            {},
        ),
        (
            TomorrowIoProvider,
            {
                "data": {
                    "time": "2026-08-30T03:00:00Z",
                    "values": {"temperature": 25, "humidity": 50},
                }
            },
            "tomorrow-secret",
            {},
        ),
        (
            WeatherbitProvider,
            {"data": [{"ts": 1788091200, "temp": 25, "rh": 50}]},
            "weatherbit-secret",
            {},
        ),
        (
            WeatherstackProvider,
            {
                "location": {"timezone_id": "Asia/Seoul", "localtime": "2026-08-30 12:00"},
                "current": {"observation_time": "12:00 PM", "temperature": 25, "humidity": 50},
            },
            "stack-secret",
            {},
        ),
        (
            AccuWeatherProvider,
            [
                {
                    "DateTime": "2026-08-30T03:00:00Z",
                    "Temperature": {"Metric": {"Value": 25}},
                    "RelativeHumidity": 50,
                }
            ],
            "accu-secret",
            {"accuweather_location_key": "123"},
        ),
        (
            WttrInProvider,
            {
                "nearest_area": [{"timezone": [{"value": "Asia/Seoul"}]}],
                "weather": [{"date": "2026-08-30"}],
                "current_condition": [
                    {"observation_time": "12:00 PM", "temp_C": 25, "humidity": 50}
                ],
            },
            None,
            {},
        ),
    ],
)
def test_external_fixture_adapters_return_common_values(
    provider_type: type[HttpWeatherProvider],
    payload: Any,
    key: str | None,
    metadata: dict[str, Any],
) -> None:
    transport = FixtureTransport(FakeResponse(payload))
    response = provider_type(api_key=key, transport=transport).fetch(
        ProviderLocation(LOCATION.location_id, LOCATION.latitude, LOCATION.longitude, metadata)
    )
    assert response.values
    assert all(value.provider == response.provider for value in response.values)
    assert all(
        value.target_at is not None and value.target_at.tzinfo == UTC for value in response.values
    )
    assert response.source_record["source_record_key"].startswith("sr_")
    assert key is None or key not in str(response.source_record)


def test_common_unit_conversion_and_weather_value_round_trip() -> None:
    transport = FixtureTransport(
        FakeResponse(
            {
                "location": {"tz_id": "Asia/Seoul"},
                "current": {"last_updated": "2026-08-30 12:00", "temp_c": 25, "wind_kph": 36},
            }
        )
    )
    result = WeatherApiProvider(api_key="secret", transport=transport).fetch(LOCATION)
    wind = next(value for value in result.values if value.metric_key == "WIND_SPEED")
    assert wind.value_number == Decimal("10.0000")
    assert wind.unit == "m/s"
    restored = WeatherValue.model_validate(wind.model_dump())
    assert restored.identity_key() == wind.identity_key()


def test_forecast_overlap_prefers_hourly_row_without_duplicate_identity() -> None:
    payload = {
        "location": {"tz_id": "Asia/Seoul"},
        "current": {
            "last_updated": "2026-08-30 12:00",
            "temp_c": 25,
            "humidity": 50,
            "condition": {"code": 1000},
        },
        "forecast": {
            "forecastday": [
                {
                    "date": "2026-08-30",
                    "hour": [
                        {
                            "time": "2026-08-30 12:00",
                            "temp_c": 26,
                            "humidity": 51,
                            "condition": {"code": 1000},
                        }
                    ],
                }
            ]
        },
    }
    response = WeatherApiProvider(
        api_key="secret", transport=FixtureTransport(FakeResponse(payload))
    ).fetch(LOCATION, dataset_key="weatherapi_forecast")
    temperatures = [value for value in response.values if value.metric_key == "TEMP"]
    assert len(temperatures) == 1
    assert temperatures[0].value_number == Decimal("26.0000")


def test_missing_credential_is_non_network_error() -> None:
    transport = FixtureTransport()
    with pytest.raises(CredentialError) as error:
        WeatherApiProvider(transport=transport).fetch(LOCATION)
    assert error.value.code == "credential_missing"
    assert transport.calls == []


def test_source_endpoint_query_is_redacted() -> None:
    source = make_source_record(
        provider="fixture",
        dataset_key="fixture_current",
        location_id="seoul",
        payload={"temperature": 25},
        endpoint="https://example.test/weather?api_key=REAL_SECRET&city=seoul",
    )
    metadata = source["payload"]["response_metadata"]
    assert "REAL_SECRET" not in metadata["endpoint"]
    assert "api_key=[REDACTED]" in metadata["endpoint"]


def test_dataset_contract_rejects_unknown_before_transport() -> None:
    transport = FixtureTransport()
    with pytest.raises(ProviderError, match="지원하지 않는 dataset"):
        OpenMeteoProvider(transport=transport).fetch(LOCATION, dataset_key="not_a_catalog_dataset")
    assert transport.calls == []


def test_httpx_connect_error_is_retried() -> None:
    import httpx

    class FailingTransport:
        def __init__(self) -> None:
            self.calls = 0

        def request(self, method: str, url: str, **kwargs: Any) -> Any:
            self.calls += 1
            raise httpx.ConnectError("dns down")

    transport = FailingTransport()
    with pytest.raises(ProviderError) as error:
        request_json(transport, "GET", "https://example.test", retries=2)
    assert error.value.code == "network"
    assert error.value.retryable is True
    assert transport.calls == 3


def test_http_payload_limit_is_checked_before_json_parsing() -> None:
    class HugeResponse:
        status_code = 200
        headers = {"content-length": "10"}
        content = b"0123456789"

        def __init__(self) -> None:
            self.json_calls = 0

        @property
        def text(self) -> str:
            return self.content.decode()

        def json(self) -> Any:
            self.json_calls += 1
            raise AssertionError("JSON must not be parsed after the size check")

    class HugeTransport:
        def __init__(self) -> None:
            self.response = HugeResponse()

        def request(self, method: str, url: str, **kwargs: Any) -> HugeResponse:
            return self.response

    transport = HugeTransport()
    with pytest.raises(ProviderError) as error:
        request_json(
            transport,
            "GET",
            "https://example.test",
            max_bytes=5,
        )
    assert error.value.code == "payload_too_large"
    assert transport.response.json_calls == 0


def test_retry_only_retries_transient_http_status() -> None:
    transport = FixtureTransport(
        FakeResponse({}, status_code=500),
        FakeResponse(
            {
                "location": {"tz_id": "Asia/Seoul"},
                "current": {"last_updated": "2026-08-30 12:00", "temp_c": 25},
            }
        ),
    )
    result = WeatherApiProvider(api_key="secret", transport=transport, retries=1).fetch(LOCATION)
    assert result.values
    assert len(transport.calls) == 2

    non_retryable = FixtureTransport(FakeResponse({}, status_code=401), FakeResponse({}))
    with pytest.raises(ProviderError) as error:
        WeatherApiProvider(api_key="secret", transport=non_retryable, retries=1).fetch(LOCATION)
    assert error.value.code == "auth"
    assert len(non_retryable.calls) == 1


def test_settings_accepts_legacy_provider_env_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEATHER_API_KEY", "alias-secret")
    monkeypatch.setenv("WEATHER_PROVIDERS", "open_meteo,weatherapi")
    settings = WeatherSettings(_env_file=None)
    assert settings.provider_api_key("weatherapi") == "alias-secret"
    assert settings.enabled_providers == ["open_meteo", "weatherapi"]


def test_configured_factory_requires_paid_provider_credential() -> None:
    settings = WeatherSettings(
        _env_file=None, environment="development", enabled_providers=["weatherapi"]
    )
    with pytest.raises(CredentialError):
        create_configured_provider("weatherapi", settings=settings, transport=FixtureTransport())
    provider = create_configured_provider(
        "open_meteo",
        settings=WeatherSettings(
            _env_file=None, environment="development", enabled_providers=["open_meteo"]
        ),
        transport=FixtureTransport(),
    )
    assert provider.provider_key == "open_meteo"


def test_external_dagster_boundary_is_atomic_and_idempotent(tmp_path: Any) -> None:
    payload = {
        "current": {
            "time": "2026-08-30T03:00:00Z",
            "temperature_2m": 25,
            "relative_humidity_2m": 50,
        }
    }
    transport = FixtureTransport(FakeResponse(payload), FakeResponse(payload))
    provider = OpenMeteoProvider(transport=transport)
    repository = WeatherRepository(TEST_DATABASE_URL)
    repository.create_schema()
    repository.upsert_location(
        WeatherLocation(
            location_id="seoul",
            name="서울",
            latitude=37.5665,
            longitude=126.978,
        )
    )
    first = run_external_weather_sync(
        repository=repository,
        provider=provider,
        targets=[LOCATION],
        dataset_key="open_meteo_current",
    )
    second = run_external_weather_sync(
        repository=repository,
        provider=provider,
        targets=[LOCATION],
        dataset_key="open_meteo_current",
    )
    assert first["values_loaded"] == 2
    assert second["values_loaded"] == 0
    assert len(repository.timeline("seoul", include_revisions=True)) == 2
    assert len(repository.list_sync_run_sources(first["run_id"])) == 1
