"""Atomic KMA weather ingestion helpers and Dagster-independent test seams."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from time import sleep
from typing import Any

from kortravelweather.models import WeatherLocation, WeatherValue, kst_now
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
)
from kortravelweather.repository import WeatherRepository


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


def stage_grid(
    *,
    client: Any,
    target: WeatherTarget,
    fetched_at: datetime | None = None,
    include_mid: bool = False,
    include_base: bool = True,
    data_client: Any | None = None,
    retries: int = 0,
    source_entity_id: str | None = None,
    max_response_rows: int | None = None,
    max_normalized_values: int | None = None,
) -> list[StagedResponse]:
    """Fetch one grid with bounded row/value materialization.

    Provider iterables are consumed only up to the remaining run budget.  Each
    row is normalized independently so a single malformed or fan-out-heavy
    response cannot allocate an unbounded list before the cap is enforced.
    """
    location = target.location
    assert location.nx is not None and location.ny is not None
    fetched = fetched_at or kst_now()
    entity_id = source_entity_id or location.location_id
    staged: list[StagedResponse] = []
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

    if include_base:
        snapshot = _retry_call(lambda: client.now(nx=location.nx, ny=location.ny), retries=retries)
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
        ultra_rows = _bounded_rows(
            _retry_call(
                lambda: client.forecast.short(nx=location.nx, ny=location.ny), retries=retries
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
        short_rows = _bounded_rows(
            _retry_call(
                lambda: client.forecast.vilage(nx=location.nx, ny=location.ny), retries=retries
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
            _retry_call(
                lambda: data_client.mid_land_forecast(reg_id=land_region_code), retries=retries
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
            _retry_call(
                lambda: data_client.mid_temperature_forecast(reg_id=temperature_region_code),
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
    if not any(response.values for response in staged):
        raise ValueError("KMA 응답에서 normalized weather fact가 생성되지 않았습니다.")
    return staged


def run_weather_sync(
    *,
    repository: WeatherRepository,
    client: Any,
    targets: Sequence[WeatherTarget],
    max_grids: int = 300,
    max_mid_groups: int | None = None,
    max_targets: int = 10_000,
    max_response_rows: int = 1_000_000,
    max_values: int = 500_000,
    include_mid: bool = False,
    data_client: Any | None = None,
    retries: int = 0,
    sync_run: Any | None = None,
) -> dict[str, Any]:
    """Fetch and publish every grid with bounded, sequential staging."""
    run = sync_run or repository.start_sync_run(
        provider=KMA_PROVIDER_NAME,
        dataset_key="kma_weather_bundle",
        locations_total=len(targets),
    )
    try:
        if not targets:
            raise ValueError("weather target이 비어 있습니다.")
        if len(targets) > max_targets:
            raise ValueError(
                f"weather target 수가 상한을 초과했습니다: {len(targets)} > {max_targets}"
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
                f"중기예보 지역 조합 수가 상한을 초과했습니다: "
                f"{len(mid_groups)} > {mid_limit}"
            )
        sources: list[dict[str, Any]] = []
        values: list[WeatherValue] = []
        response_rows_total = 0
        heartbeat = getattr(repository, "heartbeat_sync_run", None)

        def keep_alive() -> None:
            if callable(heartbeat) and heartbeat(run.run_id) is False:
                raise RuntimeError("sync run lease가 만료되어 publish를 중단했습니다.")

        def stage_and_append(
            target: WeatherTarget,
            group_targets: Sequence[WeatherTarget],
            *,
            include_mid_for_group: bool,
            source_entity_id: str,
        ) -> None:
            nonlocal response_rows_total
            keep_alive()
            remaining_rows = max_response_rows - response_rows_total
            remaining_values = max_values - len(values)
            if remaining_rows <= 0:
                raise ValueError("provider response row 예산이 소진되었습니다.")
            if remaining_values < len(group_targets):
                raise ValueError(
                    "normalized fact 예산이 target fan-out을 수용하지 못합니다: "
                    f"{remaining_values} < {len(group_targets)}"
                )
            per_target_values = max(1, remaining_values // len(group_targets))
            responses = stage_grid(
                client=client,
                target=target,
                include_mid=include_mid_for_group,
                include_base=not include_mid_for_group,
                data_client=data_client,
                retries=retries,
                source_entity_id=source_entity_id,
                max_response_rows=remaining_rows,
                max_normalized_values=per_target_values,
            )
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
                    for value in response.values:
                        if len(values) >= max_values:
                            raise ValueError(
                                f"normalized fact 수가 상한을 초과했습니다: "
                                f">= {max_values}"
                            )
                        values.append(
                            value.model_copy(
                                update={"location_id": target_row.location.location_id}
                            )
                        )
            keep_alive()

        # Grid requests are deduplicated first; each staged response is
        # immediately bounded, fanned out, and released before the next grid.
        for grid_key, group_targets in grid_groups.items():
            stage_and_append(
                group_targets[0],
                group_targets,
                include_mid_for_group=False,
                source_entity_id=f"grid:{grid_key[0]}:{grid_key[1]}",
            )
        for mid_region_codes, group_targets in mid_groups.items():
            stage_and_append(
                group_targets[0],
                group_targets,
                include_mid_for_group=True,
                source_entity_id=f"mid-region:{mid_region_codes[0]}:{mid_region_codes[1]}",
            )
        publish_and_finish = getattr(repository, "publish_and_finish", None)
        if callable(publish_and_finish):
            loaded, finished = publish_and_finish(
                run_id=run.run_id,
                source_records=sources,
                values=values,
                grids_fetched=len(grid_groups),
                mid_groups_fetched=len(mid_groups),
                requests_fetched=len(grid_groups) * 3 + len(mid_groups) * 2,
            )
        else:
            loaded = repository.ingest_batch(source_records=sources, values=values)
            finished = repository.finish_sync_run(
                run.run_id,
                status="success",
                grids_fetched=len(grid_groups),
                mid_groups_fetched=len(mid_groups),
                requests_fetched=len(grid_groups) * 3 + len(mid_groups) * 2,
                values_loaded=loaded,
            )
        if finished.status != "success":
            raise RuntimeError(
                f"sync run ownership was lost before publish completion: {finished.status}"
            )
        return {
            "run_id": finished.run_id,
            "status": finished.status,
            "grids_fetched": len(grid_groups),
            "mid_groups_fetched": len(mid_groups),
            "requests_fetched": len(grid_groups) * 3 + len(mid_groups) * 2,
            "values_loaded": loaded,
        }
    except Exception as exc:
        repository.finish_sync_run(
            run.run_id,
            status="failed",
            grids_fetched=0,
            values_loaded=0,
            error=str(exc)[:2000],
        )
        raise


def _retry_call(call: Any, *, retries: int) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return call()
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
            sleep(min(0.25 * (2**attempt), 5.0))
    assert last_error is not None
    raise last_error
