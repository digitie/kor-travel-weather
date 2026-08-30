"""WeatherAPI 계열 provider adapter 구현.

각 adapter는 provider 응답 shape만 해석하고, 저장/재시도 정책은
``providers.base``에 위임한다. live credential은 생성자 인자로만 받고 raw
lineage에는 절대 기록하지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from kortravelweather.models import ForecastStyle, TimelineBucket

from .base import (
    CredentialError,
    HttpTransport,
    ProviderError,
    ProviderLocation,
    ProviderResponse,
    WeatherProvider,
    httpx_transport,
    make_source_record,
    make_value,
    parse_datetime,
    request_json,
)

TEMP = "TEMP"
FEELS_LIKE = "FEELS_LIKE"
HUMIDITY = "HUMIDITY"
PRESSURE = "PRESSURE"
WIND_SPEED = "WIND_SPEED"
WIND_DIRECTION = "WIND_DIRECTION"
PRECIP = "PRECIP"
PRECIP_PROB = "PRECIP_PROB"
CLOUD_COVER = "CLOUD_COVER"
VISIBILITY = "VISIBILITY"
UV_INDEX = "UV_INDEX"
WEATHER_CODE = "WEATHER_CODE"

METRIC_NAMES = {
    TEMP: "기온",
    FEELS_LIKE: "체감온도",
    HUMIDITY: "상대습도",
    PRESSURE: "기압",
    WIND_SPEED: "풍속",
    WIND_DIRECTION: "풍향",
    PRECIP: "강수량",
    PRECIP_PROB: "강수확률",
    CLOUD_COVER: "운량",
    VISIBILITY: "가시거리",
    UV_INDEX: "자외선 지수",
    WEATHER_CODE: "날씨 코드",
}


def _tz(value: Any) -> ZoneInfo:
    try:
        return ZoneInfo(str(value))
    except Exception:
        return ZoneInfo("Asia/Seoul")


def _first(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _nested(mapping: Mapping[str, Any], *path: str) -> Any:
    value: Any = mapping
    for name in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(name)
    return value


def _read_path(mapping: Mapping[str, Any], path: str) -> Any:
    """provider별 dotted field와 nested object를 같은 방식으로 읽는다."""
    if path in mapping:
        return mapping[path]
    value: Any = mapping
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def _parse_or_default(value: Any, *, tz: ZoneInfo, fallback: datetime) -> datetime:
    try:
        return parse_datetime(value, tz=tz)
    except (TypeError, ValueError):
        return fallback


def _parse_local_clock(value: Any, date_text: Any, *, tz: ZoneInfo, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    text = str(value).strip()
    try:
        return parse_datetime(text, tz=tz)
    except ValueError:
        date_part = str(date_text)[:10]
        for format_text in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(f"{date_part} {text}", format_text)
            except ValueError:
                continue
            return parsed.replace(tzinfo=tz).astimezone(UTC)
        return fallback


class HttpWeatherProvider:
    provider_key = ""
    default_base_url = ""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        transport: HttpTransport | None = None,
        base_url: str | None = None,
        timeout: float = 15.0,
        retries: int = 1,
    ) -> None:
        self.api_key = api_key.strip() if api_key and api_key.strip() else None
        self.transport = transport or httpx_transport()
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.timeout = timeout
        self.retries = retries

    def close(self) -> None:
        close = getattr(self.transport, "close", None)
        if callable(close):
            close()

    def _require_key(self) -> str:
        if not self.api_key:
            raise CredentialError(self.provider_key)
        return self.api_key

    def _request(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        payload, response, metadata = request_json(
            self.transport,
            "GET",
            f"{self.base_url}/{path.lstrip('/')}",
            params=params,
            headers=headers,
            timeout=self.timeout,
            retries=self.retries,
        )
        return payload, {
            **metadata,
            "response_headers": {"content_type": response.headers.get("content-type", "")},
        }

    def _finish(
        self,
        location: ProviderLocation,
        *,
        dataset_key: str,
        payload: Mapping[str, Any],
        metadata: Mapping[str, Any],
        rows: Sequence[Mapping[str, Any]],
        values: list[tuple[Mapping[str, Any], str, Any, str | None, datetime, str | None]],
    ) -> ProviderResponse:
        source = make_source_record(
            provider=self.provider_key,
            dataset_key=dataset_key,
            location_id=location.location_id,
            payload=payload,
            endpoint=str(metadata.get("endpoint", self.base_url)),
            request_params=metadata.get("request_params"),
            status_code=metadata.get("status"),
        )
        source_key = source["source_record_key"]
        normalized = [
            make_value(
                provider=self.provider_key,
                dataset_key=dataset_key,
                location_id=location.location_id,
                metric_key=metric,
                value=raw_value,
                unit=unit,
                target_at=target,
                source_metric_key=source_metric,
                metric_name=METRIC_NAMES.get(metric),
                forecast_style=ForecastStyle.SHORT
                if "forecast" in dataset_key or "timeline" in dataset_key
                else ForecastStyle.OBSERVED,
                timeline_bucket=TimelineBucket.SHORT
                if "forecast" in dataset_key or "timeline" in dataset_key
                else None,
                source_record_key=source_key,
                raw=row,
            )
            for row, metric, raw_value, unit, target, source_metric in values
            if raw_value is not None
        ]
        if not normalized:
            raise ProviderError("provider 응답에 정규화할 weather metric이 없습니다.", code="empty")
        return ProviderResponse(self.provider_key, dataset_key, source, normalized)


def _simple_values(
    row: Mapping[str, Any],
    *,
    target: datetime,
    fields: Sequence[tuple[str, str, str | None]],
) -> list[tuple[Mapping[str, Any], str, Any, str | None, datetime, str | None]]:
    return [
        (row, metric, _read_path(row, source), unit, target, source)
        for metric, source, unit in fields
    ]


class WeatherApiProvider(HttpWeatherProvider):
    provider_key = "weatherapi"
    default_base_url = "https://api.weatherapi.com/v1"

    def fetch(
        self,
        location: ProviderLocation,
        *,
        dataset_key: str | None = None,
        at: datetime | None = None,
    ) -> ProviderResponse:
        dataset = dataset_key or "weatherapi_current"
        if dataset not in {"weatherapi_current", "weatherapi_forecast"}:
            raise ValueError(f"지원하지 않는 dataset: {dataset}")
        params = {
            "key": self._require_key(),
            "q": f"{location.latitude},{location.longitude}",
            "aqi": "no",
            "alerts": "no",
        }
        if dataset.endswith("forecast"):
            params["days"] = 2
        payload, metadata = self._request(
            "forecast.json" if dataset.endswith("forecast") else "current.json", params=params
        )
        if not isinstance(payload, Mapping) or "current" not in payload:
            raise ProviderError("WeatherAPI current 필드가 없습니다.", code="schema")
        timezone = _tz(_nested(payload, "location", "tz_id"))
        rows: list[Mapping[str, Any]] = [payload["current"]]
        values = _simple_values(
            payload["current"],
            target=_parse_or_default(
                _first(payload["current"], "last_updated_epoch", "last_updated"),
                tz=timezone,
                fallback=at or datetime.now(UTC),
            ),
            fields=(
                (TEMP, "temp_c", "deg_c"),
                (FEELS_LIKE, "feelslike_c", "deg_c"),
                (HUMIDITY, "humidity", "%"),
                (PRESSURE, "pressure_mb", "hPa"),
                (WIND_SPEED, "wind_kph", "km/h"),
                (WIND_DIRECTION, "wind_degree", "deg"),
                (PRECIP, "precip_mm", "mm"),
                (CLOUD_COVER, "cloud", "%"),
                (VISIBILITY, "vis_km", "km"),
                (UV_INDEX, "uv", "index"),
                (WEATHER_CODE, "condition.code", "code"),
            ),
        )
        if dataset.endswith("forecast"):
            for day in (
                _first(payload, "forecast").get("forecastday", [])
                if isinstance(_first(payload, "forecast"), Mapping)
                else []
            ):
                for hour in day.get("hour", []):
                    hour_target = parse_datetime(hour.get("time"), tz=timezone)
                    rows.append(hour)
                    values.extend(
                        _simple_values(
                            hour,
                            target=hour_target,
                            fields=(
                                (TEMP, "temp_c", "deg_c"),
                                (FEELS_LIKE, "feelslike_c", "deg_c"),
                                (HUMIDITY, "humidity", "%"),
                                (WIND_SPEED, "wind_kph", "km/h"),
                                (PRECIP, "precip_mm", "mm"),
                                (PRECIP_PROB, "chance_of_rain", "%"),
                                (WEATHER_CODE, "condition.code", "code"),
                            ),
                        )
                    )
        return self._finish(
            location,
            dataset_key=dataset,
            payload=payload,
            metadata=metadata,
            rows=rows,
            values=values,
        )


class OpenWeatherMapProvider(HttpWeatherProvider):
    provider_key = "openweathermap"
    default_base_url = "https://api.openweathermap.org/data/2.5"

    def fetch(
        self,
        location: ProviderLocation,
        *,
        dataset_key: str | None = None,
        at: datetime | None = None,
    ) -> ProviderResponse:
        dataset = dataset_key or "openweathermap_current"
        if dataset not in {"openweathermap_current", "openweathermap_forecast"}:
            raise ValueError(f"지원하지 않는 dataset: {dataset}")
        params = {
            "appid": self._require_key(),
            "lat": location.latitude,
            "lon": location.longitude,
            "units": "metric",
        }
        payload, metadata = self._request(
            "forecast" if dataset.endswith("forecast") else "weather", params=params
        )
        if not isinstance(payload, Mapping):
            raise ProviderError("OpenWeatherMap 응답이 object가 아닙니다.", code="schema")
        rows = list(payload.get("list", [])) if dataset.endswith("forecast") else [payload]
        values: list[tuple[Mapping[str, Any], str, Any, str | None, datetime, str | None]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            target = parse_datetime(row.get("dt", payload.get("dt")), tz=UTC)
            values.extend(
                _simple_values(
                    row,
                    target=target,
                    fields=(
                        (TEMP, "main.temp", "deg_c"),
                        (FEELS_LIKE, "main.feels_like", "deg_c"),
                        (HUMIDITY, "main.humidity", "%"),
                        (PRESSURE, "main.pressure", "hPa"),
                        (WIND_SPEED, "wind.speed", "m/s"),
                        (WIND_DIRECTION, "wind.deg", "deg"),
                        (CLOUD_COVER, "clouds.all", "%"),
                        (PRECIP, "rain.3h", "mm"),
                    ),
                )
            )
            for metric, path in ((WEATHER_CODE, ("weather", 0, "id")),):
                try:
                    current: Any = row
                    for part in path:
                        current = current[part]
                    values.append((row, metric, current, "code", target, "weather.id"))
                except (KeyError, IndexError, TypeError):
                    pass
        # Resolve dotted OpenWeather keys while retaining the raw row.
        resolved = []
        for row, metric, value, unit, target, source in values:
            if isinstance(value, str) and "." in value and value in row:
                value = row[value]
            elif value is None and source and "." in source:
                current: Any = row
                try:
                    for part in source.split("."):
                        current = current[part]
                    value = current
                except (KeyError, TypeError):
                    value = None
            resolved.append((row, metric, value, unit, target, source))
        return self._finish(
            location,
            dataset_key=dataset,
            payload=payload,
            metadata=metadata,
            rows=rows,
            values=resolved,
        )


class OpenMeteoProvider(HttpWeatherProvider):
    provider_key = "open_meteo"
    default_base_url = "https://api.open-meteo.com/v1"

    def fetch(
        self,
        location: ProviderLocation,
        *,
        dataset_key: str | None = None,
        at: datetime | None = None,
    ) -> ProviderResponse:
        dataset = dataset_key or "open_meteo_current"
        params = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "timezone": "UTC",
            "current": (
                "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,"
                "wind_speed_10m,wind_direction_10m,weather_code"
            ),
            "hourly": (
                "temperature_2m,relative_humidity_2m,precipitation_probability,precipitation,"
                "wind_speed_10m,wind_direction_10m,weather_code"
            ),
        }
        payload, metadata = self._request("forecast", params=params)
        if not isinstance(payload, Mapping):
            raise ProviderError("Open-Meteo 응답이 object가 아닙니다.", code="schema")
        current = payload.get("current") or payload.get("current_weather")
        values: list[tuple[Mapping[str, Any], str, Any, str | None, datetime, str | None]] = []
        rows: list[Mapping[str, Any]] = []
        if isinstance(current, Mapping):
            rows.append(current)
            target = parse_datetime(current.get("time"), tz=UTC)
            values.extend(
                _simple_values(
                    current,
                    target=target,
                    fields=(
                        (
                            TEMP,
                            "temperature_2m" if "temperature_2m" in current else "temperature",
                            "deg_c",
                        ),
                        (FEELS_LIKE, "apparent_temperature", "deg_c"),
                        (HUMIDITY, "relative_humidity_2m", "%"),
                        (PRECIP, "precipitation", "mm"),
                        (WIND_SPEED, "wind_speed_10m", "km/h"),
                        (WIND_DIRECTION, "wind_direction_10m", "deg"),
                        (WEATHER_CODE, "weather_code", "code"),
                    ),
                )
            )
        if dataset.endswith("forecast"):
            hourly = payload.get("hourly")
            if isinstance(hourly, Mapping) and isinstance(hourly.get("time"), list):
                for index, timestamp in enumerate(hourly["time"]):
                    row = {
                        key: values_at[index]
                        for key, values_at in hourly.items()
                        if isinstance(values_at, list) and index < len(values_at)
                    }
                    rows.append(row)
                    target = parse_datetime(timestamp, tz=UTC)
                    values.extend(
                        _simple_values(
                            row,
                            target=target,
                            fields=(
                                (TEMP, "temperature_2m", "deg_c"),
                                (FEELS_LIKE, "apparent_temperature", "deg_c"),
                                (HUMIDITY, "relative_humidity_2m", "%"),
                                (PRECIP_PROB, "precipitation_probability", "%"),
                                (PRECIP, "precipitation", "mm"),
                                (WIND_SPEED, "wind_speed_10m", "km/h"),
                                (WIND_DIRECTION, "wind_direction_10m", "deg"),
                                (WEATHER_CODE, "weather_code", "code"),
                            ),
                        )
                    )
        return self._finish(
            location,
            dataset_key=dataset,
            payload=payload,
            metadata=metadata,
            rows=rows,
            values=values,
        )


class VisualCrossingProvider(HttpWeatherProvider):
    provider_key = "visual_crossing"
    default_base_url = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services"

    def fetch(
        self,
        location: ProviderLocation,
        *,
        dataset_key: str | None = None,
        at: datetime | None = None,
    ) -> ProviderResponse:
        dataset = dataset_key or "visual_crossing_timeline"
        params = {
            "key": self._require_key(),
            "unitGroup": "metric",
            "include": "current,hours",
            "contentType": "json",
            "elements": (
                "datetime,datetimeEpoch,temp,feelslike,humidity,pressure,windspeed,winddir,"
                "precip,precipprob,cloudcover,visibility,uvindex,conditions"
            ),
        }
        payload, metadata = self._request(
            f"timeline/{location.latitude},{location.longitude}", params=params
        )
        if not isinstance(payload, Mapping):
            raise ProviderError("Visual Crossing 응답이 object가 아닙니다.", code="schema")
        timezone = _tz(payload.get("timezone"))
        rows: list[Mapping[str, Any]] = []
        values: list[tuple[Mapping[str, Any], str, Any, str | None, datetime, str | None]] = []
        candidates = [payload.get("currentConditions")] + [
            {"date": day.get("datetime"), **hour}
            for day in payload.get("days", [])
            if isinstance(day, Mapping)
            for hour in day.get("hours", [])
        ]
        for row in candidates:
            if (
                not isinstance(row, Mapping)
                or (row.get("datetime") is None and row.get("datetimeEpoch") is None)
            ):
                continue
            rows.append(row)
            target = _parse_local_clock(
                row.get("datetimeEpoch") or row.get("datetime"),
                row.get("date"),
                tz=timezone,
                fallback=at or datetime.now(UTC),
            )
            values.extend(
                _simple_values(
                    row,
                    target=target,
                    fields=(
                        (TEMP, "temp", "deg_c"),
                        (FEELS_LIKE, "feelslike", "deg_c"),
                        (HUMIDITY, "humidity", "%"),
                        (PRESSURE, "pressure", "hPa"),
                        (WIND_SPEED, "windspeed", "km/h"),
                        (WIND_DIRECTION, "winddir", "deg"),
                        (PRECIP, "precip", "mm"),
                        (PRECIP_PROB, "precipprob", "%"),
                        (CLOUD_COVER, "cloudcover", "%"),
                        (VISIBILITY, "visibility", "km"),
                        (UV_INDEX, "uvindex", "index"),
                        (WEATHER_CODE, "conditions", "code"),
                    ),
                )
            )
        return self._finish(
            location,
            dataset_key=dataset,
            payload=payload,
            metadata=metadata,
            rows=rows,
            values=values,
        )


class TomorrowIoProvider(HttpWeatherProvider):
    provider_key = "tomorrow_io"
    default_base_url = "https://api.tomorrow.io/v4"

    def fetch(
        self,
        location: ProviderLocation,
        *,
        dataset_key: str | None = None,
        at: datetime | None = None,
    ) -> ProviderResponse:
        dataset = dataset_key or "tomorrow_io_realtime"
        path = "weather/forecast" if dataset.endswith("forecast") else "weather/realtime"
        params = {"location": f"{location.latitude},{location.longitude}", "timesteps": "1h"}
        payload, metadata = self._request(
            path, params=params, headers={"apikey": self._require_key()}
        )
        if not isinstance(payload, Mapping):
            raise ProviderError("Tomorrow.io 응답이 object가 아닙니다.", code="schema")
        timelines = payload.get("timelines")
        if isinstance(timelines, Mapping):
            rows = [
                row
                for timeline in timelines.values()
                if isinstance(timeline, list)
                for row in timeline
                if isinstance(row, Mapping)
            ]
        else:
            rows = [payload.get("data", payload)]
        values = []
        for row in rows:
            values_map = row.get("values", row)
            if not isinstance(values_map, Mapping):
                continue
            target = parse_datetime(
                row.get("time") or row.get("startTime") or values_map.get("time"), tz=UTC
            )
            values.extend(
                _simple_values(
                    values_map,
                    target=target,
                    fields=(
                        (TEMP, "temperature", "deg_c"),
                        (FEELS_LIKE, "temperatureApparent", "deg_c"),
                        (HUMIDITY, "humidity", "%"),
                        (WIND_SPEED, "windSpeed", "m/s"),
                        (WIND_DIRECTION, "windDirection", "deg"),
                        (PRECIP_PROB, "precipitationProbability", "%"),
                        (PRECIP, "rainAccumulation", "mm"),
                        (WEATHER_CODE, "weatherCode", "code"),
                    ),
                )
            )
        return self._finish(
            location,
            dataset_key=dataset,
            payload=payload,
            metadata=metadata,
            rows=rows,
            values=values,
        )


class WeatherbitProvider(HttpWeatherProvider):
    provider_key = "weatherbit"
    default_base_url = "https://api.weatherbit.io/v2.0"

    def fetch(
        self,
        location: ProviderLocation,
        *,
        dataset_key: str | None = None,
        at: datetime | None = None,
    ) -> ProviderResponse:
        dataset = dataset_key or "weatherbit_current"
        params = {
            "key": self._require_key(),
            "lat": location.latitude,
            "lon": location.longitude,
            "units": "M",
        }
        if dataset.endswith("forecast"):
            params["hours"] = 48
        payload, metadata = self._request(
            "forecast/hourly" if dataset.endswith("forecast") else "current", params=params
        )
        rows = payload.get("data", []) if isinstance(payload, Mapping) else []
        values = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            target = parse_datetime(
                row.get("ts") or row.get("ob_time"),
                tz=_tz(payload.get("timezone") if isinstance(payload, Mapping) else None),
            )
            values.extend(
                _simple_values(
                    row,
                    target=target,
                    fields=(
                        (TEMP, "temp", "deg_c"),
                        (FEELS_LIKE, "app_temp", "deg_c"),
                        (HUMIDITY, "rh", "%"),
                        (PRESSURE, "pres", "hPa"),
                        (WIND_SPEED, "wind_spd", "m/s"),
                        (WIND_DIRECTION, "wind_dir", "deg"),
                        (PRECIP, "precip", "mm"),
                        (PRECIP_PROB, "pop", "%"),
                        (VISIBILITY, "vis", "km"),
                        (WEATHER_CODE, "weather.code", "code"),
                    ),
                )
            )
        resolved = []
        for row, metric, value, unit, target, source in values:
            if value is None and source == "weather.code":
                value = _nested(row, "weather", "code")
            resolved.append((row, metric, value, unit, target, source))
        return self._finish(
            location,
            dataset_key=dataset,
            payload=payload,
            metadata=metadata,
            rows=rows,
            values=resolved,
        )


class WeatherstackProvider(HttpWeatherProvider):
    provider_key = "weatherstack"
    default_base_url = "http://api.weatherstack.com"

    def fetch(
        self,
        location: ProviderLocation,
        *,
        dataset_key: str | None = None,
        at: datetime | None = None,
    ) -> ProviderResponse:
        dataset = dataset_key or "weatherstack_current"
        params = {
            "access_key": self._require_key(),
            "query": f"{location.latitude},{location.longitude}",
            "units": "m",
        }
        payload, metadata = self._request("current", params=params)
        if not isinstance(payload, Mapping) or not isinstance(payload.get("current"), Mapping):
            raise ProviderError("Weatherstack current 필드가 없습니다.", code="schema")
        row = payload["current"]
        timezone = _tz(_nested(payload, "location", "timezone_id"))
        target = _parse_local_clock(
            row.get("localObsDateTime") or row.get("observation_time"),
            _nested(payload, "location", "localtime"),
            tz=timezone,
            fallback=at or datetime.now(UTC),
        )
        values = _simple_values(
            row,
            target=target,
            fields=(
                (TEMP, "temperature", "deg_c"),
                (FEELS_LIKE, "feelslike", "deg_c"),
                (HUMIDITY, "humidity", "%"),
                (PRESSURE, "pressure", "hPa"),
                (WIND_SPEED, "wind_speed", "km/h"),
                (WIND_DIRECTION, "wind_degree", "deg"),
                (PRECIP, "precip", "mm"),
                (CLOUD_COVER, "cloudcover", "%"),
                (VISIBILITY, "visibility", "km"),
                (UV_INDEX, "uv_index", "index"),
                (WEATHER_CODE, "weather_code", "code"),
            ),
        )
        return self._finish(
            location,
            dataset_key=dataset,
            payload=payload,
            metadata=metadata,
            rows=[row],
            values=values,
        )


class AccuWeatherProvider(HttpWeatherProvider):
    provider_key = "accuweather"
    default_base_url = "https://dataservice.accuweather.com"

    def fetch(
        self,
        location: ProviderLocation,
        *,
        dataset_key: str | None = None,
        at: datetime | None = None,
    ) -> ProviderResponse:
        dataset = dataset_key or "accuweather_current"
        key = self._require_key()
        configured = location.provider_metadata.get(
            "accuweather_location_key"
        ) or location.provider_metadata.get("location_key")
        if not configured:
            lookup, lookup_meta = self._request(
                "locations/v1/cities/geoposition/search",
                params={"apikey": key, "q": f"{location.latitude},{location.longitude}"},
            )
            if not isinstance(lookup, Mapping) or not lookup.get("Key"):
                raise ProviderError("AccuWeather location key를 찾지 못했습니다.", code="schema")
            configured = lookup["Key"]
        if dataset.endswith("forecast"):
            payload, metadata = self._request(
                f"forecasts/v1/hourly/12hour/{quote(str(configured), safe='')}",
                params={"apikey": key, "metric": "true"},
            )
        else:
            payload, metadata = self._request(
                f"currentconditions/v1/{quote(str(configured), safe='')}",
                params={"apikey": key, "details": "true", "metric": "true"},
            )
        rows = payload if isinstance(payload, list) else [payload]
        values = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            target = parse_datetime(row.get("DateTime") or row.get("EpochTime"), tz=UTC)
            values.extend(
                _simple_values(
                    row,
                    target=target,
                    fields=(
                        (TEMP, "Temperature.Metric.Value", "deg_c"),
                        (FEELS_LIKE, "RealFeelTemperature.Metric.Value", "deg_c"),
                        (HUMIDITY, "RelativeHumidity", "%"),
                        (WIND_SPEED, "Wind.Speed.Metric.Value", "km/h"),
                        (WIND_DIRECTION, "Wind.Direction.Degrees", "deg"),
                        (PRECIP, "PrecipitationSummary.PastHour.Metric.Value", "mm"),
                        (PRECIP_PROB, "PrecipitationProbability", "%"),
                        (WEATHER_CODE, "WeatherIcon", "code"),
                    ),
                )
            )
        resolved = []
        for row, metric, value, unit, target, source in values:
            if value is None:
                current = row
                try:
                    for part in source.split("."):
                        current = current[part]
                    value = current
                except (KeyError, TypeError):
                    value = None
            resolved.append((row, metric, value, unit, target, source))
        return self._finish(
            location,
            dataset_key=dataset,
            payload=payload if isinstance(payload, Mapping) else {"items": payload},
            metadata=metadata,
            rows=rows,
            values=resolved,
        )


class WttrInProvider(HttpWeatherProvider):
    provider_key = "wttr_in"
    default_base_url = "https://wttr.in"

    def fetch(
        self,
        location: ProviderLocation,
        *,
        dataset_key: str | None = None,
        at: datetime | None = None,
    ) -> ProviderResponse:
        dataset = dataset_key or "wttr_in_current"
        place = (
            location.provider_metadata.get("wttr_location")
            or f"{location.latitude},{location.longitude}"
        )
        payload, metadata = self._request(
            f"{quote(str(place), safe=',-')}", params={"format": "j1", "m": ""}
        )
        if not isinstance(payload, Mapping):
            raise ProviderError("wttr.in 응답이 object가 아닙니다.", code="schema")
        current = payload.get("current_condition", [{}])[0]
        timezone = _tz(_nested(payload, "nearest_area", 0, "timezone", 0, "value"))
        rows = [current] if isinstance(current, Mapping) else []
        values = []
        if isinstance(current, Mapping):
            date_text = (
                _nested(payload, "weather", 0, "date") or datetime.now(timezone).date().isoformat()
            )
            target = _parse_local_clock(
                current.get("localObsDateTime") or current.get("observation_time"),
                date_text,
                tz=timezone,
                fallback=at or datetime.now(UTC),
            )
            values.extend(
                _simple_values(
                    current,
                    target=target,
                    fields=(
                        (TEMP, "temp_C", "deg_c"),
                        (FEELS_LIKE, "FeelsLikeC", "deg_c"),
                        (HUMIDITY, "humidity", "%"),
                        (PRESSURE, "pressure", "hPa"),
                        (WIND_SPEED, "windspeedKmph", "km/h"),
                        (WIND_DIRECTION, "winddirDegree", "deg"),
                        (PRECIP, "precipMM", "mm"),
                        (VISIBILITY, "visibility", "km"),
                        (UV_INDEX, "uvIndex", "index"),
                        (WEATHER_CODE, "weatherCode", "code"),
                    ),
                )
            )
        if dataset.endswith("forecast"):
            for day in payload.get("weather", []):
                if not isinstance(day, Mapping):
                    continue
                date_text = day.get("date")
                for hour in day.get("hourly", []):
                    if not isinstance(hour, Mapping):
                        continue
                    row = {**hour, "date": date_text}
                    rows.append(row)
                    target = parse_datetime(
                        f"{date_text} {int(hour.get('time', 0)):04d}", tz=timezone
                    )
                    values.extend(
                        _simple_values(
                            row,
                            target=target,
                            fields=(
                                (TEMP, "tempC", "deg_c"),
                                (FEELS_LIKE, "FeelsLikeC", "deg_c"),
                                (HUMIDITY, "humidity", "%"),
                                (WIND_SPEED, "windspeedKmph", "km/h"),
                                (WIND_DIRECTION, "winddirDegree", "deg"),
                                (PRECIP, "precipMM", "mm"),
                                (PRECIP_PROB, "chanceofrain", "%"),
                                (WEATHER_CODE, "weatherCode", "code"),
                            ),
                        )
                    )
        return self._finish(
            location,
            dataset_key=dataset,
            payload=payload,
            metadata=metadata,
            rows=rows,
            values=values,
        )


PROVIDER_ADAPTERS: dict[str, type[HttpWeatherProvider]] = {
    "weatherapi": WeatherApiProvider,
    "openweathermap": OpenWeatherMapProvider,
    "open_meteo": OpenMeteoProvider,
    "visual_crossing": VisualCrossingProvider,
    "tomorrow_io": TomorrowIoProvider,
    "weatherbit": WeatherbitProvider,
    "weatherstack": WeatherstackProvider,
    "accuweather": AccuWeatherProvider,
    "wttr_in": WttrInProvider,
}

# ``*Adapter`` aliases keep the boundary obvious to callers that use the
# adapter terminology while the longer provider names remain discoverable.
WeatherApiAdapter = WeatherApiProvider
OpenWeatherMapAdapter = OpenWeatherMapProvider
OpenMeteoAdapter = OpenMeteoProvider
VisualCrossingAdapter = VisualCrossingProvider
TomorrowIoAdapter = TomorrowIoProvider
WeatherbitAdapter = WeatherbitProvider
WeatherstackAdapter = WeatherstackProvider
AccuWeatherAdapter = AccuWeatherProvider
WttrInAdapter = WttrInProvider
WeatherAPIAdapter = WeatherApiProvider
TomorrowIOAdapter = TomorrowIoProvider


def create_provider(provider_key: str, **kwargs: Any) -> WeatherProvider:
    try:
        cls = PROVIDER_ADAPTERS[provider_key]
    except KeyError as exc:
        raise ValueError(f"지원하지 않는 external provider: {provider_key}") from exc
    return cls(**kwargs)
