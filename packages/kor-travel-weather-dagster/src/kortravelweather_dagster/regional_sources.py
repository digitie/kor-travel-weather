"""전국 단위 관측망(해양·산악·고속도로) 수집.

These three sources share a shape that none of the existing ones have: one call
returns every station in the country, and each row carries its own coordinates.
There is no catalog dataset to refresh separately and no per-location fan-out,
so the run is a bounded fetch followed by short publication transactions. That is
why they share :func:`publish_regional_records` instead of each growing its own
copy of the anchor-creation and publish protocol.

They are collected on their own low-frequency schedule.  The hourly path
already produces about three million facts a day; these add stations rather
than replacing any, and a source that updates a few times a day does not become
more accurate by being asked every hour.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from functools import wraps
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
    UpstreamBlocked,
    fetch_restarea_weather,
    restarea_location,
    restarea_source_record,
    restarea_weather_to_weather_values,
    upstream_block,
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
from kortravelweather.providers.krforest_dust import (
    KRFOREST_DUST_DATASET,
    DustCatalogUnavailable,
    dust_source_record,
    dust_to_weather_values,
    fetch_dust,
    pair_with_stations,
)
from kortravelweather.providers.krforest_dust import (
    station_location as dust_station_location,
)
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings

from . import chunked_publish


def _regional_source_attempt(provider: str, dataset: str):
    """W4 opt-in일 때 bulk fetch 실패도 원래 native 수집 시도로 남긴다."""
    def decorate(function):
        @wraps(function)
        def execute(*args, **kwargs):
            repository = kwargs["repository"]
            settings = kwargs.get("settings") or WeatherSettings()
            exports = settings.service_exports_enabled and settings.service_export_artifact_root
            # 실제 repository의 명시 opt-in만 사용한다. 기본 OFF 시험/호출에서
            # duck-typed repository의 __getattr__까지 호출하지 않는다.
            if (
                not isinstance(repository, WeatherRepository)
                or not (repository.export_status_enabled or exports)
                or provider not in settings.enabled_providers
            ):
                return function(*args, **kwargs)
            run = repository.start_sync_run(
                provider=provider, dataset_key=dataset,
                **({"export_cohort_required": True} if exports else {}),
            )
            try:
                extra = {}
                if exports:
                    from kortravelweather.export_sdk_collection import prepare_sdk_collection

                    extra["_source_export"] = prepare_sdk_collection(
                        repository, run_id=run.run_id, provider=provider, dataset_key=dataset,
                        artifact_root=settings.service_export_artifact_root,
                    )
                result = function(*args, **kwargs, _status_run=run, **extra)
                if result.get("skipped"):
                    repository.finish_sync_run(
                        run.run_id, status="failed",
                        error="원천 수집이 자료 확보 없이 생략되었습니다.",
                    )
                return result
            except Exception as exc:
                repository.finish_sync_run(
                    run.run_id, status="failed", error=str(redact_secrets(str(exc)))[:1000],
                )
                raise
        return execute
    return decorate


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
    sync_run: Any | None = None,
    source_export: Any | None = None,
) -> dict[str, Any]:
    """Create any missing anchors, then publish one run's worth of values.

    Anchors are insert-only, matching the AirKorea catalog path: an
    administrator may disable or annotate a station without the next collection
    silently restoring it.
    """
    if max_values <= 0:
        raise ValueError("max_values는 양수여야 합니다.")

    fetched_at = datetime.now(UTC)
    run = sync_run or repository.start_sync_run(
        provider=provider, dataset_key=dataset_key, locations_total=len(records)
    )
    try:
        if sync_run is not None:
            repository.set_sync_run_locations_total(run.run_id, len(records))
        sources: list[dict[str, Any]] = []
        values: list[WeatherValue] = []
        chunk_locations: set[str] = set()
        source_count = 0
        value_count = 0
        published = 0
        anchored = 0
        skipped_without_coordinates = 0
        skipped_without_values = 0
        disabled = 0
        truncated = False
        latest_sources = {}
        if source_export is not None:
            # 청정넷처럼 시간별 관측이 여러 개인 source도 native 이력은 모두 저장한다.
            # 승인 current에는 최신 원천 한 건만 사용하며 임의로 관측 시각을 만들지 않는다.
            for record in records:
                candidate = to_location(record)
                if candidate is not None:
                    observed = getattr(record[0] if isinstance(record, tuple) else record,
                                       "observed_at", None)
                    previous = latest_sources.get(candidate.location_id)
                    if observed is not None and (previous is None or observed > previous[0]):
                        latest_sources[candidate.location_id] = (observed, record)

        def flush() -> None:
            nonlocal published
            if not values:
                return
            published += repository.ingest_batch(source_records=sources, values=values)
            sources.clear()
            values.clear()
            chunk_locations.clear()

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
                skipped_without_values += 1
                # A station that reported nothing usable leaves no lineage
                # record either, or the run would publish provenance for facts
                # that do not exist.
                continue
            # The budget cuts between stations, never inside one: half a
            # station's readings, published as if complete, is worse than
            # fewer stations.
            if value_count + len(station_values) > max_values:
                truncated = True
                break
            export_latest = latest_sources.get(existing.location_id)
            if (
                source_export is not None and source_export.complete
                and existing.location_id in source_export.plans
                and (dataset_key == KHOA_BEACH_INDEX_DATASET
                     or (export_latest is not None and export_latest[1] is record))
            ):
                # 기존 native batch와 동일한 source/fact를 한 번만 ingest한다.
                flush()
                published += source_export.collector.ingest(
                    source_export.plans.pop(existing.location_id),
                    record=source, values=station_values,
                )
                source_count += 1
                value_count += len(station_values)
                continue
            # 전체 원천의 정규화 결과를 쌓지 않고 현재 chunk만 보관한다.
            # 하나의 원천 관측과 그 metric set은 같은 transaction에 둔다.
            if values and (
                (
                    existing.location_id not in chunk_locations
                    and len(chunk_locations) >= chunked_publish.PUBLISH_CHUNK_LOCATIONS
                )
                or len(values) + len(station_values) > chunked_publish.PUBLISH_CHUNK_VALUES
            ):
                flush()
            sources.append(source)
            values.extend(station_values)
            chunk_locations.add(existing.location_id)
            source_count += 1
            value_count += len(station_values)

        # 늦은 원천 변환/게시 실패 뒤에도 이전 chunk와 그 저장량은 남는다.
        # 모든 source는 위에서 facts와 함께 게시했으므로 finish에는 빈 묶음만 전달한다.
        flush()
        if source_export is not None:
            source_export.finish()
        loaded, finished = repository.publish_and_finish(
            run_id=run.run_id,
            source_records=[],
            values=[],
            grids_fetched=0,
            requests_fetched=1,
            values_loaded_offset=published,
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
        "produced_nothing": value_count == 0,
        "records_fetched": len(records),
        "locations_anchored": anchored,
        "locations_disabled": disabled,
        "skipped_without_coordinates": skipped_without_coordinates,
        "skipped_without_values": skipped_without_values,
        "values_loaded": loaded,
        "values_truncated": truncated,
        "source_records": source_count,
    }


@_regional_source_attempt(KHOA_PROVIDER, KHOA_BEACH_INDEX_DATASET)
def run_khoa_beach_index_sync(
    *,
    repository: WeatherRepository,
    api_key: str,
    max_places: int,
    max_values: int,
    retries: int = 3,
    timeout: float | None = None,
    settings: WeatherSettings | None = None,
    _status_run: Any | None = None,
    _source_export: Any | None = None,
) -> dict[str, Any]:
    skipped = skipped_when_disabled(KHOA_PROVIDER, settings or WeatherSettings())
    if skipped is not None:
        return skipped
    with provider_request(KHOA_PROVIDER, KHOA_BEACH_INDEX_DATASET):
        assembly = None
        if _source_export is not None:
            from kortravelweather.export_sdk_pages import BeachPageAssembly

            assembly = BeachPageAssembly()
        places = fetch_beach_index(
            api_key=api_key, max_places=max_places, retries=retries, timeout=timeout,
            **({"export_assembly": assembly} if assembly is not None else {}),
        )
    if assembly is not None and not assembly.receipt.complete:
        _source_export.invalidate()
    return publish_regional_records(
        repository=repository,
        provider=KHOA_PROVIDER,
        dataset_key=KHOA_BEACH_INDEX_DATASET,
        records=places,
        to_location=beach_location,
        to_values=beach_index_to_weather_values,
        build_source_record=beach_index_source_record,
        max_values=max_values,
        sync_run=_status_run,
        source_export=_source_export,
    )


@_regional_source_attempt(KRFOREST_PROVIDER, KRFOREST_MOUNTAIN_DATASET)
def run_krforest_mountain_sync(
    *,
    repository: WeatherRepository,
    api_key: str,
    max_records: int,
    max_values: int,
    timeout: float | None = None,
    settings: WeatherSettings | None = None,
    _status_run: Any | None = None,
    _source_export: Any | None = None,
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
        sync_run=_status_run,
        source_export=_source_export,
    )


@_regional_source_attempt(KRFOREST_PROVIDER, KRFOREST_DUST_DATASET)
def run_krforest_dust_sync(
    *,
    repository: WeatherRepository,
    api_key: str,
    hours: int,
    max_records: int,
    max_values: int,
    timeout: float | None = None,
    settings: WeatherSettings | None = None,
    _status_run: Any | None = None,
    _source_export: Any | None = None,
) -> dict[str, Any]:
    """Publish 청정넷 readings, or say why it could not.

    The readings and the station catalog are separately approved data.go.kr
    datasets, so a key can hold one and not the other.  Without the catalog a
    reading has a station code and no coordinates, which is not something to
    guess at -- and not something a nightly red schedule helps with either,
    since the remedy is an application that takes days.
    """
    runtime = settings or WeatherSettings()
    skipped = skipped_when_disabled(KRFOREST_PROVIDER, runtime)
    if skipped is not None:
        return skipped
    with provider_request(KRFOREST_PROVIDER, KRFOREST_DUST_DATASET):
        try:
            stations, measurements = fetch_dust(
                api_key=api_key,
                hours=hours,
                max_records=max_records,
                timeout=timeout,
            )
        except DustCatalogUnavailable as exc:
            return {
                "provider": KRFOREST_PROVIDER,
                "dataset_key": KRFOREST_DUST_DATASET,
                "skipped": True,
                "reason": str(exc),
                "records_fetched": 0,
                "values_loaded": 0,
            }
    paired = pair_with_stations(measurements, stations)
    # The station is what carries the coordinates, so the record handed to the
    # publish path is the pair; the reading alone cannot be anchored.
    return publish_regional_records(
        repository=repository,
        provider=KRFOREST_PROVIDER,
        dataset_key=KRFOREST_DUST_DATASET,
        records=paired,
        to_location=lambda pair: dust_station_location(pair[1]),
        to_values=lambda pair, **kwargs: dust_to_weather_values(pair[0], **kwargs),
        build_source_record=lambda pair, **kwargs: dust_source_record(pair[0], **kwargs),
        max_values=max_values,
        sync_run=_status_run,
        source_export=_source_export,
    )


@_regional_source_attempt(KREX_PROVIDER, KREX_RESTAREA_DATASET)
def run_krex_restarea_sync(
    *,
    repository: WeatherRepository,
    client: Any,
    max_records: int,
    max_values: int,
    lookback_hours: int = 24,
    settings: WeatherSettings | None = None,
    _status_run: Any | None = None,
    _source_export: Any | None = None,
) -> dict[str, Any]:
    """Fetch and publish, owning the event loop ``KrexClient`` now requires.

    The client is constructed by the caller (so a disabled provider never
    demands its credential) but entered and closed here: ``async with
    client:`` is what calls ``aclose()``, and doing that inside the same
    ``asyncio.run()`` that makes the one fetch call is what
    ``python-krex-api``'s shared rate limiter requires -- it binds to
    whichever event loop first calls ``acquire()`` and raises if a later
    call arrives from a different one, which a second, separate
    ``asyncio.run()`` always would be.
    """
    skipped = skipped_when_disabled(KREX_PROVIDER, settings or WeatherSettings())
    if skipped is not None:
        return skipped
    try:
        records = asyncio.run(
            _fetch_restarea_weather(
                client, max_records=max_records, lookback_hours=lookback_hours,
                **({"export_page_observer": _source_export.observe_krex_page}
                   if _source_export is not None else {}),
            )
        )
    except Exception as exc:
        block = upstream_block(exc)
        if block is None:
            raise
        # Still a failure -- the step goes red, as it should while nothing is
        # collected -- but one that says what it is.
        raise UpstreamBlocked(KREX_PROVIDER, KREX_RESTAREA_DATASET, block) from exc
    return publish_regional_records(
        repository=repository,
        provider=KREX_PROVIDER,
        dataset_key=KREX_RESTAREA_DATASET,
        records=records,
        to_location=restarea_location,
        to_values=restarea_weather_to_weather_values,
        build_source_record=restarea_source_record,
        max_values=max_values,
        sync_run=_status_run,
        source_export=_source_export,
    )


def _is_upstream_block(exc: BaseException) -> bool:
    return upstream_block(exc) is not None


async def _fetch_restarea_weather(
    client: Any, *, max_records: int, lookback_hours: int, export_page_observer=None,
) -> list[Any]:
    async with client:
        with provider_request(
            KREX_PROVIDER, KREX_RESTAREA_DATASET, blocked=_is_upstream_block
        ):
            return await fetch_restarea_weather(
                client, max_records=max_records, lookback_hours=lookback_hours,
                **({"export_page_observer": export_page_observer}
                   if export_page_observer is not None else {}),
            )
