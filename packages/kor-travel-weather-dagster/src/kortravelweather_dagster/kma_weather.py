"""Atomic KMA weather ingestion helpers and Dagster-independent test seams."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, unquote

from sqlalchemy.exc import OperationalError

from kortravelweather.metrics import (
    observe_sync_locations_skipped,
    observe_sync_values_skipped,
    provider_request,
)
from kortravelweather.models import WeatherLocation, WeatherValue, kst_now
from kortravelweather.partitions import is_lock_conflict
from kortravelweather.providers.kma import (
    KMA_PROVIDER_NAME,
    KmaForecastRow,
    KmaNowcastRow,
    mid_land_forecast_to_weather_values,
    mid_temperature_to_weather_values,
    parse_weather_extra_points,
    short_forecast_to_weather_values,
    ultra_short_forecast_to_weather_values,
    ultra_short_nowcast_to_weather_values,
    weather_warning_to_weather_values,
)
from kortravelweather.repository import WeatherRepository

from .chunked_publish import (
    PUBLISH_CHUNK_LOCATIONS,
    PUBLISH_CHUNK_VALUES,
    chunk_publications,
    uncited_sources,
)

KMA_ULTRA_SHORT_NOWCAST = "kma_ultra_short_nowcast"
KMA_ULTRA_SHORT_FORECAST = "kma_ultra_short_forecast"
KMA_SHORT_FORECAST = "kma_short_forecast"

# 누적 수집량 대신 batch + 한 location의 응답만 유지한다.
KMA_STAGE_VALUES = 5_000
KMA_STAGE_SOURCES = 25

#: The three grid-based datasets ``stage_grid`` can fetch per call. Every
#: caller that does not name a subset gets all three, matching the original
#: bundled behavior; a caller building one job per dataset (see
#: ``definitions.py``) narrows this to a single entry so that job's requests
#: and ``weather_sync_runs`` tracking reflect only what it actually fetched.
BASE_GRID_DATASETS = frozenset(
    {KMA_ULTRA_SHORT_NOWCAST, KMA_ULTRA_SHORT_FORECAST, KMA_SHORT_FORECAST}
)

#: Locations per alert publish transaction.  Each transaction holds the
#: advisory locks of only these locations, so an ingest for any other location
#: never waits on an alerts publish; 50 of production's 1,450 is 29 short
#: transactions instead of one that held every location.
ALERT_PUBLISH_LOCATIONS = 50

#: An alert location whose lock another writer holds is skipped for the tick
#: (``WeatherRepository.ingest_skip_locked``) instead of waited on: on
#: 2026-10-03 other jobs' publish chunks held one for longer than the whole
#: ingest retry budget (~2 minutes), and that failed most hourly alerts runs.
#: The skipped locations get this many more rounds, this far apart, before the
#: run leaves them to the next hourly tick.
ALERT_SKIP_RETRY_ROUNDS = 2
ALERT_SKIP_RETRY_SECONDS = 15.0

#: A location skipped this many alerts runs in a row is reported as starved.
ALERT_STARVED_RUNS = 3

#: A run that skipped at least ``ALERT_PARTIAL_MIN_SKIPPED`` locations and
#: more than ``ALERT_PARTIAL_SKIP_SHARE`` of its alert locations, or any
#: starved one, finishes ``partial`` rather than ``success``:
#: KorTravelWeatherSyncFailed pages on failed|partial, so chronic skipping
#: is not hidden behind green runs.  The minimum keeps a quiet hour -- one
#: regional notice for a handful of locations -- from paging on one routine
#: skip; a location held tick after tick is still caught as starved.  The
#: same minimum decides whether skipping *every* location fails the run.
ALERT_PARTIAL_SKIP_SHARE = 0.10
ALERT_PARTIAL_MIN_SKIPPED = 5

#: Grid values dropped as KMA Missing sentinels finish a run ``partial`` once
#: there are at least ``VALUE_PARTIAL_MIN_SKIPPED`` of them *and* they exceed
#: ``VALUE_PARTIAL_SKIP_SHARE`` of the values attempted.  At prod size the
#: share is what binds: a nowcast run attempts ~1,368 values (171 grids x 8
#: categories), so it pages past ~137 skips -- roughly 17 to 34 offline
#: stations, depending on how many categories each loses (7 of 8 on
#: 2026-10-05).  The minimum (two stations' worth) only matters for small
#: runs, where it keeps one routine outage from paging.  Any out-of-range
#: value (``invalid``) is a contract surprise and always finishes ``partial``;
#: a run whose every value was skipped fails.
VALUE_PARTIAL_SKIP_SHARE = 0.10
VALUE_PARTIAL_MIN_SKIPPED = 16

#: Skipped location IDs listed in the run's note, sorted and percent-escaped,
#: up to this many characters (the rest is counted, not listed).  A failed
#: run keeps 2,000 characters of its error, and a run that skipped every
#: location carries the note there, so the list must fit in that.  The note
#: is what the starvation check reads back: an ID past the limit cannot be
#: reported as starved.
ALERT_SKIP_NOTE_CHARS = 1_500
ALERT_SKIP_LOG_IDS = 5

_ALERT_SKIP_NOTE = re.compile(r"^lock 경합으로 특보 location (\d+)곳 건너뜀: \[([^\]]*)\]")

logger = logging.getLogger(__name__)

#: Grid and mid facts publish in location chunks too (``chunked_publish``):
#: at most this many locations, and at most ``GRID_PUBLISH_VALUES`` facts, per
#: transaction.  On 2026-10-02 the whole run went out in one transaction
#: instead: 4h03m holding 1,103 location locks, with three other KMA jobs
#: queued 2h+ behind it.
GRID_PUBLISH_LOCATIONS = PUBLISH_CHUNK_LOCATIONS
GRID_PUBLISH_VALUES = PUBLISH_CHUNK_VALUES


@dataclass(frozen=True, slots=True)
class WeatherTarget:
    location: WeatherLocation
    mid_region_code: str | None = None
    mid_land_region_code: str | None = None
    mid_temperature_region_code: str | None = None

    @property
    def land_region_code(self) -> str | None:
        return self.mid_land_region_code or self.mid_region_code

    @property
    def temperature_region_code(self) -> str | None:
        return self.mid_temperature_region_code or self.mid_region_code

    @property
    def has_mid(self) -> bool:
        return self.land_region_code is not None or self.temperature_region_code is not None


@dataclass(frozen=True, slots=True)
class StagedResponse:
    source_record: dict[str, Any]
    values: list[WeatherValue]


async def _kma_provider_call(call: Any, *, dataset: str, retries: int) -> Any:
    """Observe one logical KMA/DataGo.kr call across the retry boundary."""
    with provider_request(KMA_PROVIDER_NAME, dataset):
        return await _retry_call(call, retries=retries)


def targets_from_settings(
    raw_targets: Iterable[Mapping[str, Any]],
    *,
    extra_points: str | None = None,
    disabled_location_ids: set[str] | frozenset[str] = frozenset(),
) -> list[WeatherTarget]:
    """Validate targets while preserving every location sharing a grid.

    Grid request deduplication happens in :func:`run_weather_sync`; the target
    catalog itself must retain all location ids so one KMA response can fan out
    to every consumer anchor on that grid.
    """
    result: list[WeatherTarget] = []
    seen_location_ids: set[str] = set()
    for raw in raw_targets:
        # Mid region codes belong to the provider target, not the generic
        # location DTO (which intentionally uses extra='forbid').
        provider_fields = {
            "mid_region_code",
            "mid_land_region_code",
            "mid_temperature_region_code",
            "mid_land_reg_id",
            "mid_ta_reg_id",
        }
        location_payload = {key: value for key, value in raw.items() if key not in provider_fields}
        location = WeatherLocation.model_validate(location_payload)
        legacy_mid = raw.get("mid_region_code")
        land_mid = raw.get("mid_land_region_code") or raw.get("mid_land_reg_id") or legacy_mid
        temperature_mid = (
            raw.get("mid_temperature_region_code") or raw.get("mid_ta_reg_id") or legacy_mid
        )

        def normalize_region(value: Any, field_name: str) -> str | None:
            if value is None:
                return None
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name}가 올바르지 않습니다: {value!r}")
            return value.strip()

        mid_region_code = normalize_region(legacy_mid, "mid_region_code")
        land_mid = normalize_region(land_mid, "mid_land_region_code")
        temperature_mid = normalize_region(temperature_mid, "mid_temperature_region_code")
        if (land_mid is None) != (temperature_mid is None):
            raise ValueError(
                "mid_land_region_code와 mid_temperature_region_code를 함께 설정해야 합니다."
            )
        if location.location_id in seen_location_ids:
            raise ValueError(f"target location_id가 중복됩니다: {location.location_id}")
        seen_location_ids.add(location.location_id)
        if not location.enabled:
            continue
        if location.nx is None or location.ny is None:
            # Match kor-travel-map's KMA grid conversion so an admin-created
            # lat/lon anchor is ingestible without manually entering nx/ny.
            from kma import to_grid

            nx, ny = to_grid(location.latitude, location.longitude)
            location = WeatherLocation.model_validate({**location.model_dump(), "nx": nx, "ny": ny})
        result.append(
            WeatherTarget(
                location,
                mid_region_code=mid_region_code,
                mid_land_region_code=land_mid,
                mid_temperature_region_code=temperature_mid,
            )
        )
    for longitude, latitude in parse_weather_extra_points(extra_points):
        from kma import to_grid

        nx, ny = to_grid(latitude, longitude)
        location_id = f"extra-grid-{nx}-{ny}"
        if location_id in disabled_location_ids:
            continue
        if any(target.location.location_id == location_id for target in result):
            continue
        result.append(
            WeatherTarget(
                WeatherLocation(
                    location_id=location_id,
                    name=f"KMA extra grid ({nx},{ny})",
                    latitude=latitude,
                    longitude=longitude,
                    nx=nx,
                    ny=ny,
                    metadata={"extra_point": True},
                )
            )
        )
    return result


def _json_row(row: Any) -> dict[str, Any]:
    raw = getattr(row, "raw", None)
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(row, Mapping):
        return dict(row)
    model_dump = getattr(row, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json")
        if isinstance(value, dict):
            return value
    if hasattr(row, "__dict__"):
        return dict(row.__dict__)
    raise TypeError(f"KMA row을 JSON으로 변환할 수 없습니다: {type(row).__name__}")


def response_source_key(
    dataset_key: str,
    location_id: str,
    rows: Sequence[Any],
    response_metadata: Mapping[str, Any] | None = None,
) -> str:
    canonical_metadata = (
        _metadata_dict(response_metadata)
        if response_metadata is not None
        else _response_metadata(rows)
    )
    payload = {
        "dataset_key": dataset_key,
        "location_id": location_id,
        "rows": [_json_row(row) for row in rows],
        "response_metadata": canonical_metadata,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    return "sr_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:48]


def _source_spec(
    dataset_key: str,
    location_id: str,
    rows: Sequence[Any],
    fetched_at: datetime,
    response_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    canonical_metadata = (
        _metadata_dict(response_metadata)
        if response_metadata is not None
        else _response_metadata(rows)
    )
    payload = {
        "dataset_key": dataset_key,
        "location_id": location_id,
        "rows": [_json_row(row) for row in rows],
        "response_metadata": canonical_metadata,
    }
    return {
        "source_record_key": response_source_key(
            dataset_key, location_id, rows, response_metadata=response_metadata
        ),
        "provider": KMA_PROVIDER_NAME,
        "dataset_key": dataset_key,
        "source_entity_type": "weather_response",
        "source_entity_id": location_id,
        "payload": payload,
        "fetched_at": fetched_at,
    }


def _response_metadata(rows: Sequence[Any]) -> dict[str, Any]:
    """Preserve python-kma-api endpoint/request metadata when available."""
    if not rows:
        return {}
    metadata = getattr(rows[0], "metadata", None)
    return _metadata_dict(metadata)


def _metadata_dict(metadata: Any) -> dict[str, Any]:
    if metadata is None:
        return {}
    dumped = getattr(metadata, "model_dump", None)
    if callable(dumped):
        value = dumped(mode="json")
        if isinstance(value, dict):
            # collected/imported/fetched timestamps are observability fields,
            # not response identity. Hash only endpoint/request/base metadata.
            cleaned = {
                key: item
                for key, item in value.items()
                if key
                in {
                    "provider",
                    "service_name",
                    "endpoint",
                    "request_params",
                    "base_date",
                    "base_time",
                    "status",
                }
            }
            return _redact_metadata(cleaned)
        return {}
    if isinstance(metadata, Mapping):
        return _redact_metadata(
            {
                key: value
                for key, value in metadata.items()
                if key
                in {
                    "provider",
                    "service_name",
                    "endpoint",
                    "request_params",
                    "base_date",
                    "base_time",
                    "status",
                }
            }
        )
    return _redact_metadata(
        {
            key: getattr(metadata, key)
            for key in (
                "provider",
                "service_name",
                "endpoint",
                "request_params",
                "base_date",
                "base_time",
            )
            if hasattr(metadata, key)
        }
    )


def _redact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Remove credentials from request metadata before hashing/persistence."""
    # Provider metadata is allowed to contain arbitrary nested request
    # structures.  Redact recursively so aliases such as ``authKey`` and
    # nested ``token`` values can never leak through the admin source view.
    secret_names = {
        "servicekey",
        "service_key",
        "apikey",
        "api_key",
        "x_api_key",
        "token",
        "access_token",
        "authkey",
        "auth_key",
        "authorization",
        "password",
        "secret",
        "client_secret",
        "appkey",
        "app_key",
        "key",
    }

    def normalized_key(key: Any) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).lower().replace("-", "_")

    def redact(value: Any) -> Any:
        if isinstance(value, Mapping):
            redacted: dict[Any, Any] = {}
            for key, item in value.items():
                key_name = normalized_key(key)
                redacted[key] = "[REDACTED]" if key_name in secret_names else redact(item)
            return redacted
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(redact(item) for item in value)
        if isinstance(value, str):
            return re.sub(
                r"(?i)(service[_-]?key|api[_-]?key|auth[_-]?key|access[_-]?token|token|password|secret|key)(=|:)([^&\s,;]+)",
                r"\1\2[REDACTED]",
                value,
            )
        return value

    return redact(metadata)


