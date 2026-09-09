"""한국도로공사 휴게소 기상 adapter.

Like the mountain network, one call covers the whole country and every row
carries its own coordinates, so there is no catalog dataset and no per-location
fan-out.

Two things are specific to this source.  It authenticates with a
``data.ex.co.kr`` key rather than the ``data.go.kr`` key every other Korean
public source here uses, so it has its own credential field.  And its feed lags
by an unpredictable number of hours, which is why the client's
``latest_weather`` walks backwards until it finds a populated hour instead of
asking for "now" and getting an empty page.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from krex import KrexClient, RestAreaWeather

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.providers.base import jsonable, make_source_record

KREX_PROVIDER = "python-krex-api"
KREX_RESTAREA_DATASET = "krex_restarea_weather"

_METRIC_FIELDS: tuple[tuple[str, str, str | None, str], ...] = (
    ("TEMP", "temperature", "℃", "기온"),
    ("HUMIDITY", "humidity", "%", "상대습도"),
    ("WIND_SPEED", "wind_speed", "m/s", "풍속"),
    ("RAINFALL", "rainfall", "mm", "강수량"),
    ("RAINFALL_STRENGTH", "rainfall_strength", "mm/h", "강수강도"),
    ("NEW_SNOW", "new_snow", "cm", "신적설"),
    ("SNOW", "snow", "cm", "적설"),
    ("CLOUD", "cloud", None, "운량"),
    ("DEW_POINT", "dew_point", "℃", "이슬점"),
)


def _slug(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z]+", "-", value.strip().lower()).strip("-")
    return normalized or "restarea"


def restarea_location(record: RestAreaWeather) -> WeatherLocation | None:
    """Convert one rest-area reading into a stable anchor, or ``None``."""
    if record.lat is None or record.lon is None:
        return None
    name = (record.unit_name or "").strip() or "휴게소"
    code = (record.unit_code or "").strip()
    suffix = code or (
        f"{_slug(name)}-"
        + hashlib.sha256(
            f"{name}|{record.route_no}|{record.lat}|{record.lon}".encode()
        ).hexdigest()[:12]
    )
    return WeatherLocation(
        location_id=f"krex-{suffix}",
        name=name,
        latitude=record.lat,
        longitude=record.lon,
        region_code=(record.address or "").strip()[:32] or None,
        metadata={
            "measurement_point": {
                "provider": KREX_PROVIDER,
                "station_id": code or None,
                "station_name": name,
                "route_no": record.route_no,
                "route_name": record.route_name,
                "direction_code": record.direction_code,
                "measurement_station": record.measurement_station,
                "address": record.address,
                "network": "고속도로 휴게소 기상관측",
            }
        },
    )


def restarea_source_record(
    record: RestAreaWeather, *, location_id: str, fetched_at: datetime
) -> dict[str, Any]:
    """One lineage record per station.

    The upstream call is nationwide, so the tempting shape is one record per
    response.  The repository refuses it: a ``weather_response`` entity must
    name the location its facts belong to, or a fact could claim provenance it
    does not have.  Per-station is also the more useful grain -- it says which
    reading a value came from, not merely which fetch.
    """
    return make_source_record(
        provider=KREX_PROVIDER,
        dataset_key=KREX_RESTAREA_DATASET,
        location_id=location_id,
        payload=jsonable(record.raw),
        endpoint="/openapi/restinfo/restWeatherList",
        fetched_at=fetched_at,
    )


def restarea_weather_to_weather_values(
    record: RestAreaWeather,
    *,
    location_id: str,
    source_record_key: str,
    known_at: datetime,
) -> list[WeatherValue]:
    observed_at = record.observed_at or known_at
    values: list[WeatherValue] = []
    for metric, attribute, unit, metric_name in _METRIC_FIELDS:
        raw_value = getattr(record, attribute)
        if raw_value is None:
            continue
        values.append(
            WeatherValue(
                location_id=location_id,
                provider=KREX_PROVIDER,
                dataset_key=KREX_RESTAREA_DATASET,
                weather_domain="highway_weather",
                forecast_style=ForecastStyle.OBSERVED,
                metric_key=metric,
                metric_name=metric_name,
                source_metric_key=attribute,
                value_number=Decimal(str(raw_value)),
                unit=unit,
                observed_at=observed_at,
                target_at=observed_at,
                known_at=known_at,
                collected_at=known_at,
                payload={
                    "unit_code": record.unit_code,
                    "unit_name": record.unit_name,
                    "route_name": record.route_name,
                    "observed_at": observed_at.isoformat(),
                    "raw_metric": attribute,
                },
                source_record_key=source_record_key,
            )
        )
    # The textual sky condition has no number, so it rides as its own value
    # rather than being dropped or forced into ``value_number``.
    if (record.weather or "").strip():
        values.append(
            WeatherValue(
                location_id=location_id,
                provider=KREX_PROVIDER,
                dataset_key=KREX_RESTAREA_DATASET,
                weather_domain="highway_weather",
                forecast_style=ForecastStyle.OBSERVED,
                metric_key="WEATHER_TEXT",
                metric_name="날씨",
                source_metric_key="weather",
                value_text=record.weather.strip(),
                observed_at=observed_at,
                target_at=observed_at,
                known_at=known_at,
                collected_at=known_at,
                payload={"unit_code": record.unit_code, "raw_metric": "weather"},
                source_record_key=source_record_key,
            )
        )
    return values


def fetch_restarea_weather(
    client: KrexClient,
    *,
    max_records: int = 2000,
    lookback_hours: int = 24,
) -> list[RestAreaWeather]:
    """Fetch the most recent populated hour, bounded.

    The feed publishes late and unevenly, so asking for the current hour
    routinely returns nothing; ``latest_weather`` walks back until it finds
    data.  ``lookback_hours`` bounds that walk -- without it, an outage turns
    every run into a long series of empty requests.
    """
    page = client.restarea.latest_weather(lookback_hours=lookback_hours)
    return list(page.items)[:max_records]


def measurement_point_metadata(location: WeatherLocation) -> Mapping[str, Any] | None:
    point = location.metadata.get("measurement_point")
    return point if isinstance(point, Mapping) else None
