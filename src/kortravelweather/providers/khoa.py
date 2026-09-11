"""국립해양조사원 해수욕장 해양지수 adapter.

KHOA publishes 184 services.  This adapter takes one of them -- the beach
index -- because it is the only marine dataset that is nationwide in a single
call *and* carries coordinates, so it needs neither a station-catalog dataset
nor a request per site.  The tide-station observations (``dt_recent``,
``survey_wind`` and friends) are richer, but each requires an ``obsCode`` and
therefore a per-station fan-out; they are a separate piece of work.

Unlike the mountain and highway sources, these rows are **forecasts**, not
observations: each place carries a per-day outlook.  They are recorded as such
so a bundle read does not present tomorrow's wave height as a current reading.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, time
from decimal import Decimal
from typing import Any

from khoa import BeachIndexPlace, KhoaClient

from kortravelweather.models import KST, ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.providers.base import (
    jsonable,
    make_source_record,
    safe_location_suffix,
)

KHOA_PROVIDER = "python-khoa-api"
KHOA_BEACH_INDEX_DATASET = "khoa_beach_index"

#: KHOA issues two outlooks for the same day -- morning and afternoon -- and
#: they carry different wave heights and temperatures.  Dating both at midnight
#: made them the same fact, which the append-only table correctly refused
#: ("immutable weather fact 충돌") on the first live run.  The period *is* the
#: target time, so it belongs in ``target_at``.
#:
#: An unrecognised period lands at noon.  Two unrecognised periods on one day
#: would then collide and fail the run -- loudly, with no bad data written,
#: which is the right failure for a vocabulary that grew upstream.
_PERIOD_HOURS = {"오전": 9, "오후": 15}
_UNKNOWN_PERIOD_HOUR = 12

_METRIC_FIELDS: tuple[tuple[str, str, str | None, str], ...] = (
    ("WAVE_HEIGHT", "max_wave_height_m", "m", "최대 파고"),
    ("WATER_TEMP", "average_water_temperature_c", "℃", "평균 수온"),
    ("TEMP", "average_air_temperature_c", "℃", "평균 기온"),
    ("WIND_SPEED", "max_wind_speed_m_s", "m/s", "최대 풍속"),
    ("BEACH_INDEX", "total_index", None, "해수욕장 지수"),
)


def beach_location(place: BeachIndexPlace) -> WeatherLocation | None:
    """Convert one beach into a stable anchor, or ``None`` without coordinates."""
    if place.latitude is None or place.longitude is None:
        return None
    name = (place.name or "").strip() or "해수욕장"
    code = (place.id or "").strip()
    suffix = safe_location_suffix(
        code, discriminator=f"{code}|{name}|{place.latitude}|{place.longitude}"
    )
    address = (place.road_address or place.parcel_address or "").strip()
    return WeatherLocation(
        location_id=f"khoa-{suffix}",
        name=name,
        latitude=place.latitude,
        longitude=place.longitude,
        region_code=address[:32] or None,
        metadata={
            "measurement_point": {
                "provider": KHOA_PROVIDER,
                "station_id": code or None,
                "station_name": name,
                "address": address or None,
                "network": "해수욕장 해양지수",
            }
        },
    )


def beach_index_source_record(
    place: BeachIndexPlace, *, location_id: str, fetched_at: datetime
) -> dict[str, Any]:
    """One lineage record per station.

    The upstream call is nationwide, so the tempting shape is one record per
    response.  The repository refuses it: a ``weather_response`` entity must
    name the location its facts belong to, or a fact could claim provenance it
    does not have.  Per-station is also the more useful grain -- it says which
    reading a value came from, not merely which fetch.
    """
    return make_source_record(
        provider=KHOA_PROVIDER,
        dataset_key=KHOA_BEACH_INDEX_DATASET,
        location_id=location_id,
        payload=jsonable(place.raw),
        endpoint="/beachIndex",
        fetched_at=fetched_at,
    )


def beach_index_to_weather_values(
    place: BeachIndexPlace,
    *,
    location_id: str,
    source_record_key: str,
    known_at: datetime,
) -> list[WeatherValue]:
    values: list[WeatherValue] = []
    for forecast in place.forecasts:
        if forecast.predicted_on is None:
            # Without a date the row cannot be placed on a timeline, and
            # defaulting it to "now" would file a forecast as an observation.
            continue
        hour = _PERIOD_HOURS.get(
            (forecast.forecast_period or "").strip(), _UNKNOWN_PERIOD_HOUR
        )
        target_at = datetime.combine(
            forecast.predicted_on, time(hour=hour), tzinfo=KST
        )
        for metric, attribute, unit, metric_name in _METRIC_FIELDS:
            raw_value = getattr(forecast, attribute)
            if raw_value is None:
                continue
            values.append(
                WeatherValue(
                    location_id=location_id,
                    provider=KHOA_PROVIDER,
                    dataset_key=KHOA_BEACH_INDEX_DATASET,
                    weather_domain="marine_weather",
                    forecast_style=ForecastStyle.SHORT,
                    metric_key=metric,
                    metric_name=metric_name,
                    source_metric_key=attribute,
                    value_number=Decimal(str(raw_value)),
                    unit=unit,
                    target_at=target_at,
                    known_at=known_at,
                    collected_at=known_at,
                    payload={
                        "beach_code": place.id,
                        "beach_name": place.name,
                        "forecast_period": forecast.forecast_period,
                        "predicted_on": forecast.predicted_on.isoformat(),
                        "raw_metric": attribute,
                    },
                    source_record_key=source_record_key,
                )
            )
        if (forecast.open_status or "").strip():
            values.append(
                WeatherValue(
                    location_id=location_id,
                    provider=KHOA_PROVIDER,
                    dataset_key=KHOA_BEACH_INDEX_DATASET,
                    weather_domain="marine_weather",
                    forecast_style=ForecastStyle.SHORT,
                    metric_key="BEACH_OPEN_STATUS",
                    metric_name="개장 상태",
                    source_metric_key="open_status",
                    value_text=forecast.open_status.strip(),
                    target_at=target_at,
                    known_at=known_at,
                    collected_at=known_at,
                    payload={"beach_code": place.id, "raw_metric": "open_status"},
                    source_record_key=source_record_key,
                )
            )
    return values


def fetch_beach_index(
    *,
    api_key: str,
    max_places: int = 400,
    page_size: int = 100,
    retries: int = 3,
    timeout: float | None = None,
) -> list[BeachIndexPlace]:
    """Fetch the nationwide beach index, bounded, from a synchronous caller.

    KhoaClient became async-only (every ``beach_index``-style sync method was
    removed; only the ``a``-prefixed coroutines remain).  The event loop is
    owned here for the duration of one call, the same boundary
    ``providers.krforest.fetch_mountain_weather`` crosses for ForestClient, so
    Dagster and the repository stay synchronous.

    ``max_places`` is a budget rather than a filter: the builtin catalog lists
    356 beaches today, and a catalog that grows upstream must not silently turn
    one run into an unbounded walk.
    """
    return asyncio.run(
        _fetch_beach_index(
            api_key=api_key,
            max_places=max_places,
            page_size=page_size,
            retries=retries,
            client_timeout=timeout,
        )
    )


async def _fetch_beach_index(
    *,
    api_key: str,
    max_places: int,
    page_size: int,
    retries: int,
    client_timeout: float | None,
) -> list[BeachIndexPlace]:
    # Named ``client_timeout`` rather than ``timeout``: it configures the HTTP
    # client, it is not a deadline on this coroutine (see krforest.py for the
    # same rationale, where ruff's ASYNC109 first flagged it).
    collected: list[BeachIndexPlace] = []
    async with KhoaClient(
        service_key=api_key,
        timeout=client_timeout or 10.0,
        retries=retries,
        # The client reads a .env file by default; deployments inject the key
        # through settings, and reading a stray file would make which key is
        # in use depend on the working directory.
        env_file=None,
    ) as client:
        page_no = 1
        while len(collected) < max_places:
            page = await client.abeach_index(
                page_no=page_no, num_of_rows=min(page_size, max_places - len(collected))
            )
            items = list(page.items)
            if not items:
                break
            collected.extend(items)
            if not getattr(page, "has_next_page", False):
                break
            page_no += 1
    return collected[:max_places]


def measurement_point_metadata(location: WeatherLocation) -> Mapping[str, Any] | None:
    point = location.metadata.get("measurement_point")
    return point if isinstance(point, Mapping) else None