def _forecast_grid(rows: Sequence[Any], nx: int, ny: int) -> None:
    if not rows:
        raise ValueError("KMA 응답이 비어 있습니다.")
    for row in rows:
        parsed = KmaForecastRow.from_raw(row)
        if parsed.nx != nx or parsed.ny != ny:
            raise ValueError(
                f"KMA 응답 격자 불일치: expected=({nx},{ny}) got=({parsed.nx},{parsed.ny})"
            )


def _nowcast_grid(rows: Sequence[Any], nx: int, ny: int) -> None:
    if not rows:
        raise ValueError("KMA 초단기실황 응답이 비어 있습니다.")
    for row in rows:
        parsed = KmaNowcastRow.from_raw(row)
        if parsed.nx != nx or parsed.ny != ny:
            raise ValueError(
                f"KMA 응답 격자 불일치: expected=({nx},{ny}) got=({parsed.nx},{parsed.ny})"
            )


def _mid_region(rows: Sequence[Any], region_code: str) -> None:
    for row in rows:
        raw = getattr(row, "raw", None)
        source = raw if isinstance(raw, Mapping) else row
        returned = None
        for name in ("reg_id", "regId"):
            if isinstance(source, Mapping) and name in source:
                returned = source[name]
                break
            if hasattr(row, name):
                returned = getattr(row, name)
                break
        if returned is None or str(returned).strip() != region_code:
            raise ValueError(
                f"중기예보 지역 불일치: expected={region_code} got={returned!r}"
            )


