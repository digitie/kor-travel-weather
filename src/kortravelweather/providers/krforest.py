"""산림청 산악기상관측망 adapter.

One call returns every mountain station nationwide, each row already carrying
its own coordinates, so this source has no per-location fan-out and no separate
catalog fetch -- unlike AirKorea, where the station list is its own dataset.

The upstream client is async-only, which nothing else here is.  The boundary is
drawn at :func:`fetch_mountain_weather`: it is an ordinary synchronous function
that owns an event loop for the duration of one call, so Dagster, the
repository, and every caller stay synchronous.  Pushing async any further up
would mean an async path through the repository for one provider out of eleven.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from krforest import ForestClient, MountainWeather

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.providers.base import (
    jsonable,
    make_source_record,
    safe_location_suffix,
)

KRFOREST_PROVIDER = "python-krforest-api"
KRFOREST_MOUNTAIN_DATASET = "krforest_mountain_weather"

#: ``(metric_key, MountainWeather attribute, unit, Korean name)``.
#:
#: The 2 m and 10 m heights are kept as separate metrics rather than collapsed
#: to one: on a ridge they differ by more than the instrument error, and a
#: reader who asked for wind at 10 m should not silently receive 2 m.
_METRIC_FIELDS: tuple[tuple[str, str, str | None, str], ...] = (
    ("TEMP", "temperature_2m", "℃", "기온 2m"),
    ("TEMP_10M", "temperature_10m", "℃", "기온 10m"),
    ("HUMIDITY", "humidity_2m", "%", "상대습도 2m"),
    ("HUMIDITY_10M", "humidity_10m", "%", "상대습도 10m"),
    ("PRESSURE", "pressure", "hPa", "기압"),
    ("RAINFALL", "rainfall_tipping", "mm", "강수량 전도형"),
    ("RAINFALL_WEIGHT", "rainfall_weight", "mm", "강수량 중량식"),
    ("GROUND_TEMP", "ground_temperature", "℃", "지면온도"),
    ("WIND_SPEED", "wind_speed_2m", "m/s", "풍속 2m"),
    ("WIND_SPEED_10M", "wind_speed_10m", "m/s", "풍속 10m"),
    ("WIND_DIRECTION", "wind_direction_2m", "deg", "풍향 2m"),
    ("WIND_DIRECTION_10M", "wind_direction_10m", "deg", "풍향 10m"),
)


def station_location(record: MountainWeather) -> WeatherLocation | None:
    """Convert one observation row into a stable anchor, or ``None``.

    A row without coordinates cannot be placed on the map and cannot be joined
    to anything else, so it is dropped rather than anchored at (0, 0).
    """
    if record.latitude is None or record.longitude is None:
        return None
    name = (record.obs_name or "").strip() or "산악기상관측소"
    code = (record.obs_id or "").strip()
    suffix = safe_location_suffix(
        code,
        discriminator=f"{code}|{name}|{record.local_area}|"
        f"{record.latitude}|{record.longitude}",
    )
    return WeatherLocation(
        location_id=f"krforest-{suffix}",
        name=name,
        latitude=record.latitude,
        longitude=record.longitude,
        region_code=(record.local_area or "").strip()[:32] or None,
        metadata={
            "measurement_point": {
                "provider": KRFOREST_PROVIDER,
                "station_id": code or None,
                "station_name": name,
                "local_area": record.local_area,
                "network": "산악기상관측망",
            }
        },
    )


def mountain_source_record(
    record: MountainWeather, *, location_id: str, fetched_at: datetime
) -> dict[str, Any]:
    """One lineage record per station.

    The upstream call is nationwide, so the tempting shape is one record per
    response.  The repository refuses it: a ``weather_response`` entity must
    name the location its facts belong to, or a fact could claim provenance it
    does not have.  Per-station is also the more useful grain -- it says which
    reading a value came from, not merely which fetch.
    """
    return make_source_record(
        provider=KRFOREST_PROVIDER,
        dataset_key=KRFOREST_MOUNTAIN_DATASET,
        location_id=location_id,
        payload=jsonable(record.raw),
        endpoint="/mtweather/mountListSearch",
        fetched_at=fetched_at,
    )


def mountain_weather_to_weather_values(
    record: MountainWeather,
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
                provider=KRFOREST_PROVIDER,
                dataset_key=KRFOREST_MOUNTAIN_DATASET,
                weather_domain="mountain_weather",
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
                    "station_id": record.obs_id,
                    "station_name": record.obs_name,
                    "observed_at": observed_at.isoformat(),
                    "raw_metric": attribute,
                },
                source_record_key=source_record_key,
            )
        )
    return values


def fetch_mountain_weather(
    *,
    api_key: str,
    max_records: int = 2000,
    page_size: int = 500,
    timeout: float | None = None,
) -> list[MountainWeather]:
    """Fetch mountain observations, bounded, from a synchronous caller.

    ``max_records`` is a budget, not a filter: the walk stops once it is
    reached, so a station catalog that grows upstream cannot turn one run into
    an unbounded fetch.
    """
    return asyncio.run(
        _fetch_mountain_weather(
            api_key=api_key,
            max_records=max_records,
            page_size=page_size,
            client_timeout=timeout,
        )
    )


async def _fetch_mountain_weather(
    *, api_key: str, max_records: int, page_size: int, client_timeout: float | None
) -> list[MountainWeather]:
    # Named ``client_timeout`` rather than ``timeout``: it configures the HTTP
    # client, it is not a deadline on this coroutine, and calling it ``timeout``
    # makes ruff's ASYNC109 read it as one.
    collected: list[MountainWeather] = []
    async with ForestClient(api_key=api_key, timeout=client_timeout) as client:
        page_no = 1
        while len(collected) < max_records:
            page = await client.travel.mountain_weather(
                page_no=page_no, num_of_rows=min(page_size, max_records - len(collected))
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
