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
                "kma_ultra_short_nowcast", "초단기실황", "현재 관측값", "KMA", "매시 정각 관측 · 40분부터 조회 가능"
            ),
            DatasetSpec(
                "kma_ultra_short_forecast", "초단기예보", "시간별 예보", "KMA", "매시 30분 발표 · 45분부터 조회 가능", True
            ),
            DatasetSpec(
                "kma_short_forecast", "단기예보", "시간·일별 예보", "KMA", "하루 8회(02,05,08,11,14,17,20,23시) 발표", True
            ),
            DatasetSpec(
                "kma_mid_forecast", "중기예보", "3–10일 지역 예보", "KMA", "하루 2회(06,18시) 발표", True
            ),
        ),
    ),
    ProviderSpec(
        "python-khoa-api",
        "국립해양조사원 해수욕장",
        True,
        "data_go_kr_service_key",
        "https://apis.data.go.kr/1192136",
        (
            DatasetSpec(
                "khoa_beach_index",
                "해수욕장 해양지수",
                "해수욕장별 파고·수온·기온·풍속 일별 예보",
                "/beachIndex",
                "하루 1회 갱신",
                True,
            ),
        ),
    ),
    ProviderSpec(
        "python-krforest-api",
        "산림청 산악기상",
        True,
        "data_go_kr_service_key",
        "https://apis.data.go.kr/1400377",
        (
            DatasetSpec(
                "krforest_mountain_weather",
                "산악기상관측",
                "산악관측소 기온·습도·기압·강수·풍향풍속 관측값",
                "/mtweather/mountListSearch",
                "10분마다 갱신",
            ),
            DatasetSpec(
                "krforest_dust",
                "청정넷 미세먼지",
                "산림 청정넷(AICAN) PM10·PM2.5·PM1.0과 기온·습도·풍향풍속",
                "/AicanDustData/dustData",
                "10분마다 갱신",
            ),
        ),
    ),
    ProviderSpec(
        "python-krex-api",
        "한국도로공사 휴게소",
        True,
        "krex_api_key",
        "https://data.ex.co.kr",
        (
            DatasetSpec(
                "krex_restarea_weather",
                "휴게소 기상",
                "고속도로 휴게소 기온·습도·풍속·강수·적설 관측값",
                "/openapi/restinfo/restWeatherList",
                "매시간 갱신",
            ),
        ),
    ),
    ProviderSpec(
        "python-airkorea-api",
        "AirKorea 측정소",
        True,
        "airkorea_api_key",
        "https://apis.data.go.kr/B552584",
        (
            DatasetSpec(
                "airkorea_station_catalog",
                "측정소 카탈로그",
                "AirKorea 측정소 위치·메타데이터",
                "/MsrstnInfoInqireSvc/getMsrstnList",
                "매시간 갱신",
            ),
            DatasetSpec(
                "airkorea_realtime_measurement",
                "대기질 관측",
                "AirKorea 측정소 실시간 관측",
                "/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty",
                "매시간 갱신",
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
                "기온·습도·풍속 등 현재 날씨 관측값",
                "/current.json",
                "조회할 때마다 실시간 조회",
            ),
            DatasetSpec(
                "weatherapi_forecast",
                "예보",
                "기온·강수확률 등 시간별 날씨 예보",
                "/forecast.json",
                "매시간 갱신",
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
                "기온·습도·풍속 등 현재 날씨 관측값",
                "/weather",
                "조회할 때마다 실시간 조회",
            ),
            DatasetSpec(
                "openweathermap_forecast",
                "예보",
                "기온·강수확률 등 3시간 간격 날씨 예보",
                "/forecast",
                "3시간마다 갱신",
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
                "open_meteo_current", "현재 관측", "기온·습도·풍속 등 현재 날씨 관측값", "/forecast", "조회할 때마다 실시간 조회"
            ),
            DatasetSpec(
                "open_meteo_forecast",
                "예보",
                "기온·강수확률 등 시간별 날씨 예보",
                "/forecast",
                "매시간 갱신",
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
                "예보",
                "기온·강수확률 등 시간별 날씨 예보",
                "/timeline",
                "매시간 갱신",
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
                "기온·습도·풍속 등 현재 날씨 관측값",
                "/weather/realtime",
                "조회할 때마다 실시간 조회",
            ),
            DatasetSpec(
                "tomorrow_io_forecast",
                "예보",
                "기온·강수확률 등 시간별 날씨 예보",
                "/weather/forecast",
                "매시간 갱신",
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
                "weatherbit_current", "현재 관측", "기온·습도·풍속 등 현재 날씨 관측값", "/current", "조회할 때마다 실시간 조회"
            ),
            DatasetSpec(
                "weatherbit_forecast",
                "예보",
                "기온·강수확률 등 시간별 날씨 예보",
                "/forecast/hourly",
                "매시간 갱신",
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
                "weatherstack_current", "현재 관측", "기온·습도·풍속 등 현재 날씨 관측값", "/current", "조회할 때마다 실시간 조회"
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
                "기온·습도·풍속 등 현재 날씨 관측값",
                "/currentconditions",
                "조회할 때마다 실시간 조회",
            ),
            DatasetSpec(
                "accuweather_forecast",
                "예보",
                "기온·강수확률 등 시간별 날씨 예보",
                "/forecasts",
                "매시간 갱신",
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
                "기온·습도·풍속 등 현재 날씨 관측값",
                "/:location",
                "조회할 때마다 실시간 조회",
            ),
            DatasetSpec(
                "wttr_in_forecast", "예보", "기온·강수확률 등 시간별 날씨 예보", "/:location", "매시간 갱신", True
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