def _bounded_rows(items: Iterable[Any], *, limit: int | None, label: str) -> list[Any]:
    """Materialize at most ``limit`` provider rows, failing before overflow."""
    if limit is not None and limit <= 0:
        raise ValueError(f"{label} 응답 row 예산이 소진되었습니다.")
    rows: list[Any] = []
    for item in items:
        if limit is not None and len(rows) >= limit:
            raise ValueError(f"{label} 응답 row 수가 상한을 초과했습니다: {limit}")
        rows.append(item)
    return rows


def _bounded_conversion(
    rows: Sequence[Any],
    converter: Any,
    *,
    max_values: int | None,
    **kwargs: Any,
) -> list[WeatherValue]:
    """Convert one row at a time so fan-out cannot allocate an unbounded list."""
    values: list[WeatherValue] = []
    for row in rows:
        converted = converter([row], **kwargs)
        if max_values is not None and len(values) + len(converted) > max_values:
            raise ValueError(
                f"normalized fact 수가 상한을 초과했습니다: "
                f"{len(values) + len(converted)} > {max_values}"
            )
        values.extend(converted)
    return values


def _warning_matches_target(item: Any, target: WeatherTarget) -> bool:
    """Keep a regional warning from being fanned out to another region.

    KMA warning rows are not completely uniform across service revisions.  If
    a row carries an area/region identifier, compare it with the target's
    configured ``region_code``.  Rows without that optional field are treated
    as national-scope notices and remain eligible for every target in the
    requested issuing-office group.
    """
    expected = target.location.region_code
    if not expected:
        return True
    raw = _json_row(item)
    aliases = (
        "reg_id",
        "regId",
        "area_code",
        "areaCode",
        "zone_code",
        "zoneCode",
        "area_name",
        "areaName",
        "zone",
        "area",
    )
    returned = next((raw.get(key) for key in aliases if raw.get(key) not in (None, "")), None)
    if returned is None:
        return True
    expected_text = str(expected).strip().lower()
    returned_text = str(returned).strip().lower()
    return expected_text == returned_text or expected_text in returned_text


