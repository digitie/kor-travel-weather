"""산림청 국립산림과학원 청정넷(AICAN) 미세먼지 adapter.

Two data.go.kr datasets, and they are approved separately:

- ``15078005`` 측정데이터 -- PM10/PM2.5/PM1.0 plus temperature, humidity and
  wind, at ten-minute marks.  This is the one that carries the readings.
- ``15078013`` 운영현황 -- the station catalog, and the *only* place the
  coordinates live.  A measurement row names a ``station_code`` and nothing
  else, so without this dataset a reading cannot be placed anywhere.

That split is why this source can be half-available: a key approved for the
first and not the second fetches perfectly good numbers that cannot be
anchored.  :func:`fetch_dust` reports that as a skip naming the dataset to
apply for, rather than as a failure -- approval takes days, and a schedule that
goes red every morning for something nobody can fix that morning is noise.

The date filter is exclusive at both ends: ``start_date`` and ``end_date`` set
to the same day return nothing at all, which is why the window is widened by a
day on each side and then trimmed here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from krforest import ForestClient, ForestDustMeasurement, ForestDustStation
from krforest.exceptions import ForestAuthError

from kortravelweather.models import KST, ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.providers.base import (
    jsonable,
    make_source_record,
    safe_location_suffix,
)

KRFOREST_PROVIDER = "python-krforest-api"
KRFOREST_DUST_DATASET = "krforest_dust"

#: The station catalog this source cannot work without.
STATION_DATASET_ID = "15078013"

_METRIC_FIELDS: tuple[tuple[str, str, str | None, str], ...] = (
    ("PM10", "pm10", "㎍/㎥", "미세먼지 PM10"),
    ("PM25", "pm25", "㎍/㎥", "초미세먼지 PM2.5"),
    ("PM01", "pm01", "㎍/㎥", "극초미세먼지 PM1.0"),
    ("TEMP", "temperature", "℃", "기온"),
    ("HUMIDITY", "humidity", "%", "상대습도"),
    ("WIND_SPEED", "wind_speed", "m/s", "풍속"),
    ("WIND_DIRECTION", "wind_direction", "deg", "풍향"),
)


class DustCatalogUnavailable(RuntimeError):
    """The station catalog is not approved for this service key.

    Distinct from an outage on purpose: the caller reports this as a skip and
    keeps running, because the remedy is a 활용신청 that takes days.
    """


def station_location(station: ForestDustStation) -> WeatherLocation | None:
    """Convert a 청정넷 station into an anchor, or ``None`` without coordinates."""
    if station.latitude is None or station.longitude is None:
        return None
    code = (station.station_code or "").strip()
    name = (station.description or "").strip() or f"청정넷 {code or '관측소'}"
    address = (station.address or "").strip()
    suffix = safe_location_suffix(
        code, discriminator=f"{code}|{name}|{station.latitude}|{station.longitude}"
    )
    return WeatherLocation(
        location_id=f"krforest-dust-{suffix}",
        name=name[:200],
        latitude=station.latitude,
        longitude=station.longitude,
        region_code=address[:32] or None,
        metadata={
            "measurement_point": {
                "provider": KRFOREST_PROVIDER,
                "station_id": code or None,
                "station_name": name,
                "address": address or None,
                "elevation": station.elevation,
                "network": "청정넷(AICAN)",
            }
        },
    )


def dust_source_record(
    measurement: ForestDustMeasurement, *, location_id: str, fetched_at: datetime
) -> dict[str, Any]:
    """One lineage record per reading.

    Ten-minute marks mean many readings per station per run, and each is its own
    fact; digesting the row keeps a replayed fetch on the record it already
    wrote instead of creating one per run.
    """
    return make_source_record(
        provider=KRFOREST_PROVIDER,
        dataset_key=KRFOREST_DUST_DATASET,
        location_id=location_id,
        payload=jsonable(measurement.raw),
        endpoint="/AicanDustData/dustData",
        fetched_at=fetched_at,
    )


def dust_to_weather_values(
    measurement: ForestDustMeasurement,
    *,
    location_id: str,
    source_record_key: str,
    known_at: datetime,
) -> list[WeatherValue]:
    observed_at = measurement.observed_at or known_at
    values: list[WeatherValue] = []
    for metric, attribute, unit, metric_name in _METRIC_FIELDS:
        raw_value = getattr(measurement, attribute)
        if raw_value is None:
            continue
        values.append(
            WeatherValue(
                location_id=location_id,
                provider=KRFOREST_PROVIDER,
                dataset_key=KRFOREST_DUST_DATASET,
                weather_domain="air_quality",
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
                    "station_code": measurement.station_code,
                    "observed_at": observed_at.isoformat(),
                    "raw_metric": attribute,
                },
                source_record_key=source_record_key,
            )
        )
    return values


def fetch_dust(
    *,
    api_key: str,
    hours: int = 3,
    max_records: int = 20_000,
    page_size: int = 1000,
    timeout: float | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, ForestDustStation], list[ForestDustMeasurement]]:
    """Return the station catalog and the readings from the last ``hours``.

    Raises :class:`DustCatalogUnavailable` when the key is not approved for the
    catalog dataset, because the readings are useless without it.
    """
    return asyncio.run(
        _fetch_dust(
            api_key=api_key,
            hours=hours,
            max_records=max_records,
            page_size=page_size,
            client_timeout=timeout,
            now=now or datetime.now(KST),
        )
    )


async def _fetch_dust(
    *,
    api_key: str,
    hours: int,
    max_records: int,
    page_size: int,
    client_timeout: float | None,
    now: datetime,
) -> tuple[dict[str, ForestDustStation], list[ForestDustMeasurement]]:
    async with ForestClient(api_key=api_key, timeout=client_timeout) as client:
        try:
            stations = await _fetch_stations(client, page_size=page_size)
        except ForestAuthError as exc:
            raise DustCatalogUnavailable(
                f"청정넷 운영현황({STATION_DATASET_ID}) 활용신청이 필요합니다. "
                "측정데이터는 조회되지만 관측소 좌표가 없어 앵커를 만들 수 없습니다."
            ) from exc
        cutoff = now - timedelta(hours=hours)
        measurements = await _fetch_measurements(
            client,
            # Both bounds are exclusive, so widen by a day and trim below.
            start_date=(cutoff - timedelta(days=1)).strftime("%Y%m%d"),
            end_date=(now + timedelta(days=1)).strftime("%Y%m%d"),
            max_records=max_records,
            page_size=page_size,
        )
    recent = [
        row
        for row in measurements
        if row.observed_at is not None and row.observed_at >= cutoff
    ]
    return stations, recent


async def _fetch_stations(
    client: ForestClient, *, page_size: int
) -> dict[str, ForestDustStation]:
    stations: dict[str, ForestDustStation] = {}
    page_no = 1
    while True:
        page = await client.safety.dust_stations(page_no=page_no, num_of_rows=page_size)
        items = list(page.items)
        if not items:
            break
        for station in items:
            code = (station.station_code or "").strip()
            if code:
                stations[code] = station
        if not getattr(page, "has_next_page", False):
            break
        page_no += 1
    return stations


async def _fetch_measurements(
    client: ForestClient,
    *,
    start_date: str,
    end_date: str,
    max_records: int,
    page_size: int,
) -> list[ForestDustMeasurement]:
    collected: list[ForestDustMeasurement] = []
    page_no = 1
    while len(collected) < max_records:
        page = await client.safety.dust_measurements(
            start_date=start_date,
            end_date=end_date,
            page_no=page_no,
            num_of_rows=min(page_size, max_records - len(collected)),
        )
        items = list(page.items)
        if not items:
            break
        collected.extend(items)
        if not getattr(page, "has_next_page", False):
            break
        page_no += 1
    return collected[:max_records]


def measurement_point_metadata(location: WeatherLocation) -> Mapping[str, Any] | None:
    point = location.metadata.get("measurement_point")
    return point if isinstance(point, Mapping) else None


def pair_with_stations(
    measurements: Sequence[ForestDustMeasurement],
    stations: Mapping[str, ForestDustStation],
) -> list[tuple[ForestDustMeasurement, ForestDustStation]]:
    """Drop readings whose station is not in the catalog.

    A reading with no station cannot be placed on the map, and guessing a
    location for it would be worse than losing it.
    """
    paired: list[tuple[ForestDustMeasurement, ForestDustStation]] = []
    for row in measurements:
        code = (row.station_code or "").strip()
        station = stations.get(code)
        if station is not None:
            paired.append((row, station))
    return paired
