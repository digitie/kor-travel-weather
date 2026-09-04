"""AirKorea 측정소 catalog와 실시간 대기질을 주기적으로 publish한다."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from kortravelweather.metrics import provider_request
from kortravelweather.models import WeatherValue
from kortravelweather.providers.airkorea import (
    AIRKOREA_MEASUREMENT_DATASET,
    AIRKOREA_PROVIDER,
    AIRKOREA_STATION_DATASET,
    fetch_sido_measurements,
    fetch_station_catalog,
    fetch_station_measurement,
    measurement_point_metadata,
    measurement_source_record,
    measurement_to_weather_values,
    normalize_sido_name,
)
from kortravelweather.providers.base import redact_secrets
from kortravelweather.repository import WeatherRepository


def run_airkorea_weather_sync(
    *,
    repository: WeatherRepository,
    client: Any,
    max_stations: int = 1000,
    max_values: int = 100_000,
) -> dict[str, Any]:
    """Refresh station anchors, then fetch one latest observation per station.

    Station metadata is insert-only: an administrator may disable or annotate
    an anchor without the hourly catalog refresh overwriting it.  Measurements
    use the canonical existing anchor and are published atomically per run.
    """
    if max_stations <= 0 or max_values <= 0:
        raise ValueError("AirKorea budget은 양수여야 합니다.")

    def keep_alive(run_id: str) -> None:
        heartbeat = getattr(repository, "heartbeat_sync_run", None)
        if callable(heartbeat) and heartbeat(run_id) is False:
            raise RuntimeError("AirKorea sync run lease가 만료되어 publish를 중단했습니다.")

    catalog_run = repository.start_sync_run(
        provider=AIRKOREA_PROVIDER,
        dataset_key=AIRKOREA_STATION_DATASET,
        locations_total=max_stations,
    )
    catalog_entries: list[tuple[Any, dict[str, Any]]] = []
    try:
        with provider_request(AIRKOREA_PROVIDER, AIRKOREA_STATION_DATASET):
            catalog_entries = fetch_station_catalog(client, max_stations=max_stations)
        active_locations = []
        for location, _ in catalog_entries:
            keep_alive(catalog_run.run_id)
            existing = repository.get_location(location.location_id)
            if existing is None:
                try:
                    existing = repository.create_location(location)
                except ValueError:
                    # Another worker inserted the same deterministic station
                    # id.  Read its canonical row rather than overwriting it.
                    existing = repository.get_location(location.location_id)
            if existing is not None and existing.enabled:
                active_locations.append(existing)
        catalog_sources = [
            {**source, "run_id": catalog_run.run_id} for _, source in catalog_entries
        ]
        loaded, finished = repository.publish_and_finish(
            run_id=catalog_run.run_id,
            source_records=catalog_sources,
            values=[],
            grids_fetched=0,
            requests_fetched=1,
        )
        if finished.status != "success":
            raise RuntimeError("AirKorea 측정소 catalog run ownership을 잃었습니다.")
    except Exception as exc:
        repository.finish_sync_run(
            catalog_run.run_id,
            status="failed",
            error=str(redact_secrets(str(exc)))[:1000],
        )
        raise

    measurement_run = repository.start_sync_run(
        provider=AIRKOREA_PROVIDER,
        dataset_key=AIRKOREA_MEASUREMENT_DATASET,
        locations_total=len(active_locations),
    )
    sources: list[dict[str, Any]] = []
    values: list[WeatherValue] = []
    failed_stations: list[str] = []
    failure_types: set[str] = set()
    fetched_at = datetime.now(UTC)
    request_count = 0

    def append_measurement(location: Any, measurement: Any) -> None:
        """Normalize one bulk row and enforce the run fact budget."""
        nonlocal values
        source = measurement_source_record(
            measurement,
            location_id=location.location_id,
            fetched_at=fetched_at,
        )
        station_values = measurement_to_weather_values(
            measurement,
            location_id=location.location_id,
            source_record_key=source["source_record_key"],
            known_at=fetched_at,
        )
        if len(values) + len(station_values) > max_values:
            raise ValueError("AirKorea normalized fact 수가 상한을 초과했습니다.")
        sources.append({**source, "run_id": measurement_run.run_id})
        values.extend(station_values)

    # The upstream station endpoint accepts one station name at a time and
    # quickly hits the public request quota for a nationwide catalog.  Group
    # anchors by SIDO and use the bulk endpoint when available.  Names that
    # cannot be mapped (or duplicate within a SIDO) stay quarantined/fallback
    # rather than silently attaching a row to the wrong anchor.
    bulk_available = callable(getattr(client, "sido_measurements", None))
    grouped: dict[str, dict[str, list[Any]]] = {}
    fallback_locations: list[Any] = []
    for location in active_locations:
        point = measurement_point_metadata(location) or {}
        station_name = str(point.get("station_name") or location.name).strip()
        sido = normalize_sido_name(
            str(point.get("sido_name") or point.get("address") or "")
        )
        if bulk_available and sido:
            grouped.setdefault(sido, {}).setdefault(station_name, []).append(location)
        else:
            fallback_locations.append(location)

    matched_locations: set[str] = set()
    for sido, locations_by_name in sorted(grouped.items()):
        keep_alive(measurement_run.run_id)
        try:
            with provider_request(AIRKOREA_PROVIDER, AIRKOREA_MEASUREMENT_DATASET):
                measurements = fetch_sido_measurements(
                    client,
                    sido_name=sido,
                    max_stations=max_stations,
                )
            request_count += 1
        except Exception as exc:
            request_count += 1
            failed_stations.extend(
                location.location_id
                for candidates in locations_by_name.values()
                for location in candidates
            )
            if len(failure_types) < 8:
                failure_types.add(type(exc).__name__)
            continue

        for measurement in measurements:
            station_name = measurement.station_name.strip()
            candidates = locations_by_name.get(station_name, [])
            if len(candidates) != 1:
                # A duplicate station name cannot be safely identified by the
                # current upstream API.  Quarantine instead of cross-wiring
                # the observation to an arbitrary anchor.
                failed_stations.extend(location.location_id for location in candidates)
                if candidates and len(failure_types) < 8:
                    failure_types.add("AmbiguousStationName")
                continue
            location = candidates[0]
            try:
                append_measurement(location, measurement)
            except ValueError as exc:
                if "상한" in str(exc):
                    repository.finish_sync_run(
                        measurement_run.run_id,
                        status="failed",
                        requests_fetched=request_count,
                        error=str(redact_secrets(str(exc)))[:1000],
                    )
                    raise
                failed_stations.append(location.location_id)
                if len(failure_types) < 8:
                    failure_types.add(type(exc).__name__)
                continue
            except Exception as exc:
                failed_stations.append(location.location_id)
                if len(failure_types) < 8:
                    failure_types.add(type(exc).__name__)
                continue
            matched_locations.add(location.location_id)
            keep_alive(measurement_run.run_id)

    # Preserve the compatibility path for test doubles/older clients that do
    # not expose the SIDO bulk method, and for anchors lacking a valid address.
    for location in fallback_locations:
        keep_alive(measurement_run.run_id)
        point = measurement_point_metadata(location) or {}
        try:
            with provider_request(AIRKOREA_PROVIDER, AIRKOREA_MEASUREMENT_DATASET):
                response = fetch_station_measurement(
                    client,
                    station_name=str(point.get("station_name") or location.name),
                    location_id=location.location_id,
                    known_at=fetched_at,
                    expected_sido=str(point.get("address") or "").split()[0] or None,
                )
            request_count += 1
        except Exception as exc:
            request_count += 1
            failed_stations.append(location.location_id)
            if len(failure_types) < 8:
                failure_types.add(type(exc).__name__)
            continue
        if response is None:
            continue
        source, station_values = response
        if len(values) + len(station_values) > max_values:
            error = "AirKorea normalized fact 수가 상한을 초과했습니다."
            repository.finish_sync_run(
                measurement_run.run_id,
                status="failed",
                requests_fetched=request_count,
                error=error,
            )
            raise ValueError(error)
        sources.append({**source, "run_id": measurement_run.run_id})
        values.extend(station_values)
        matched_locations.add(location.location_id)
        keep_alive(measurement_run.run_id)

    missing_locations = [
        location.location_id
        for location in active_locations
        if location.location_id not in matched_locations
        and location.location_id not in failed_stations
    ]
    failed_stations.extend(missing_locations)
    try:
        loaded, finished = repository.publish_and_finish(
            run_id=measurement_run.run_id,
            source_records=sources,
            values=values,
            grids_fetched=0,
            requests_fetched=request_count,
            error=(
                f"{len(failed_stations)}개 측정소 요청 실패"
                f" ({', '.join(sorted(failure_types))})"
                if failed_stations
                else None
            ),
        )
        if finished.status != "success":
            raise RuntimeError("AirKorea measurement run ownership을 잃었습니다.")
    except Exception as exc:
        repository.finish_sync_run(
            measurement_run.run_id,
            status="failed",
            requests_fetched=request_count,
            error=str(redact_secrets(str(exc)))[:1000],
        )
        raise
    return {
        "provider": AIRKOREA_PROVIDER,
        "status": finished.status,
        "station_count": len(catalog_entries),
        "active_station_count": len(active_locations),
        "catalog_run_id": catalog_run.run_id,
        "run_id": measurement_run.run_id,
        "requests_fetched": 1 + request_count,
        "values_loaded": loaded,
        "stations_failed": len(failed_stations),
    }
