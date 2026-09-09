"""전국 단위 관측망(해양·산악·고속도로) 수집.

These three sources share a shape that none of the existing ones have: one call
returns every station in the country, and each row carries its own coordinates.
There is no catalog dataset to refresh separately and no per-location fan-out,
so the whole run is a single request followed by one atomic publish.  That is
why they share :func:`publish_regional_records` instead of each growing its own
copy of the anchor-creation and publish protocol.

They are collected on their own low-frequency schedule.  The hourly path
already produces about three million facts a day; these add stations rather
than replacing any, and a source that updates a few times a day does not become
more accurate by being asked every hour.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from kortravelweather.metrics import provider_request
from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.providers import redact_secrets
from kortravelweather.providers.khoa import (
    KHOA_BEACH_INDEX_DATASET,
    KHOA_PROVIDER,
    beach_index_source_record,
    beach_index_to_weather_values,
    beach_location,
    fetch_beach_index,
)
from kortravelweather.providers.krex import (
    KREX_PROVIDER,
    KREX_RESTAREA_DATASET,
    fetch_restarea_weather,
    restarea_location,
    restarea_source_record,
    restarea_weather_to_weather_values,
)
from kortravelweather.providers.krforest import (
    KRFOREST_MOUNTAIN_DATASET,
    KRFOREST_PROVIDER,
    fetch_mountain_weather,
    mountain_source_record,
    mountain_weather_to_weather_values,
)
from kortravelweather.providers.krforest import (
    station_location as mountain_station_location,
)
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings


def skipped_when_disabled(provider: str, settings: WeatherSettings) -> dict[str, Any] | None:
    """Return a skip result when the operator has not enabled this provider.

    The dedicated Korean-source assets historically ran on their schedule alone
    and ignored ``enabled_providers``, which only gated the external HTTP
    adapters.  These three appear in that list, so an operator who removes one
    expects collection to stop -- a list called "enabled providers" that does
    not disable anything is worse than no list.

    Skipping is reported rather than raising: a provider that is off on purpose
    is not a failed run, and a red schedule every twelve hours trains people to
    ignore it.
    """
    if provider in settings.enabled_providers:
        return None
    return {
        "provider": provider,
        "skipped": True,
        "reason": "provider가 KOR_TRAVEL_WEATHER_ENABLED_PROVIDERS에 없습니다.",
        "records_fetched": 0,
        "values_loaded": 0,
    }


def publish_regional_records(
    *,
    repository: WeatherRepository,
    provider: str,
    dataset_key: str,
    records: Sequence[Any],
    to_location: Callable[[Any], WeatherLocation | None],
    to_values: Callable[..., list[WeatherValue]],
    build_source_record: Callable[..., dict[str, Any]],
    max_values: int,
) -> dict[str, Any]:
    """Create any missing anchors, then publish one run's worth of values.

    Anchors are insert-only, matching the AirKorea catalog path: an
    administrator may disable or annotate a station without the next collection
    silently restoring it.
    """
    if max_values <= 0:
        raise ValueError("max_values는 양수여야 합니다.")

    fetched_at = datetime.now(UTC)
    run = repository.start_sync_run(
        provider=provider, dataset_key=dataset_key, locations_total=len(records)
    )
    try:
        sources: list[dict[str, Any]] = []
        values: list[WeatherValue] = []
        anchored = 0
        skipped_without_coordinates = 0
        disabled = 0
        truncated = False
        for record in records:
            location = to_location(record)
            if location is None:
                skipped_without_coordinates += 1
                continue
            existing = repository.get_location(location.location_id)
            if existing is None:
                try:
                    existing = repository.create_location(location)
                except ValueError:
                    # Another worker inserted the same deterministic id; read
                    # its canonical row rather than overwriting it.
                    existing = repository.get_location(location.location_id)
            if existing is None:
                continue
            if not existing.enabled:
                disabled += 1
                continue
            anchored += 1
            # The lineage record is built first because every value has to name
            # it; a value cannot be created and then re-pointed, since its own
            # identity is derived from the source key.
            source = {
                **build_source_record(
                    record, location_id=existing.location_id, fetched_at=fetched_at
                ),
                "run_id": run.run_id,
            }
            station_values = to_values(
                record,
                location_id=existing.location_id,
                source_record_key=source["source_record_key"],
                known_at=fetched_at,
            )
            if not station_values:
                # A station that reported nothing usable leaves no lineage
                # record either, or the run would publish provenance for facts
                # that do not exist.
                continue
            # The budget cuts between stations, never inside one: half a
            # station's readings, published as if complete, is worse than
            # fewer stations.
            if len(values) + len(station_values) > max_values:
                truncated = True
                break
            sources.append(source)
            values.extend(station_values)

        loaded, finished = repository.publish_and_finish(
            run_id=run.run_id,
            source_records=sources,
            values=values,
            grids_fetched=0,
            requests_fetched=1,
        )
        if finished.status != "success":
            raise RuntimeError(f"{provider} run ownership을 잃었습니다.")
    except Exception as exc:
        repository.finish_sync_run(
            run.run_id, status="failed", error=str(redact_secrets(str(exc)))[:1000]
        )
        raise
    return {
        "provider": provider,
        "dataset_key": dataset_key,
        # A run that fetched rows and anchored none looked exactly like a
        # healthy run: krforest returned 513 stations with no coordinates and
        # reported success three times before anyone noticed it had never
        # written a value.
        "produced_nothing": len(values) == 0,
        "records_fetched": len(records),
        "locations_anchored": anchored,
        "locations_disabled": disabled,
        "skipped_without_coordinates": skipped_without_coordinates,
        "values_loaded": loaded,
        "values_truncated": truncated,
        "source_records": len(sources),
    }


def run_khoa_beach_index_sync(
    *,
    repository: WeatherRepository,
    client: Any,
    max_places: int,
    max_values: int,
    settings: WeatherSettings | None = None,
) -> dict[str, Any]:
    skipped = skipped_when_disabled(KHOA_PROVIDER, settings or WeatherSettings())
    if skipped is not None:
        return skipped
    with provider_request(KHOA_PROVIDER, KHOA_BEACH_INDEX_DATASET):
        places = fetch_beach_index(client, max_places=max_places)
    return publish_regional_records(
        repository=repository,
        provider=KHOA_PROVIDER,
        dataset_key=KHOA_BEACH_INDEX_DATASET,
        records=places,
        to_location=beach_location,
        to_values=beach_index_to_weather_values,
        build_source_record=beach_index_source_record,
        max_values=max_values,
    )


def run_krforest_mountain_sync(
    *,
    repository: WeatherRepository,
    api_key: str,
    max_records: int,
    max_values: int,
    timeout: float | None = None,
    settings: WeatherSettings | None = None,
) -> dict[str, Any]:
    skipped = skipped_when_disabled(KRFOREST_PROVIDER, settings or WeatherSettings())
    if skipped is not None:
        return skipped
    with provider_request(KRFOREST_PROVIDER, KRFOREST_MOUNTAIN_DATASET):
        records = fetch_mountain_weather(
            api_key=api_key, max_records=max_records, timeout=timeout
        )
    return publish_regional_records(
        repository=repository,
        provider=KRFOREST_PROVIDER,
        dataset_key=KRFOREST_MOUNTAIN_DATASET,
        records=records,
        to_location=mountain_station_location,
        to_values=mountain_weather_to_weather_values,
        build_source_record=mountain_source_record,
        max_values=max_values,
    )


def run_krex_restarea_sync(
    *,
    repository: WeatherRepository,
    client: Any,
    max_records: int,
    max_values: int,
    lookback_hours: int = 24,
    settings: WeatherSettings | None = None,
) -> dict[str, Any]:
    skipped = skipped_when_disabled(KREX_PROVIDER, settings or WeatherSettings())
    if skipped is not None:
        return skipped
    with provider_request(KREX_PROVIDER, KREX_RESTAREA_DATASET):
        records = fetch_restarea_weather(
            client, max_records=max_records, lookback_hours=lookback_hours
        )
    return publish_regional_records(
        repository=repository,
        provider=KREX_PROVIDER,
        dataset_key=KREX_RESTAREA_DATASET,
        records=records,
        to_location=restarea_location,
        to_values=restarea_weather_to_weather_values,
        build_source_record=restarea_source_record,
        max_values=max_values,
    )