async def stage_grid(
    *,
    client: Any,
    target: WeatherTarget,
    fetched_at: datetime | None = None,
    include_mid: bool = False,
    include_base: bool = True,
    base_datasets: frozenset[str] | None = None,
    data_client: Any | None = None,
    retries: int = 0,
    source_entity_id: str | None = None,
    max_response_rows: int | None = None,
    max_normalized_values: int | None = None,
    skipped: Counter[str] | None = None,
) -> list[StagedResponse]:
    """Fetch one grid with bounded row/value materialization.

    Provider iterables are consumed only up to the remaining run budget.  Each
    row is normalized independently so a single malformed or fan-out-heavy
    response cannot allocate an unbounded list before the cap is enforced.
    A KMA Missing sentinel or out-of-range value drops only that metric and is
    counted in ``skipped``; it no longer aborts the whole run.
    """
    location = target.location
    assert location.nx is not None and location.ny is not None
    fetched = fetched_at or kst_now()
    entity_id = source_entity_id or location.location_id
    staged: list[StagedResponse] = []
    skipped_before = skipped.total() if skipped is not None else 0
    rows_budget = max_response_rows
    values_budget = max_normalized_values
    if rows_budget is not None and rows_budget <= 0:
        raise ValueError("provider response row 예산이 소진되었습니다.")
    if values_budget is not None and values_budget <= 0:
        raise ValueError("normalized fact 예산이 소진되었습니다.")

    def row_limit(multiplier: int = 1) -> int | None:
        if rows_budget is None:
            return None
        limit = rows_budget
        if values_budget is not None:
            limit = min(limit, max(1, values_budget // multiplier))
        return limit

    active_base_datasets = base_datasets if base_datasets is not None else BASE_GRID_DATASETS
    if include_base and KMA_ULTRA_SHORT_NOWCAST in active_base_datasets:
        snapshot = await _kma_provider_call(
            lambda: client.now(nx=location.nx, ny=location.ny),
            dataset="kma_ultra_short_nowcast",
            retries=retries,
        )
        raw_snapshot = getattr(snapshot, "raw", None)
        raw_items = raw_snapshot.get("items", []) if isinstance(raw_snapshot, Mapping) else []
        now_items = _bounded_rows(raw_items, limit=row_limit(), label="초단기실황")
        _nowcast_grid(now_items, location.nx, location.ny)
        if rows_budget is not None:
            rows_budget -= len(now_items)
        now_metadata = _metadata_dict(getattr(snapshot, "metadata", None))
        now_key = response_source_key("kma_ultra_short_nowcast", entity_id, now_items, now_metadata)
        now_values = _bounded_conversion(
            now_items,
            ultra_short_nowcast_to_weather_values,
            max_values=values_budget,
            location_id=location.location_id,
            source_record_key=now_key,
            known_at=fetched,
            skipped=skipped,
        )
        if values_budget is not None:
            values_budget -= len(now_values)
        staged.append(
            StagedResponse(
                _source_spec(
                    "kma_ultra_short_nowcast", entity_id, now_items, fetched, now_metadata
                ),
                now_values,
            )
        )
        if rows_budget is not None and rows_budget <= 0:
            raise ValueError("provider response row 수가 상한을 초과했습니다.")
        if values_budget is not None and values_budget <= 0:
            raise ValueError("normalized fact 수가 상한을 초과했습니다.")
    if include_base and KMA_ULTRA_SHORT_FORECAST in active_base_datasets:
        ultra_rows = _bounded_rows(
            await _kma_provider_call(
                lambda: client.forecast.short(nx=location.nx, ny=location.ny),
                dataset="kma_ultra_short_forecast",
                retries=retries,
            ),
            limit=row_limit(),
            label="초단기예보",
        )
        _forecast_grid(ultra_rows, location.nx, location.ny)
        if rows_budget is not None:
            rows_budget -= len(ultra_rows)
        ultra_key = response_source_key("kma_ultra_short_forecast", entity_id, ultra_rows)
        ultra_values = _bounded_conversion(
            ultra_rows,
            ultra_short_forecast_to_weather_values,
            max_values=values_budget,
            location_id=location.location_id,
            source_record_key=ultra_key,
            known_at=fetched,
            skipped=skipped,
        )
        if values_budget is not None:
            values_budget -= len(ultra_values)
        staged.append(
            StagedResponse(
                _source_spec("kma_ultra_short_forecast", entity_id, ultra_rows, fetched),
                ultra_values,
            )
        )
        if rows_budget is not None and rows_budget <= 0:
            raise ValueError("provider response row 수가 상한을 초과했습니다.")
        if values_budget is not None and values_budget <= 0:
            raise ValueError("normalized fact 수가 상한을 초과했습니다.")
    if include_base and KMA_SHORT_FORECAST in active_base_datasets:
        short_rows = _bounded_rows(
            await _kma_provider_call(
                lambda: client.forecast.vilage(nx=location.nx, ny=location.ny),
                dataset="kma_short_forecast",
                retries=retries,
            ),
            limit=row_limit(),
            label="단기예보",
        )
        _forecast_grid(short_rows, location.nx, location.ny)
        if rows_budget is not None:
            rows_budget -= len(short_rows)
        short_key = response_source_key("kma_short_forecast", entity_id, short_rows)
        short_values = _bounded_conversion(
            short_rows,
            short_forecast_to_weather_values,
            max_values=values_budget,
            location_id=location.location_id,
            source_record_key=short_key,
            known_at=fetched,
            skipped=skipped,
        )
        staged.append(
            StagedResponse(
                _source_spec("kma_short_forecast", entity_id, short_rows, fetched),
                short_values,
            )
        )
        if values_budget is not None:
            values_budget -= len(short_values)
    if include_mid and target.has_mid:
        if data_client is None:
            raise ValueError("중기예보에는 DataGoKrClient가 필요합니다.")
        land_region_code = target.land_region_code
        temperature_region_code = target.temperature_region_code
        if land_region_code is None or temperature_region_code is None:
            raise ValueError(
                "중기예보에는 mid_land_region_code와 mid_temperature_region_code가 모두 필요합니다."
            )
        land_rows = _bounded_rows(
            await _kma_provider_call(
                lambda: data_client.mid_land_forecast(reg_id=land_region_code),
                dataset="kma_mid_forecast",
                retries=retries,
            ),
            limit=row_limit(26),
            label="중기육상예보",
        )
        if rows_budget is not None:
            rows_budget -= len(land_rows)
        if not land_rows:
            raise ValueError("중기예보 응답이 비어 있습니다.")
        _mid_region(land_rows, land_region_code)
        land_key = response_source_key(
            "kma_mid_forecast", f"mid-land:{land_region_code}", land_rows
        )
        land_values = _bounded_conversion(
            land_rows,
            mid_land_forecast_to_weather_values,
            max_values=values_budget,
            location_id=location.location_id,
            source_record_key=land_key,
            known_at=fetched,
        )
        if values_budget is not None:
            values_budget -= len(land_values)
        staged.append(
            StagedResponse(
                _source_spec(
                    "kma_mid_forecast", f"mid-land:{land_region_code}", land_rows, fetched
                ),
                land_values,
            )
        )
        if rows_budget is not None and rows_budget <= 0:
            raise ValueError("provider response row 수가 상한을 초과했습니다.")
        if values_budget is not None and values_budget <= 0:
            raise ValueError("normalized fact 수가 상한을 초과했습니다.")
        temp_rows = _bounded_rows(
            await _kma_provider_call(
                lambda: data_client.mid_temperature_forecast(reg_id=temperature_region_code),
                dataset="kma_mid_forecast",
                retries=retries,
            ),
            limit=row_limit(16),
            label="중기기온예보",
        )
        if rows_budget is not None:
            rows_budget -= len(temp_rows)
        if not temp_rows:
            raise ValueError("중기예보 응답이 비어 있습니다.")
        _mid_region(temp_rows, temperature_region_code)
        temp_key = response_source_key(
            "kma_mid_forecast", f"mid-temperature:{temperature_region_code}", temp_rows
        )
        temp_values = _bounded_conversion(
            temp_rows,
            mid_temperature_to_weather_values,
            max_values=values_budget,
            location_id=location.location_id,
            source_record_key=temp_key,
            known_at=fetched,
        )
        staged.append(
            StagedResponse(
                _source_spec(
                    "kma_mid_forecast",
                    f"mid-temperature:{temperature_region_code}",
                    temp_rows,
                    fetched,
                ),
                temp_values,
            )
        )
    skipped_here = (skipped.total() if skipped is not None else 0) - skipped_before
    if not any(response.values for response in staged) and not skipped_here:
        # A grid whose every value was a skipped sentinel is an outage at that
        # station, counted by the run; only a response with nothing in it is
        # a contract failure.
        raise ValueError("KMA 응답에서 normalized weather fact가 생성되지 않았습니다.")
    return staged


def run_weather_sync(
    *,
    repository: WeatherRepository,
    client: Any,
    targets: Sequence[WeatherTarget],
    alert_targets: Sequence[WeatherTarget] | None = None,
    max_grids: int = 300,
    max_mid_groups: int | None = None,
    max_targets: int = 10_000,
    max_response_rows: int = 1_000_000,
    max_values: int = 500_000,
    include_base: bool = True,
    base_datasets: frozenset[str] | None = None,
    include_mid: bool = False,
    include_alerts: bool = False,
    alert_station_id: str | int | None = None,
    data_client: Any | None = None,
    retries: int = 0,
    sync_run: Any | None = None,
) -> dict[str, Any]:
    """Own the event loop KmaClient/DataGoKrClient now require for the run.

    Both clients are constructed by the caller -- so a disabled provider
    never demands its credential -- but entered and closed here, in the same
    ``asyncio.run()`` that stages every grid.  Each client's shared rate
    limiter binds to whichever event loop first calls ``acquire()`` and
    raises if a later call arrives from a different one, and a run stages up
    to ``max_grids`` grids and ``max_mid_groups`` region pairs against the
    same client -- a separate ``asyncio.run()`` per grid, the obvious
    alternative, would trip that on the second one.

    ``include_base``/``base_datasets`` let a caller build one job per KMA
    dataset (nowcast, ultra-short forecast, short forecast, mid forecast,
    alerts) instead of fetching the whole bundle every run -- each dataset
    has its own real publish cadence, and a run that only wants one no
    longer has to pay for (or track sync-run status against) the others.
    """
    return asyncio.run(
        _run_weather_sync(
            repository=repository,
            client=client,
            targets=targets,
            alert_targets=alert_targets,
            max_grids=max_grids,
            max_mid_groups=max_mid_groups,
            max_targets=max_targets,
            max_response_rows=max_response_rows,
            max_values=max_values,
            include_base=include_base,
            base_datasets=base_datasets,
            include_mid=include_mid,
            include_alerts=include_alerts,
            alert_station_id=alert_station_id,
            data_client=data_client,
            retries=retries,
            sync_run=sync_run,
        )
    )


async def _run_weather_sync(
    *,
    repository: WeatherRepository,
    client: Any,
    targets: Sequence[WeatherTarget],
    alert_targets: Sequence[WeatherTarget] | None = None,
    max_grids: int = 300,
    max_mid_groups: int | None = None,
    max_targets: int = 10_000,
    max_response_rows: int = 1_000_000,
    max_values: int = 500_000,
    include_base: bool = True,
    base_datasets: frozenset[str] | None = None,
    include_mid: bool = False,
    include_alerts: bool = False,
    alert_station_id: str | int | None = None,
    data_client: Any | None = None,
    retries: int = 0,
    sync_run: Any | None = None,
) -> dict[str, Any]:
    """Fetch and publish every grid with bounded, sequential staging."""
    async with AsyncExitStack() as clients:
        await clients.enter_async_context(client)
        if data_client is not None:
            await clients.enter_async_context(data_client)
        return await _stage_and_publish_weather(
            repository=repository,
            client=client,
            targets=targets,
            alert_targets=alert_targets,
            max_grids=max_grids,
            max_mid_groups=max_mid_groups,
            max_targets=max_targets,
            max_response_rows=max_response_rows,
            max_values=max_values,
            include_base=include_base,
            base_datasets=base_datasets,
            include_mid=include_mid,
            include_alerts=include_alerts,
            alert_station_id=alert_station_id,
            data_client=data_client,
            retries=retries,
            sync_run=sync_run,
        )


async def _stage_and_publish_weather(
    *,
    repository: WeatherRepository,
    client: Any,
    targets: Sequence[WeatherTarget],
    alert_targets: Sequence[WeatherTarget] | None = None,
    max_grids: int = 300,
    max_mid_groups: int | None = None,
    max_targets: int = 10_000,
    max_response_rows: int = 1_000_000,
    max_values: int = 500_000,
    include_base: bool = True,
    base_datasets: frozenset[str] | None = None,
    include_mid: bool = False,
    include_alerts: bool = False,
    alert_station_id: str | int | None = None,
    data_client: Any | None = None,
    retries: int = 0,
    sync_run: Any | None = None,
) -> dict[str, Any]:
    run = sync_run or repository.start_sync_run(
        provider=KMA_PROVIDER_NAME,
        dataset_key="kma_weather_bundle",
        locations_total=len(targets),
    )
    #: Alert chunks already committed; reported even when the run fails.
    published_values = 0
    try:
        alert_target_list = list(alert_targets) if alert_targets is not None else list(targets)
        # Check each dataset against the targets it actually reads.  A single
        # grid-target check failed the alerts job -- which reads only
        # alert_targets -- on every run while the grid set was empty, and let a
        # mid job with no region-coded target succeed having fetched nothing.
        if include_base and not targets:
            raise ValueError("weather target이 비어 있습니다.")
        if include_mid and not any(target.has_mid for target in targets):
            raise ValueError("중기예보 지역 코드가 설정된 weather target이 없습니다.")
        if include_alerts and not alert_target_list:
            raise ValueError("weather alert target이 비어 있습니다.")
        if len(targets) > max_targets:
            raise ValueError(
                f"weather target 수가 상한을 초과했습니다: {len(targets)} > {max_targets}"
            )
        if len(alert_target_list) > max_targets:
            raise ValueError(
                f"weather alert target 수가 상한을 초과했습니다: "
                f"{len(alert_target_list)} > {max_targets}"
            )
        unique_grid_count = len({(target.location.nx, target.location.ny) for target in targets})
        if unique_grid_count > max_grids:
            raise ValueError(
                f"weather grid 수가 상한을 초과했습니다: {unique_grid_count} > {max_grids}"
            )
        get_location = getattr(repository, "get_location", None)
        create_location = getattr(repository, "create_location", None)
        ensure_location_grid = getattr(repository, "ensure_location_grid", None)
        upsert_location = getattr(repository, "upsert_location", None)
        if callable(create_location) or callable(upsert_location):
            for target in targets:
                location = target.location
                if target.has_mid:
                    location = WeatherLocation.model_validate(
                        {
                            **location.model_dump(),
                            "metadata": {
                                **location.metadata,
                                "mid_region_code": target.mid_region_code,
                                "mid_land_region_code": target.land_region_code,
                                "mid_temperature_region_code": target.temperature_region_code,
                            },
                        }
                    )
                # Existing catalog rows are owned by the admin/API.  A sync
                # may bootstrap a missing anchor, but must never write a
                # stale target snapshot back over an intervening edit or
                # disabled row.
                current_location = (
                    get_location(location.location_id) if callable(get_location) else None
                )
                if current_location is not None:
                    if (
                        callable(ensure_location_grid)
                        and (current_location.nx is None or current_location.ny is None)
                        and location.nx is not None
                        and location.ny is not None
                    ):
                        ensure_location_grid(
                            location.location_id,
                            nx=location.nx,
                            ny=location.ny,
                            latitude=location.latitude,
                            longitude=location.longitude,
                        )
                    continue
                if callable(create_location):
                    try:
                        create_location(location)
                    except ValueError:
                        # Another worker/admin created the anchor meanwhile;
                        # retain that canonical row and continue ingesting.
                        continue
                elif callable(upsert_location):
                    upsert_location(location)
        grid_groups: dict[tuple[int, int], list[WeatherTarget]] = {}
        mid_groups: dict[tuple[str, str], list[WeatherTarget]] = {}
        for target in targets:
            assert target.location.nx is not None and target.location.ny is not None
            grid_key = (target.location.nx, target.location.ny)
            grid_groups.setdefault(grid_key, []).append(target)
            if include_mid and target.has_mid:
                land_region_code = target.land_region_code
                temperature_region_code = target.temperature_region_code
                if land_region_code is None or temperature_region_code is None:
                    raise ValueError(
                        "중기예보에는 land/temperature region code가 모두 필요합니다."
                    )
                mid_key = (land_region_code, temperature_region_code)
                mid_groups.setdefault(mid_key, []).append(target)
        mid_limit = max_mid_groups if max_mid_groups is not None else max_grids
        if len(mid_groups) > mid_limit:
            raise ValueError(
                f"중기예보 지역 조합 수가 상한을 초과했습니다: {len(mid_groups)} > {mid_limit}"
            )
        sources: list[dict[str, Any]] = []
        values: list[WeatherValue] = []
        alert_sources: list[dict[str, Any]] = []
        alert_values: list[WeatherValue] = []
        response_rows_total = 0
        normalized_values_total = 0
        #: Metrics dropped as KMA Missing sentinels or out-of-range values,
        #: keyed ``"{dataset}:{missing|invalid}:{category}"`` (per response).
        values_skipped: Counter[str] = Counter()
        #: Values the grid/mid responses yielded, before fan-out to locations.
        values_accepted = 0
        alert_locations_seen: set[str] = set()
        alert_locations_published: set[str] = set()
        alert_skipped_locations: set[str] = set()
        heartbeat = getattr(repository, "heartbeat_sync_run", None)

        def keep_alive() -> None:
            if callable(heartbeat) and heartbeat(run.run_id) is False:
                raise RuntimeError("sync run lease가 만료되어 publish를 중단했습니다.")

        async def flush_alert_batch() -> None:
            nonlocal published_values
            batch_locations = {value.location_id for value in alert_values}
            loaded, skipped = await _publish_alerts(
                repository, alert_sources, alert_values, keep_alive
            )
            published_values += loaded
            alert_skipped_locations.update(skipped)
            alert_locations_published.update(batch_locations - set(skipped))
            empty = uncited_sources(alert_sources, alert_values)
            if empty:
                await asyncio.to_thread(repository.ingest_batch, source_records=empty, values=[])
            alert_sources.clear()
            alert_values.clear()

        async def flush_grid_batch() -> None:
            nonlocal published_values
            for chunk_sources, chunk in chunk_publications(
                sources,
                values,
                max_locations=GRID_PUBLISH_LOCATIONS,
                max_values=GRID_PUBLISH_VALUES,
            ):
                keep_alive()
                published_values += await asyncio.to_thread(
                    repository.ingest_batch, source_records=chunk_sources, values=chunk
                )
            empty = uncited_sources(sources, values)
            if empty:
                await asyncio.to_thread(repository.ingest_batch, source_records=empty, values=[])
            sources.clear()
            values.clear()

        async def stage_and_append(
            target: WeatherTarget,
            group_targets: Sequence[WeatherTarget],
            *,
            include_mid_for_group: bool,
            source_entity_id: str,
        ) -> None:
            nonlocal response_rows_total, normalized_values_total, values_accepted
            if len(sources) >= KMA_STAGE_SOURCES:
                await flush_grid_batch()
            keep_alive()
            remaining_rows = max_response_rows - response_rows_total
            remaining_values = max_values - normalized_values_total
            if remaining_rows <= 0:
                raise ValueError("provider response row 예산이 소진되었습니다.")
            if remaining_values < len(group_targets):
                raise ValueError(
                    "normalized fact 예산이 target fan-out을 수용하지 못합니다: "
                    f"{remaining_values} < {len(group_targets)}"
                )
            per_target_values = max(1, remaining_values // len(group_targets))
            responses = await stage_grid(
                client=client,
                target=target,
                include_mid=include_mid_for_group,
                include_base=include_base and not include_mid_for_group,
                base_datasets=base_datasets,
                data_client=data_client,
                retries=retries,
                source_entity_id=source_entity_id,
                max_response_rows=remaining_rows,
                max_normalized_values=per_target_values,
                skipped=values_skipped,
            )
            values_accepted += sum(len(response.values) for response in responses)
            for response in responses:
                payload = response.source_record.get("payload") or {}
                row_count = len(payload.get("rows", [])) if isinstance(payload, Mapping) else 0
                response_rows_total += row_count
                if response_rows_total > max_response_rows:
                    raise ValueError(
                        f"provider response row 수가 상한을 초과했습니다: "
                        f"{response_rows_total} > {max_response_rows}"
                    )
                sources.append({**response.source_record, "run_id": run.run_id})
                # One immutable response is fanned out to every catalog anchor
                # sharing its grid/region. Append one fact at a time so the
                # aggregate cap is enforced before another object is retained.
                for target_row in group_targets:
                    # 이전 target에서 flush한 경우 현재 response의 lineage를 다시 넣는다.
                    if not any(
                        source["source_record_key"] == response.source_record["source_record_key"]
                        for source in sources
                    ):
                        sources.append({**response.source_record, "run_id": run.run_id})
                    for value in response.values:
                        if normalized_values_total >= max_values:
                            raise ValueError(
                                f"normalized fact 수가 상한을 초과했습니다: >= {max_values}"
                            )
                        values.append(
                            value.model_copy(
                                update={"location_id": target_row.location.location_id}
                            )
                        )
                        normalized_values_total += 1
                    if len(values) >= KMA_STAGE_VALUES:
                        await flush_grid_batch()
            keep_alive()

        # Grid requests are deduplicated first; each staged response is
        # immediately bounded, fanned out, and released before the next grid.
        if include_base:
            for grid_key, group_targets in grid_groups.items():
                await stage_and_append(
                    group_targets[0],
                    group_targets,
                    include_mid_for_group=False,
                    source_entity_id=f"grid:{grid_key[0]}:{grid_key[1]}",
                )
        for mid_region_codes, group_targets in mid_groups.items():
            await stage_and_append(
                group_targets[0],
                group_targets,
                include_mid_for_group=True,
                source_entity_id=f"mid-region:{mid_region_codes[0]}:{mid_region_codes[1]}",
            )
        alert_groups = 0
        alert_rows_total = 0
        if include_alerts:
            if data_client is None:
                raise ValueError("특보에는 DataGoKrClient가 필요합니다.")
            alert_targets: dict[str, list[WeatherTarget]] = {}
            for target in alert_target_list:
                configured = target.location.metadata.get("kma_alert_station_id")
                station = str(configured or alert_station_id or "108").strip()
                if not station:
                    raise ValueError("KMA 특보 관측소 ID가 비어 있습니다.")
                alert_targets.setdefault(station, []).append(target)
            # The warning API accepts date/datetime values. Keep a short,
            # deterministic window so hourly runs do not repeatedly fetch an
            # unbounded historical response. Each target group is tied to its
            # configured issuing office; Seoul warnings are never fanned out to
            # a target configured for another office.
            for station, station_targets in alert_targets.items():
                keep_alive()
                alert_to = kst_now()
                alert_from = alert_to - timedelta(days=3)

                def fetch_warnings(
                    station_id: str = station,
                    from_at: datetime = alert_from,
                    to_at: datetime = alert_to,
                ) -> Any:
                    return data_client.weather_warning_list(
                        stn_id=station_id,
                        from_tm_fc=from_at,
                        to_tm_fc=to_at,
                        num_of_rows=min(100, max_response_rows),
                    )

                warning_items = _bounded_rows(
                    await _kma_provider_call(
                        fetch_warnings,
                        dataset="kma_weather_alerts",
                        retries=retries,
                    ),
                    limit=max_response_rows - response_rows_total,
                    label="기상특보",
                )
                alert_groups += 1
                alert_rows_total += len(warning_items)
                response_rows_total += len(warning_items)
                # One source row per notice keeps the immutable fact identity
                # unique even when several notices share the same issue time.
                for item in warning_items:
                    if len(alert_sources) >= KMA_STAGE_SOURCES:
                        await flush_alert_batch()
                    entity_id = f"kma-alert:{station}"
                    template = weather_warning_to_weather_values(
                        [item], location_id=entity_id, known_at=alert_to
                    )
                    if not template:
                        continue
                    source_key = template[0].source_record_key
                    raw = _json_row(item)
                    source = {
                        "source_record_key": source_key,
                        "provider": KMA_PROVIDER_NAME,
                        "dataset_key": "kma_weather_alerts",
                        "source_entity_type": "weather_response",
                        "source_entity_id": entity_id,
                        "payload": {
                            "dataset_key": "kma_weather_alerts",
                            "location_id": entity_id,
                            "rows": [raw],
                            "response_metadata": {},
                        },
                        "fetched_at": alert_to,
                        "run_id": run.run_id,
                    }
                    alert_sources.append(source)
                    for target in station_targets:
                        if not _warning_matches_target(item, target):
                            continue
                        if normalized_values_total >= max_values:
                            raise ValueError("normalized fact 수가 상한을 초과했습니다.")
                        if not alert_sources:
                            alert_sources.append(source)
                        alert_values.append(
                            template[0].model_copy(
                                update={"location_id": target.location.location_id}
                            )
                        )
                        normalized_values_total += 1
                        alert_locations_seen.add(target.location.location_id)
                        if len(alert_values) >= KMA_STAGE_VALUES:
                            await flush_alert_batch()
                keep_alive()
        grids_fetched = len(grid_groups) if include_base else 0
        base_dataset_count = (
            len(base_datasets) if base_datasets is not None else len(BASE_GRID_DATASETS)
        )
        base_requests_fetched = grids_fetched * base_dataset_count
        # Every fact publishes in location chunks, each its own short
        # transaction holding only its own locations' locks.  One transaction
        # for the run held every location it touched for as long as the
        # insert took: alerts (fanned out to all 1,450 locations) for over an
        # hour on 2026-10-01, a short-forecast run for 4h03m on 2026-10-02,
        # with every other writer queued behind them.
        #
        # 메모리 상한에 도달한 grid batch는 수집 도중에 게시한다. 이후 provider/DB
        # 실패 시 이미 commit된 fact는 유지하며 다음 실행이 unique key로 멱등 재수집한다.
        # Each
        # published chunk is every fact of whole responses for its locations,
        # and the next run replays the rest (published facts are an
        # idempotent no-op).  Every chunk carries the source records its facts
        # cite, so each one re-checks that the run still owns its lease.
        # 특보도 제한된 batch 안에서만 skip 재시도한다. source 인용을 먼저 검사하고
        # chunk 목록은 하나씩 생성한다.
        #
        # Alerts skip a location another writer holds (``_publish_alerts``):
        # the next hourly run fetches the same notices, so a held location
        # costs one tick of freshness instead of the whole run.  The run fails
        # only when no alert location could be published.
        grid_publications = chunk_publications(
            sources,
            values,
            max_locations=GRID_PUBLISH_LOCATIONS,
            max_values=GRID_PUBLISH_VALUES,
        )
        await flush_alert_batch()
        # 하나라도 notice 게시가 누락된 location은 이후 batch 성공에도 incomplete다.
        alerts_skipped = sorted(alert_skipped_locations)
        alerts_starved: list[str] = []
        skip_note: str | None = None
        status = "success"
        if alerts_skipped:
            alert_locations = len(alert_locations_seen)
            observe_sync_locations_skipped(
                KMA_PROVIDER_NAME, "kma_weather_alerts", len(alerts_skipped)
            )
            skip_note = _alert_skip_note(alerts_skipped)
            if not alert_locations_published and alert_locations >= ALERT_PARTIAL_MIN_SKIPPED:
                # The note leads the error so the starvation check reads this
                # run's skips back like any other's.
                raise RuntimeError(
                    f"{skip_note}; 특보 location {alert_locations}곳 모두 publish하지 못했습니다."
                )
            logger.warning(
                "kma_weather_alerts: %d/%d locations skipped on lock contention, "
                "left to the next tick: %s",
                len(alerts_skipped),
                alert_locations,
                ", ".join(alerts_skipped[:ALERT_SKIP_LOG_IDS]),
            )
            alerts_starved = _starved_alert_locations(repository, run, alerts_skipped)
            if alerts_starved:
                logger.warning(
                    "kma_weather_alerts: %d locations skipped %d runs in a row "
                    "(lock starvation): %s",
                    len(alerts_starved),
                    ALERT_STARVED_RUNS,
                    ", ".join(alerts_starved[:ALERT_SKIP_LOG_IDS]),
                )
            if alerts_starved or (
                len(alerts_skipped) >= ALERT_PARTIAL_MIN_SKIPPED
                and len(alerts_skipped) > ALERT_PARTIAL_SKIP_SHARE * alert_locations
            ):
                status = "partial"
        values_missing = sum(n for key, n in values_skipped.items() if ":missing:" in key)
        values_invalid = values_skipped.total() - values_missing
        values_attempted = values_accepted + values_skipped.total()
        if values_skipped:
            for key, count in values_skipped.items():
                dataset_key, reason, _category = key.split(":", 2)
                observe_sync_values_skipped(KMA_PROVIDER_NAME, dataset_key, reason, count)
            logger.warning(
                "KMA run %s skipped %d of %d values (missing sentinel / out of range): %s",
                run.run_id,
                values_skipped.total(),
                values_attempted,
                dict(values_skipped.most_common()),
            )
            if values_accepted == 0:
                raise RuntimeError(
                    f"KMA 값 {values_attempted}건을 모두 건너뛰었습니다: "
                    f"{dict(values_skipped.most_common())}"
                )
            if values_invalid or (
                values_missing >= VALUE_PARTIAL_MIN_SKIPPED
                and values_missing > VALUE_PARTIAL_SKIP_SHARE * values_attempted
            ):
                status = "partial"
                value_note = (
                    f"KMA 값 {values_skipped.total()}/{values_attempted}건 건너뜀 "
                    f"(missing {values_missing}, invalid {values_invalid})"
                )
                # The alert note, when present, must lead: the starvation
                # check reads it back from the start of the error.
                skip_note = f"{skip_note}; {value_note}" if skip_note else value_note
        for chunk_sources, chunk in grid_publications:
            keep_alive()
            # Off the event loop: a lock race retries with a blocking pause
            # (``WeatherRepository._retry_lock_race``).
            published_values += await asyncio.to_thread(
                repository.ingest_batch, source_records=chunk_sources, values=chunk
            )
        # Responses no fact cites -- notices that matched no location -- are
        # still fetched responses; they are recorded with the run's finish.
        final_sources = [
            *uncited_sources(sources, values),
            *uncited_sources(alert_sources, alert_values),
        ]
        publish_and_finish = getattr(repository, "publish_and_finish", None)
        if callable(publish_and_finish):
            loaded, finished = await asyncio.to_thread(
                publish_and_finish,
                run_id=run.run_id,
                source_records=final_sources,
                values=[],
                grids_fetched=grids_fetched,
                mid_groups_fetched=len(mid_groups),
                requests_fetched=base_requests_fetched + len(mid_groups) * 2 + alert_groups,
                values_loaded_offset=published_values,
                status=status,
                error=skip_note,
            )
        else:
            loaded = (
                await asyncio.to_thread(
                    repository.ingest_batch, source_records=final_sources, values=[]
                )
                + published_values
            )
            finished = repository.finish_sync_run(
                run.run_id,
                status=status,
                grids_fetched=grids_fetched,
                mid_groups_fetched=len(mid_groups),
                requests_fetched=base_requests_fetched + len(mid_groups) * 2 + alert_groups,
                values_loaded=loaded,
                error=skip_note,
            )
        if finished.status != status:
            raise RuntimeError(
                f"sync run ownership was lost before publish completion: {finished.status}"
            )
        return {
            "values_attempted": values_attempted,
            "values_skipped": values_skipped.total(),
            "values_skipped_by_reason": dict(values_skipped.most_common()),
            "run_id": finished.run_id,
            "status": finished.status,
            "grids_fetched": grids_fetched,
            "mid_groups_fetched": len(mid_groups),
            "requests_fetched": base_requests_fetched + len(mid_groups) * 2 + alert_groups,
            "alerts_fetched": alert_rows_total,
            "values_loaded": loaded,
            "alert_locations_skipped": len(alerts_skipped),
            "alert_locations_skipped_sample": alerts_skipped[:ALERT_SKIP_LOG_IDS],
            "alert_locations_starved": alerts_starved,
        }
    except Exception as exc:
        repository.finish_sync_run(
            run.run_id,
            status="failed",
            grids_fetched=0,
            # None keeps the count each committed chunk recorded on the run
            # row; ``published_values`` misses a chunk whose COMMIT landed
            # but whose reply did not.
            values_loaded=None,
            error=str(exc)[:2000],
        )
        raise


def _alert_skip_note(skipped: Sequence[str]) -> str:
    """The run's note for skipped alert locations; ``_alert_skips_in`` reads it.

    IDs are percent-escaped, so a comma or bracket in one cannot break the
    list, and listed only up to ``ALERT_SKIP_NOTE_CHARS``.
    """
    listed: list[str] = []
    size = 0
    for location in skipped:
        escaped = quote(location, safe="-_.:~")
        if size + len(escaped) + 1 > ALERT_SKIP_NOTE_CHARS:
            break
        listed.append(escaped)
        size += len(escaped) + 1
    rest = len(skipped) - len(listed)
    note = f"lock 경합으로 특보 location {len(skipped)}곳 건너뜀: [{','.join(listed)}]"
    return note + (f" 외 {rest}곳" if rest else "") + " (다음 tick에 재시도)"


def _alert_skips_in(note: str | None) -> set[str]:
    """The location IDs a run's note says it skipped (none for any other note)."""
    match = _ALERT_SKIP_NOTE.match(note or "")
    if match is None:
        return set()
    return {unquote(location) for location in match.group(2).split(",") if location}


async def _publish_alerts(
    repository: Any,
    alert_sources: Sequence[Mapping[str, Any]],
    alert_values: Sequence[WeatherValue],
    keep_alive: Any,
) -> tuple[int, list[str]]:
    """Publish alert facts in location chunks, skipping held locations.

    A skipped location's facts are left to the next hourly run, which fetches
    the same notices again; each fact is that location's whole notice, so the
    rest is never half a notice.  Only those locations go to the retry
    rounds.  Location advisory locks are only tried, in the repository's
    sorted order, so the lock order every ingest relies on is unchanged.

    A chunk whose transaction still spends its lock-race budget waited on
    some other lock -- partition DDL, the nightly drop, the run row -- and
    every later chunk would spend ~2 minutes the same way, so that fails the
    run at once, as before skipping, with an error that says so.  Any other
    error propagates too.  Returns what was published and the locations
    still skipped, sorted.
    """
    published = 0
    pending: Sequence[WeatherValue] = alert_values
    skipped: set[str] = set()
    for round_ in range(ALERT_SKIP_RETRY_ROUNDS + 1):
        if round_:
            if not skipped:
                break
            await asyncio.sleep(ALERT_SKIP_RETRY_SECONDS)
            pending = [value for value in alert_values if value.location_id in skipped]
            skipped = set()
        for chunk_sources, chunk in chunk_publications(
            alert_sources,
            pending,
            max_locations=ALERT_PUBLISH_LOCATIONS,
            max_values=max(1, len(pending)),
        ):
            keep_alive()
            try:
                # Off the event loop: a lock race retries with a blocking
                # pause (``WeatherRepository._retry_lock_race``).
                loaded, held = await asyncio.to_thread(
                    repository.ingest_skip_locked, source_records=chunk_sources, values=chunk
                )
            except OperationalError as exc:
                if not is_lock_conflict(exc):
                    raise
                raise RuntimeError(
                    "특보 publish chunk가 location lock이 아닌 lock(partition DDL·run row 등)을 "
                    "기다리다 재시도 예산을 다 썼습니다."
                ) from exc
            published += loaded
            skipped.update(held)
    return published, sorted(skipped)


def _starved_alert_locations(repository: Any, run: Any, skipped: Sequence[str]) -> list[str]:
    """Locations this run and the ``ALERT_STARVED_RUNS - 1`` runs before it all skipped.

    A failed run counts when its error carries the skip note (it skipped every
    location); one that failed for any other reason published nothing it
    could have skipped, so it neither counts nor breaks the streak.
    """
    provider = getattr(run, "provider", None)
    dataset_key = getattr(run, "dataset_key", None)
    if not skipped or provider is None or dataset_key is None:
        return []
    previous = [
        prior
        for prior in repository.list_sync_runs(
            limit=ALERT_STARVED_RUNS + 10, provider=provider, dataset_key=dataset_key
        )
        if prior.run_id != run.run_id
        and prior.status != "running"
        and (prior.status != "failed" or _ALERT_SKIP_NOTE.match(prior.error or ""))
    ][: ALERT_STARVED_RUNS - 1]
    if len(previous) < ALERT_STARVED_RUNS - 1:
        return []
    starved = set(skipped)
    for prior in previous:
        starved &= _alert_skips_in(prior.error)
    return sorted(starved)


async def _retry_call(call: Any, *, retries: int) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await call()
        except (AssertionError, TypeError, ValueError):
            # Contract/parse failures are deterministic and must not spend
            # additional provider quota. The python-kma-api client already
            # retries its transport boundary; this loop is only a final guard
            # for transient connection-like exceptions from custom clients.
            raise
        except Exception as exc:
            retryable = getattr(exc, "retryable", None)
            if retryable is not None and retryable is not True:
                # python-kma-api marks auth, request and parse failures as
                # deterministic. Retrying them only burns quota and obscures
                # the actionable error.
                raise
            if retryable is None and not isinstance(exc, (ConnectionError, TimeoutError, OSError)):
                # Unknown application exceptions are treated as contract
                # failures. Only explicit provider retryable errors and
                # transport exceptions may consume the retry budget.
                raise
            last_error = exc
            if attempt >= retries:
                break
            await asyncio.sleep(min(0.25 * (2**attempt), 5.0))
    assert last_error is not None
    raise last_error
