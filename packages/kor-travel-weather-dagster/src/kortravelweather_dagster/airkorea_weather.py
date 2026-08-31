"""AirKorea 측정소 catalog와 실시간 대기질을 주기적으로 publish한다."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from kortravelweather.providers.airkorea import (
    AIRKOREA_MEASUREMENT_DATASET,
    AIRKOREA_PROVIDER,
    AIRKOREA_STATION_DATASET,
    fetch_station_catalog,
    fetch_station_measurement,
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
        locations_total=0,
    )
    catalog_entries: list[tuple[Any, dict[str, Any]]] = []
    try:
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
    values = []
    fetched_at = datetime.now(UTC)
    try:
        for location in active_locations:
            keep_alive(measurement_run.run_id)
            response = fetch_station_measurement(
                client,
                station_name=str(
                    (location.metadata.get("measurement_point") or {}).get(
                        "station_name", location.name
                    )
                ),
                location_id=location.location_id,
                known_at=fetched_at,
                expected_sido=str(
                    (location.metadata.get("measurement_point") or {}).get("address", "")
                ).split()[0]
                or None,
            )
            if response is None:
                continue
            source, station_values = response
            if len(values) + len(station_values) > max_values:
                raise ValueError("AirKorea normalized fact 수가 상한을 초과했습니다.")
            sources.append({**source, "run_id": measurement_run.run_id})
            values.extend(station_values)
            keep_alive(measurement_run.run_id)
        loaded, finished = repository.publish_and_finish(
            run_id=measurement_run.run_id,
            source_records=sources,
            values=values,
            grids_fetched=0,
            requests_fetched=len(active_locations),
        )
        if finished.status != "success":
            raise RuntimeError("AirKorea measurement run ownership을 잃었습니다.")
    except Exception as exc:
        repository.finish_sync_run(
            measurement_run.run_id,
            status="failed",
            requests_fetched=len(sources),
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
        "requests_fetched": 1 + len(active_locations),
        "values_loaded": loaded,
    }
