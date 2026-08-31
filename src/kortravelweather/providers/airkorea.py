"""AirKorea 측정소를 weather anchor와 관측 fact로 연결하는 adapter."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from airkorea import AirKoreaClient
from airkorea.models import AirQualityMeasurement, Station

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue

AIRKOREA_PROVIDER = "python-airkorea-api"
AIRKOREA_STATION_DATASET = "airkorea_station_catalog"
AIRKOREA_MEASUREMENT_DATASET = "airkorea_realtime_measurement"


def _slug(value: str) -> str:
    """Return an ASCII-safe, deterministic station suffix.

    AirKorea's public station list does not consistently expose a station
    code.  Korean names are not valid ``WeatherLocation.location_id`` values,
    and names alone collide (there are multiple ``중구`` stations), so retain a
    readable ASCII slug only when possible and append a stable digest for
    non-ASCII/ambiguous names at the call site.
    """
    normalized = re.sub(r"[^0-9A-Za-z]+", "-", value.strip().lower()).strip("-")
    return normalized or "station"


def station_code(station: Station) -> str | None:
    """Return an optional AirKorea station id when present in the raw row."""
    raw = station.raw
    for key in ("stationCode", "stationId", "stationNo", "dmCode"):
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def station_location(station: Station) -> WeatherLocation | None:
    """Convert an AirKorea station with WGS84 coordinates to a safe anchor."""
    if station.lat is None or station.lon is None:
        return None
    code = station_code(station)
    suffix = code or (
        f"{_slug(station.station_name)}-"
        f"{hashlib.sha256(f'{station.station_name}|{station.addr}|{station.lat}|{station.lon}'.encode()).hexdigest()[:12]}"
    )
    metadata: dict[str, Any] = {
        "measurement_point": {
            "provider": AIRKOREA_PROVIDER,
            "station_id": code,
            "station_name": station.station_name,
            "address": station.addr,
            "network": station.mang_name,
        }
    }
    return WeatherLocation(
        location_id=f"airkorea-{suffix}",
        name=station.station_name,
        latitude=station.lat,
        longitude=station.lon,
        region_code=station.addr,
        metadata=metadata,
    )


def station_source_record(station: Station, *, fetched_at: datetime) -> dict[str, Any]:
    """Build a deterministic station-catalog lineage record."""
    raw = dict(station.raw)
    canonical = json.dumps(
        raw, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    key = "sr_" + hashlib.sha256(
        f"{AIRKOREA_STATION_DATASET}:{canonical}".encode()
    ).hexdigest()[:48]
    return {
        "source_record_key": key,
        "provider": AIRKOREA_PROVIDER,
        "dataset_key": AIRKOREA_STATION_DATASET,
        "source_entity_type": "airkorea_station",
        "source_entity_id": station_code(station) or station.station_name,
        "payload": {"station": raw},
        "fetched_at": fetched_at,
    }


_MEASUREMENT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("PM10", "pm10_value", "ug/m3"),
    ("PM25", "pm25_value", "ug/m3"),
    ("O3", "o3_value", "ppm"),
    ("NO2", "no2_value", "ppm"),
    ("SO2", "so2_value", "ppm"),
    ("CO", "co_value", "ppm"),
    ("KHAI", "khai_value", "index"),
)


def measurement_source_record(
    measurement: AirQualityMeasurement,
    *,
    location_id: str,
    fetched_at: datetime,
) -> dict[str, Any]:
    raw = dict(measurement.raw)
    canonical = json.dumps(
        raw, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    key = "sr_" + hashlib.sha256(
        f"{AIRKOREA_MEASUREMENT_DATASET}:{location_id}:{canonical}".encode()
    ).hexdigest()[:48]
    return {
        "source_record_key": key,
        "provider": AIRKOREA_PROVIDER,
        "dataset_key": AIRKOREA_MEASUREMENT_DATASET,
        "source_entity_type": "airkorea_station",
        "source_entity_id": location_id,
        "payload": {"measurement": raw},
        "fetched_at": fetched_at,
    }


def measurement_to_weather_values(
    measurement: AirQualityMeasurement,
    *,
    location_id: str,
    source_record_key: str,
    known_at: datetime,
) -> list[WeatherValue]:
    observed_at = measurement.data_time or known_at
    values: list[WeatherValue] = []
    for metric, attribute, unit in _MEASUREMENT_FIELDS:
        raw_value = getattr(measurement, attribute)
        if raw_value is None:
            continue
        values.append(
            WeatherValue(
                location_id=location_id,
                provider=AIRKOREA_PROVIDER,
                dataset_key=AIRKOREA_MEASUREMENT_DATASET,
                weather_domain="air_quality",
                forecast_style=ForecastStyle.OBSERVED,
                metric_key=metric,
                metric_name={
                    "PM10": "미세먼지 PM10",
                    "PM25": "초미세먼지 PM2.5",
                    "O3": "오존",
                    "NO2": "이산화질소",
                    "SO2": "아황산가스",
                    "CO": "일산화탄소",
                    "KHAI": "통합대기환경지수",
                }[metric],
                source_metric_key=attribute,
                value_number=Decimal(str(raw_value)),
                unit=unit,
                observed_at=observed_at,
                target_at=observed_at,
                known_at=known_at,
                collected_at=known_at,
                payload={
                    "station_name": measurement.station_name,
                    "data_time": measurement.data_time.isoformat()
                    if measurement.data_time
                    else None,
                    "raw_metric": attribute,
                },
                source_record_key=source_record_key,
            )
        )
    return values


def fetch_station_catalog(
    client: AirKoreaClient,
    *,
    max_stations: int = 1000,
) -> list[tuple[WeatherLocation, dict[str, Any]]]:
    """Fetch and normalize a bounded, paginated AirKorea station catalog.

    The public endpoint currently caps ``numOfRows`` at a provider-defined
    page size (100).  Walking pages prevents a successful first response from
    silently dropping the rest of the nationwide station catalog.
    """
    page_size = min(100, max_stations)
    stations: list[Station] = []
    page_no = 1
    while len(stations) < max_stations:
        page = client.stations(page_no=page_no, num_of_rows=page_size)
        if not page:
            break
        remaining = max_stations - len(stations)
        stations.extend(page[:remaining])
        if len(page) < page_size:
            break
        page_no += 1
    fetched_at = datetime.now(UTC)
    result: list[tuple[WeatherLocation, dict[str, Any]]] = []
    for station in stations[:max_stations]:
        location = station_location(station)
        if location is None:
            continue
        result.append((location, station_source_record(station, fetched_at=fetched_at)))
    return result


def fetch_station_measurement(
    client: AirKoreaClient,
    *,
    station_name: str,
    location_id: str,
    known_at: datetime,
    expected_sido: str | None = None,
) -> tuple[dict[str, Any], list[WeatherValue]] | None:
    measurement = client.latest_station_measurement(station_name)
    if measurement is None:
        return None
    # ``stationName`` is the only selector exposed by python-airkorea-api.
    # Validate the returned identity before attaching it to an anchor so a
    # duplicate name from another province cannot silently cross-contaminate
    # the catalog.  Callers may pass the first address token as an additional
    # guard; a mismatch is quarantined (None) rather than published.
    if measurement.station_name.strip() != station_name.strip():
        raise ValueError(
            f"AirKorea 측정소 응답 이름이 요청과 다릅니다: "
            f"expected={station_name!r} got={measurement.station_name!r}"
        )
    if expected_sido and measurement.sido_name:
        returned_sido = measurement.sido_name.strip()
        if returned_sido and returned_sido != expected_sido.strip():
            raise ValueError(
                "AirKorea 측정소 응답 시도가 요청 anchor와 다릅니다: "
                f"expected={expected_sido!r} got={returned_sido!r}"
            )
    source = measurement_source_record(
        measurement,
        location_id=location_id,
        fetched_at=known_at,
    )
    return source, measurement_to_weather_values(
        measurement,
        location_id=location_id,
        source_record_key=source["source_record_key"],
        known_at=known_at,
    )


def measurement_point_metadata(location: WeatherLocation) -> Mapping[str, Any] | None:
    value = location.metadata.get("measurement_point")
    return value if isinstance(value, Mapping) else None
