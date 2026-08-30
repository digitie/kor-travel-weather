"""지원 provider/dataset의 정적 catalog.

catalog은 UI와 Dagster가 공유하는 계약이다. 비밀값은 catalog에 넣지 않고
settings에서만 읽는다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    key: str
    label: str
    description: str
    endpoint: str
    cadence: str
    forecast: bool = False


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    key: str
    label: str
    auth_required: bool
    credential_field: str | None
    base_url: str
    datasets: tuple[DatasetSpec, ...]


PROVIDER_CATALOG: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "python-kma-api",
        "기상청 KMA",
        True,
        "data_go_kr_service_key",
        "https://apis.data.go.kr",
        (
            DatasetSpec(
                "kma_ultra_short_nowcast", "초단기실황", "현재 관측값", "KMA", "provider 응답 주기"
            ),
            DatasetSpec(
                "kma_ultra_short_forecast", "초단기예보", "시간별 예보", "KMA", "발표 기준", True
            ),
            DatasetSpec(
                "kma_short_forecast", "단기예보", "시간·일별 예보", "KMA", "발표 기준", True
            ),
            DatasetSpec(
                "kma_mid_forecast", "중기예보", "3–10일 지역 예보", "KMA", "발표 기준", True
            ),
        ),
    ),
    ProviderSpec(
        "weatherapi",
        "WeatherAPI",
        True,
        "weatherapi_api_key",
        "https://api.weatherapi.com/v1",
        (
            DatasetSpec(
                "weatherapi_current",
                "현재 관측",
                "WeatherAPI current",
                "/current.json",
                "요청 시점",
            ),
            DatasetSpec(
                "weatherapi_forecast",
                "예보",
                "WeatherAPI hourly forecast",
                "/forecast.json",
                "시간별",
                True,
            ),
        ),
    ),
    ProviderSpec(
        "openweathermap",
        "OpenWeatherMap",
        True,
        "openweathermap_api_key",
        "https://api.openweathermap.org/data/2.5",
        (
            DatasetSpec(
                "openweathermap_current",
                "현재 관측",
                "OpenWeatherMap current",
                "/weather",
                "요청 시점",
            ),
            DatasetSpec(
                "openweathermap_forecast",
                "예보",
                "OpenWeatherMap 3-hour forecast",
                "/forecast",
                "3시간별",
                True,
            ),
        ),
    ),
    ProviderSpec(
        "open_meteo",
        "Open-Meteo",
        False,
        None,
        "https://api.open-meteo.com/v1",
        (
            DatasetSpec(
                "open_meteo_current", "현재 관측", "Open-Meteo current", "/forecast", "요청 시점"
            ),
            DatasetSpec(
                "open_meteo_forecast",
                "예보",
                "Open-Meteo hourly forecast",
                "/forecast",
                "시간별",
                True,
            ),
        ),
    ),
    ProviderSpec(
        "visual_crossing",
        "Visual Crossing",
        True,
        "visual_crossing_api_key",
        "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services",
        (
            DatasetSpec(
                "visual_crossing_timeline",
                "Timeline",
                "Visual Crossing timeline",
                "/timeline",
                "시간별",
                True,
            ),
        ),
    ),
    ProviderSpec(
        "tomorrow_io",
        "Tomorrow.io",
        True,
        "tomorrow_io_api_key",
        "https://api.tomorrow.io/v4",
        (
            DatasetSpec(
                "tomorrow_io_realtime",
                "실시간",
                "Tomorrow.io realtime",
                "/weather/realtime",
                "요청 시점",
            ),
            DatasetSpec(
                "tomorrow_io_forecast",
                "예보",
                "Tomorrow.io forecast",
                "/weather/forecast",
                "시간별",
                True,
            ),
        ),
    ),
    ProviderSpec(
        "weatherbit",
        "Weatherbit",
        True,
        "weatherbit_api_key",
        "https://api.weatherbit.io/v2.0",
        (
            DatasetSpec(
                "weatherbit_current", "현재 관측", "Weatherbit current", "/current", "요청 시점"
            ),
            DatasetSpec(
                "weatherbit_forecast",
                "예보",
                "Weatherbit hourly forecast",
                "/forecast/hourly",
                "시간별",
                True,
            ),
        ),
    ),
    ProviderSpec(
        "weatherstack",
        "Weatherstack",
        True,
        "weatherstack_api_key",
        "https://api.weatherstack.com",
        (
            DatasetSpec(
                "weatherstack_current", "현재 관측", "Weatherstack current", "/current", "요청 시점"
            ),
        ),
    ),
    ProviderSpec(
        "accuweather",
        "AccuWeather",
        True,
        "accuweather_api_key",
        "https://dataservice.accuweather.com",
        (
            DatasetSpec(
                "accuweather_current",
                "현재 관측",
                "AccuWeather current conditions",
                "/currentconditions",
                "요청 시점",
            ),
            DatasetSpec(
                "accuweather_forecast",
                "예보",
                "AccuWeather hourly forecast",
                "/forecasts",
                "시간별",
                True,
            ),
        ),
    ),
    ProviderSpec(
        "wttr_in",
        "wttr.in",
        False,
        None,
        "https://wttr.in",
        (
            DatasetSpec(
                "wttr_in_current",
                "현재 관측",
                "wttr.in current condition",
                "/:location",
                "요청 시점",
            ),
            DatasetSpec(
                "wttr_in_forecast", "예보", "wttr.in hourly forecast", "/:location", "시간별", True
            ),
        ),
    ),
)


def provider_spec(provider_key: str) -> ProviderSpec:
    for spec in PROVIDER_CATALOG:
        if spec.key == provider_key:
            return spec
    raise KeyError(provider_key)


def catalog_dicts(*, configured: dict[str, bool] | None = None) -> list[dict[str, object]]:
    return [
        {
            "provider": spec.key,
            "label": spec.label,
            "auth_required": spec.auth_required,
            "credential_configured": (configured.get(spec.key) if configured is not None else None),
            "base_url": spec.base_url,
            "datasets": [
                {
                    "key": dataset.key,
                    "label": dataset.label,
                    "description": dataset.description,
                    "endpoint": dataset.endpoint,
                    "cadence": dataset.cadence,
                    "forecast": dataset.forecast,
                }
                for dataset in spec.datasets
            ],
        }
        for spec in PROVIDER_CATALOG
    ]
