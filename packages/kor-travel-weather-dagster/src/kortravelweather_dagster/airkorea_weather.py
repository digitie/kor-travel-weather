"""AirKorea 측정소 catalog와 실시간 대기질을 주기적으로 publish한다."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kortravelweather.metrics import provider_request
from kortravelweather.models import WeatherValue
from kortravelweather.providers.airkorea import (
    AIRKOREA_MEASUREMENT_DATASET,
    AIRKOREA_PROVIDER,
    AIRKOREA_STATION_DATASET,
    StationSidoMismatch,
    anchor_sido_names,
    fetch_sido_measurements,
    fetch_station_catalog,
    fetch_station_measurement,
    measurement_point_metadata,
    measurement_sido_name,
    measurement_sido_names,
    measurement_source_record,
    measurement_to_weather_values,
)
from kortravelweather.providers.base import redact_secrets
from kortravelweather.repository import WeatherRepository

from .chunked_publish import chunk_publications, uncited_sources

logger = logging.getLogger(__name__)

#: 측정소 실패 사유. 요청 오류만 일시적이고(client가 이미 재시도함) 나머지는 같은 입력이면
#: 다음 run에도 같은 결과가 나오는 결정적 실패다.
NOT_IN_RESPONSE = "not_in_response"
AMBIGUOUS_NAME = "ambiguous_name"
#: 이름은 맞지만 원천 행의 ``sidoName``이 anchor의 시도와 달라 붙이지 않았다.
SIDO_MISMATCH = "sido_mismatch"
REQUEST_ERROR = "request_error"
NORMALIZE_ERROR = "normalize_error"
TRANSIENT_REASONS = frozenset({REQUEST_ERROR})
FAILED_SAMPLE_SIZE = 20


def run_airkorea_weather_sync(
    *,
    repository: WeatherRepository,
    client: Any,
    max_stations: int = 1000,
    max_values: int = 100_000,
    export_artifact_root: Path | None = None,
) -> dict[str, Any]:
    """Own the event loop ``AirKoreaClient`` now requires for the whole run.

    The client is constructed by the caller -- so a disabled provider never
    demands its credential -- but entered and closed here, in the same
    ``asyncio.run()`` that makes every fetch call.  ``python-airkorea-api``'s
    shared rate limiter binds to whichever event loop first calls
    ``acquire()`` and raises if a later call arrives from a different one, and
    the measurement loop below calls the client once per SIDO -- a separate
    ``asyncio.run()`` per call, the obvious alternative, would trip that on
    the second province.
    """
    return asyncio.run(
        _run_airkorea_weather_sync(
            repository=repository,
            client=client,
            max_stations=max_stations,
            max_values=max_values,
            export_artifact_root=export_artifact_root,
        )
    )


async def _run_airkorea_weather_sync(
    *,
    repository: WeatherRepository,
    client: Any,
    max_stations: int = 1000,
    max_values: int = 100_000,
    export_artifact_root: Path | None = None,
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

    async with client:
        catalog_run = repository.start_sync_run(
            provider=AIRKOREA_PROVIDER,
            dataset_key=AIRKOREA_STATION_DATASET,
            locations_total=max_stations,
        )
        catalog_entries: list[tuple[Any, dict[str, Any]]] = []
        catalog_requests = 1
        try:
            with provider_request(AIRKOREA_PROVIDER, AIRKOREA_STATION_DATASET):
                catalog_entries = await fetch_station_catalog(client, max_stations=max_stations)
            if not catalog_entries and export_artifact_root is not None:
                catalog_requests += 1
                with provider_request(AIRKOREA_PROVIDER, AIRKOREA_STATION_DATASET):
                    catalog_entries = await fetch_station_catalog(client, max_stations=max_stations)
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
            loaded, finished = await asyncio.to_thread(
                repository.publish_and_finish,
                run_id=catalog_run.run_id,
                source_records=catalog_sources,
                values=[],
                grids_fetched=0,
                requests_fetched=catalog_requests,
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
            **({"export_cohort_required": True} if export_artifact_root is not None else {}),
        )
        export_collector, export_plans = None, {}
        if export_artifact_root is not None:
            try:
                from kortravelweather.export_sdk_collector import SdkExportCollector, sdk_descriptor
                from kortravelweather.export_source_cohorts import declare_source_cohort

                def prepare_exports() -> tuple[Any, dict[str, Any]]:
                    # cohort 선언과 준비는 DB transaction이고 준비는 잠금 경합을 막히는
                    # sleep으로 재시도한다. event loop를 멈추지 않게 thread에서 돈다.
                    declare_source_cohort(
                        repository, run_id=measurement_run.run_id, provider=AIRKOREA_PROVIDER,
                        dataset_key=AIRKOREA_MEASUREMENT_DATASET,
                        descriptors=[sdk_descriptor(
                            location, provider=AIRKOREA_PROVIDER,
                            dataset_key=AIRKOREA_MEASUREMENT_DATASET,
                        ) for location in active_locations],
                    )
                    collector = SdkExportCollector(
                        repository, run_id=measurement_run.run_id,
                        artifact_root=export_artifact_root,
                    )
                    return collector, {location.location_id: collector.prepare(
                        location=location, provider=AIRKOREA_PROVIDER,
                        dataset_key=AIRKOREA_MEASUREMENT_DATASET,
                    ) for location in active_locations}

                export_collector, export_plans = await asyncio.to_thread(prepare_exports)
            except Exception:
                # cohort/prepare 실패는 native 수집을 막지 않는다. 원자적 intent가
                # reader에서 이전 정상 source로 실패를 숨기지 못하게 한다.
                logger.warning("airkorea_export_prepare_deferred run_id=%s", measurement_run.run_id)
                export_collector = None
        sources: list[dict[str, Any]] = []
        values: list[WeatherValue] = []
        # location_id -> 첫 실패 사유. 다른 묶음에서 맞춘 측정소는 끝에서 빠진다.
        failures: dict[str, str] = {}
        failure_types: set[str] = set()

        def fail(location_id: str, reason: str, exc: BaseException | None = None) -> None:
            failures.setdefault(location_id, reason)
            if exc is not None and len(failure_types) < 8:
                failure_types.add(type(exc).__name__)

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
        # location_id -> (묻는 bulk 묶음들, 원천 행 sidoName과 대조할 anchor 시도 기대값).
        # 기대값은 두 묶음 이상을 묻는 anchor(통합 주소)만 갖는다. 묶음 하나만 묻는 anchor는
        # None이고 요청한 묶음의 시도로 판단한다. 시도별 bulk 응답은 요청한 시도의 행이라
        # 행 시도 대조가 더하는 보호가 거의 없고, 원천 sidoName 표기가 바뀌면 그 시도의
        # anchor 전체가 멈추기 때문이다.
        anchor_sidos: dict[str, tuple[tuple[str, ...], tuple[str, ...] | None]] = {}
        fallback_locations: list[Any] = []
        for location in active_locations:
            point = measurement_point_metadata(location) or {}
            station_name = str(point.get("station_name") or location.name).strip()
            sidos = measurement_sido_names(point)
            if bulk_available and sidos:
                anchor_sidos[location.location_id] = (
                    sidos, anchor_sido_names(point) if len(sidos) > 1 else None,
                )
                for sido in sidos:
                    grouped.setdefault(sido, {}).setdefault(station_name, []).append(location)
            else:
                fallback_locations.append(location)

        # 묶음 응답을 모두 받은 뒤에 붙인다. 시도를 하나로 가리지 못한 통합 주소 anchor는
        # 두 묶음에서 같은 이름이 나오면 어느 쪽이 제 관측인지 정할 수 없어서다.
        # location_id -> {원천 행 시도: 측정 행}
        bindings: dict[str, dict[str, Any]] = {}
        bound_locations: dict[str, Any] = {}
        ambiguous: set[str] = set()
        failed_sidos: set[str] = set()
        for sido, locations_by_name in sorted(grouped.items()):
            keep_alive(measurement_run.run_id)
            try:
                with provider_request(AIRKOREA_PROVIDER, AIRKOREA_MEASUREMENT_DATASET):
                    measurements = await fetch_sido_measurements(
                        client,
                        sido_name=sido,
                        max_stations=max_stations,
                    )
                    if not measurements and export_artifact_root is not None:
                        request_count += 1
                        measurements = await fetch_sido_measurements(
                            client, sido_name=sido, max_stations=max_stations,
                        )
                request_count += 1
            except Exception as exc:
                request_count += 1
                failed_sidos.add(sido)
                for candidates in locations_by_name.values():
                    for location in candidates:
                        fail(location.location_id, REQUEST_ERROR, exc)
                continue

            for measurement in measurements:
                named = locations_by_name.get(measurement.station_name.strip(), [])
                if not named:
                    continue
                # 두 묶음 이상을 묻는 anchor(통합 주소)는 원천 행의 시도(sidoName)를 anchor의
                # 시도와 대조해 같은 이름의 다른 시도 측정소 관측을 붙이지 않는다. 행에 시도가
                # 없으면 요청한 묶음의 시도로 본다(시도별 bulk 응답 계약). 묶음 하나만 묻는
                # anchor는 행 시도를 보지 않고 요청한 묶음의 시도로 받는다.
                row_sido = measurement_sido_name(measurement) or sido
                # (anchor, 그 anchor가 이 행을 받은 시도)
                candidates: list[tuple[Any, str]] = []
                for location in named:
                    expected = anchor_sidos[location.location_id][1]
                    if expected is None:
                        candidates.append((location, sido))
                    elif row_sido in expected:
                        candidates.append((location, row_sido))
                if not candidates:
                    for location in named:
                        fail(location.location_id, SIDO_MISMATCH)
                    continue
                if len(candidates) != 1:
                    # A duplicate station name cannot be safely identified by the
                    # current upstream API.  Quarantine instead of cross-wiring
                    # the observation to an arbitrary anchor.
                    for location, _ in candidates:
                        fail(location.location_id, AMBIGUOUS_NAME)
                        ambiguous.add(location.location_id)
                    continue
                location, bound_sido = candidates[0]
                # 통합 주소 측정소는 두 묶음에 들어 있을 수 있다. 같은 시도 행은 처음 것만 쓴다.
                bindings.setdefault(location.location_id, {}).setdefault(bound_sido, measurement)
                bound_locations[location.location_id] = location

        matched_locations: set[str] = set()
        for location_id, rows in bindings.items():
            if location_id in ambiguous:
                continue
            queried, expected = anchor_sidos[location_id]
            if len(rows) != 1:
                # 시도를 가리지 못한 anchor가 서로 다른 시도 응답에서 같은 이름을 찾았다.
                fail(location_id, AMBIGUOUS_NAME)
                continue
            if expected is not None and len(expected) > 1 and failed_sidos.intersection(queried):
                # 못 받은 묶음에도 같은 이름이 있었는지 알 수 없어 붙이지 않는다. 그 묶음의
                # request_error가 이미 남아 있다.
                continue
            location = bound_locations[location_id]
            try:
                append_measurement(location, next(iter(rows.values())))
            except ValueError as exc:
                if "상한" in str(exc):
                    repository.finish_sync_run(
                        measurement_run.run_id,
                        status="failed",
                        requests_fetched=request_count,
                        error=str(redact_secrets(str(exc)))[:1000],
                    )
                    raise
                fail(location_id, NORMALIZE_ERROR, exc)
                continue
            except Exception as exc:
                fail(location_id, NORMALIZE_ERROR, exc)
                continue
            matched_locations.add(location_id)
            keep_alive(measurement_run.run_id)

        # Preserve the compatibility path for test doubles/older clients that do
        # not expose the SIDO bulk method, and for anchors lacking a valid address.
        for location in fallback_locations:
            keep_alive(measurement_run.run_id)
            point = measurement_point_metadata(location) or {}
            try:
                with provider_request(AIRKOREA_PROVIDER, AIRKOREA_MEASUREMENT_DATASET):
                    response = await fetch_station_measurement(
                        client,
                        station_name=str(point.get("station_name") or location.name),
                        location_id=location.location_id,
                        known_at=fetched_at,
                        expected_sido=str(point.get("address") or "").split()[0] or None,
                    )
                    if response is None and export_artifact_root is not None:
                        request_count += 1
                        response = await fetch_station_measurement(
                            client, station_name=str(point.get("station_name") or location.name),
                            location_id=location.location_id, known_at=fetched_at,
                            expected_sido=str(point.get("address") or "").split()[0] or None,
                        )
                request_count += 1
            except StationSidoMismatch as exc:
                # 응답은 받았지만 시도 대조에 실패했다. 다음 run에도 같으니 결정적 실패다.
                request_count += 1
                fail(location.location_id, SIDO_MISMATCH, exc)
                continue
            except Exception as exc:
                request_count += 1
                fail(location.location_id, REQUEST_ERROR, exc)
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

        # 응답에 없던 측정소도 실패다. 다른 묶음에서 맞춘 측정소의 앞선 실패는 지운다.
        for location in active_locations:
            if location.location_id not in matched_locations:
                failures.setdefault(location.location_id, NOT_IN_RESPONSE)
        failed_stations = {
            location_id: reason for location_id, reason in failures.items()
            if location_id not in matched_locations
        }
        failure_reasons = dict(sorted(Counter(failed_stations.values()).items()))
        try:
            # Stations publish in short location chunks (``chunked_publish``),
            # off the event loop since a lock race retries with a blocking
            # pause.  A chunk that fails leaves earlier chunks -- whole
            # stations -- published, and the run row carries what they loaded.
            published = 0
            if export_collector is not None:
                by_source = {}
                for value in values:
                    by_source.setdefault(value.source_record_key, []).append(value)
                for source in sources:
                    keep_alive(measurement_run.run_id)
                    published += await asyncio.to_thread(
                        export_collector.ingest, export_plans.pop(source["source_entity_id"], None),
                        record=source, values=by_source.pop(source["source_record_key"], []),
                    )
                for location_id, prepared in export_plans.items():
                    if prepared is not None:
                        # 측정소 실패 사유(시도 대조 실패 포함)를 거절 사유로 그대로 남긴다.
                        export_collector._reject(
                            prepared.identity, "SOURCE_COVERAGE_UNCONFIRMED",
                            reason=failed_stations.get(location_id, NOT_IN_RESPONSE),
                        )
            else:
                for chunk_sources, chunk in chunk_publications(sources, values):
                    published += await asyncio.to_thread(
                        repository.ingest_batch, source_records=chunk_sources, values=chunk
                    )
            loaded, finished = await asyncio.to_thread(
                repository.publish_and_finish,
                run_id=measurement_run.run_id,
                source_records=(
                    [] if export_collector is not None else uncited_sources(sources, values)
                ),
                values=[],
                values_loaded_offset=published,
                grids_fetched=0,
                requests_fetched=request_count,
                error=(
                    f"{len(failed_stations)}개 측정소 수집 실패 ("
                    + ", ".join(f"{key}={count}" for key, count in failure_reasons.items())
                    + (f"; {', '.join(sorted(failure_types))}" if failure_types else "")
                    + ")"
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
        transient = sum(
            count for key, count in failure_reasons.items() if key in TRANSIENT_REASONS
        )
        # native run은 success로 둔다(측정한 측정소는 그대로 승격돼야 한다). 일부 측정소를
        # 못 받은 run은 Dagster 결과에서만 partial로 드러낸다.
        return {
            "provider": AIRKOREA_PROVIDER,
            "status": "partial" if failed_stations else finished.status,
            "station_count": len(catalog_entries),
            "active_station_count": len(active_locations),
            "catalog_run_id": catalog_run.run_id,
            "run_id": measurement_run.run_id,
            "requests_fetched": catalog_requests + request_count,
            "values_loaded": loaded,
            "stations_failed": len(failed_stations),
            "stations_failed_transient": transient,
            "stations_failed_deterministic": len(failed_stations) - transient,
            "station_failure_reasons": failure_reasons,
            "failed_station_sample": sorted(failed_stations)[:FAILED_SAMPLE_SIZE],
        }
