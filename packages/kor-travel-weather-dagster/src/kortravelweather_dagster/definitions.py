"""Dagster Definitions and hourly KMA sync schedule."""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import cache

from dagster import (
    AssetExecutionContext,
    AssetObservation,
    DefaultScheduleStatus,
    Definitions,
    Failure,
    MetadataValue,
    asset,
    define_asset_job,
    in_process_executor,
    multiprocess_executor,
)
from kortravelcommon.dagster import RecoveryPolicy
from kortravelcommon.deadline import DeadlineExceeded
from kortravelcommon.scheduling import staggered_cron

from kortravelweather.metrics import start_metrics_server
from kortravelweather.models import kst_now
from kortravelweather.providers import (
    PROVIDER_CATALOG,
    ProviderLocation,
    create_configured_provider,
    provider_spec,
    redact_secrets,
)
from kortravelweather.providers.khoa import KHOA_PROVIDER
from kortravelweather.providers.krex import KREX_PROVIDER, UpstreamBlocked
from kortravelweather.providers.krforest import KRFOREST_PROVIDER
from kortravelweather.providers.sampling import spatially_even_subset
from kortravelweather.repository import SyncRunAlreadyActive, WeatherRepository
from kortravelweather.retention import run_short_forecast_retention, short_forecast_cutoff
from kortravelweather.settings import WeatherSettings

from .address_refresh import address_refresh_job, address_refresh_sensor
from .airkorea_weather import run_airkorea_weather_sync
from .export_publisher import weather_export_publisher_sensor
from .external_weather import run_external_weather_sync
from .kma_weather import (
    ALERT_SKIP_LOG_IDS,
    ALERT_STARVED_RUNS,
    KMA_SHORT_FORECAST,
    KMA_ULTRA_SHORT_FORECAST,
    KMA_ULTRA_SHORT_NOWCAST,
    WeatherTarget,
    run_weather_sync,
    targets_from_settings,
)
from .overlap import log_overlap_skip, overlap_skip_result, skips_live_overlap
from .recovery import optional_recovery_sensors
from .regional_sources import (
    run_khoa_beach_index_sync,
    run_krex_restarea_sync,
    run_krforest_dust_sync,
    run_krforest_mountain_sync,
    skipped_when_disabled,
)
from .resources import (
    AirKoreaResource,
    ExternalWeatherProviderResource,
    KhoaResource,
    KmaClientResource,
    KrexResource,
    KrforestResource,
    WeatherRepositoryResource,
)
from .retention import FORWARD_PARTITION_ALERT_DAYS, run_weather_retention_purge
from .sync_recovery import terminal_worker, weather_sync_recovery_sensor

logger = logging.getLogger(__name__)

KMA_ROTATION_SLOT_TAG = "kortravelweather/kma_rotation_slot"


def _start_metrics_server_from_env() -> None:
    """Expose worker metrics on the internal compose network when enabled."""
    raw_port = os.getenv("KOR_TRAVEL_WEATHER_METRICS_PORT", "").strip()
    if not raw_port:
        return
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError("KOR_TRAVEL_WEATHER_METRICS_PORT가 정수가 아닙니다.") from exc
    if not start_metrics_server(port):
        logger.warning(
            "Dagster metrics listener port %s is already bound; "
            "worker samples use PROMETHEUS_MULTIPROC_DIR aggregation",
            port,
        )


_start_metrics_server_from_env()


def _is_kma_target_location(location: object) -> bool:
    """Keep measurement-station anchors out of KMA's explicit target set.

    The station catalog (AirKorea, KRForest, KREX, KHOA) is intentionally the
    anchor source for external providers.  Treating every station as a KMA
    target would turn a 1,400-anchor catalog into well over a thousand KMA grid
    calls and make the KMA budget fail.  An administrator can opt a shared
    anchor into KMA explicitly with ``metadata.kma_opt_in=true``; the rest are
    sampled into whatever grid budget the explicit targets leave over (see
    ``_station_grid_fill``).
    """
    metadata = getattr(location, "metadata", None)
    if not isinstance(metadata, Mapping):
        return True
    return not (
        isinstance(metadata.get("measurement_point"), Mapping)
        and metadata.get("kma_opt_in") is not True
    )


def _load_all_kma_locations(repository: WeatherRepository) -> list[object]:
    db_locations = []
    catalog_offset = 0
    catalog_page_size = 5000
    while True:
        page = repository.list_locations(
            enabled_only=False, limit=catalog_page_size, offset=catalog_offset
        )
        db_locations.extend(page)
        if len(page) < catalog_page_size:
            break
        catalog_offset += len(page)
    return db_locations


def _provider_codes(metadata: dict[str, object]) -> dict[str, object]:
    """Read canonical and pre-ADR alias keys from persisted anchors."""
    legacy = metadata.get("mid_region_code")
    land = metadata.get("mid_land_region_code") or metadata.get("mid_land_reg_id") or legacy
    temperature = (
        metadata.get("mid_temperature_region_code") or metadata.get("mid_ta_reg_id") or legacy
    )
    return {
        "mid_region_code": legacy,
        "mid_land_region_code": land,
        "mid_temperature_region_code": temperature,
    }


def _merge_env_targets(
    rows: dict[str, dict[str, object]],
    settings: WeatherSettings,
    disabled_ids: set[str],
) -> dict[str, dict[str, object]]:
    """Layer env-configured targets over the DB catalog snapshot.

    DB anchor coordinates/lifecycle are canonical; env rows may only add
    provider-specific fields (mid region codes) to an existing row, or
    bootstrap a brand new one.
    """
    merged = dict(rows)
    for row in settings.targets:
        location_id = row.get("location_id")
        if not isinstance(location_id, str) or location_id in disabled_ids:
            continue
        if location_id in merged:
            for key in (
                "mid_region_code",
                "mid_land_region_code",
                "mid_temperature_region_code",
                "mid_land_reg_id",
                "mid_ta_reg_id",
            ):
                if row.get(key):
                    merged[location_id][key] = row[key]
        elif row.get("enabled", True):
            merged[location_id] = dict(row)
    return merged


def _kma_grid_targets(
    repository: WeatherRepository,
    settings: WeatherSettings,
    *,
    station_fill: bool,
    rotation_slot: int = 0,
) -> tuple[list[WeatherTarget], dict[str, dict[str, object]], set[str], "StationFill | None"]:
    """Build the validated target set every base-grid/mid-forecast KMA job shares.

    Admin-managed enabled locations opted into KMA (see
    ``_is_kma_target_location``) are the canonical source; env targets are an
    additive/override layer for bootstrap and provider-specific fields.  With
    ``station_fill`` (the base-grid jobs only -- mid and alerts read nothing a
    station grid adds) part of the grid budget they leave unused is filled from
    the station catalog (``_station_grid_fill``), so a catalog with no explicit
    target still collects KMA instead of failing every run.
    """
    db_locations = _load_all_kma_locations(repository)
    disabled_ids = {location.location_id for location in db_locations if not location.enabled}
    db_targets = {
        location.location_id: {**location.model_dump(), **_provider_codes(location.metadata)}
        for location in db_locations
        if location.enabled and _is_kma_target_location(location)
    }
    merged = _merge_env_targets(db_targets, settings, disabled_ids)
    targets = targets_from_settings(
        merged.values(),
        extra_points=settings.extra_points,
        disabled_location_ids=disabled_ids,
    )
    fill = None
    if station_fill:
        fill = _station_grid_fill(
            db_locations,
            targets,
            max_grids=settings.max_grids_per_run,
            fill_max_grids=settings.kma_station_fill_max_grids,
            disabled_ids=disabled_ids,
            rotation_slot=rotation_slot,
        )
        targets.extend(fill.targets)
    return targets, merged, disabled_ids, fill


@dataclass(frozen=True, slots=True)
class StationFill:
    """What ``_station_grid_fill`` took, for the run log's coverage line."""

    targets: list[WeatherTarget]
    #: Grids taken from the fill budget.
    grids_filled: int
    #: Explicit-target grids that also carry stations; they cost no budget.
    grids_shared: int
    #: Enabled station anchors, and the grids they sit on.
    stations_total: int
    grids_total: int
    rotation_slot: int = 0
    rotation_runs: int | None = 1

    def coverage_line(self) -> str:
        return (
            f"station fill: {len(self.targets)} of {self.stations_total} stations "
            f"on {self.grids_filled + self.grids_shared} of {self.grids_total} grids "
            f"({self.grids_shared} shared with explicit targets)"
        )


def _station_grid_fill(
    db_locations: list[object],
    targets: list[WeatherTarget],
    *,
    max_grids: int,
    fill_max_grids: int,
    disabled_ids: set[str],
    rotation_slot: int = 0,
) -> StationFill:
    """Spend a bounded grid budget on station anchors no explicit target covers.

    Explicit KMA targets (non-station anchors, opted-in stations, env
    ``TARGETS``/``EXTRA_POINTS``) used to be the only source.  A fresh catalog
    has none of them -- only the station anchors the AirKorea and regional
    syncs create -- so every KMA grid job failed on every run with an empty
    target set.  The station catalog already covers the country, so a sample
    of it is collected instead.

    The sample has its own budget, ``fill_max_grids``
    (``KMA_STATION_FILL_MAX_GRIDS``), because grids are what cost a KMA call
    and the data.go.kr key is shared: ``max_grids_per_run`` is a ceiling, not a
    spend target.  The fill takes at most ``fill_max_grids`` grids and never
    more than the explicit targets leave under ``max_grids_per_run``.

    Grids are ranked by their own KMA grid-cell centre with the same
    limit-independent ``spatially_even_subset`` order the external providers'
    caps use.  Ranking the grid rather than a station on it keeps the choice
    stable when stations come and go: AirKorea re-keys a station on any
    address edit.  Every station on a chosen grid rides along, since one
    response already fans out to all anchors on its grid -- and so does every
    station on a grid an explicit target already requests, at no budget cost.
    """
    covered = {(target.location.nx, target.location.ny) for target in targets}
    taken = {target.location.location_id for target in targets}
    stations = targets_from_settings(
        (
            location.model_dump()
            for location in db_locations
            if location.enabled
            and not _is_kma_target_location(location)
            and location.location_id not in taken
        ),
        disabled_location_ids=disabled_ids,
    )
    # Stations on a grid an explicit target already requests ride along on
    # that response for free: it is fetched anyway, and the fan-out reaches
    # every anchor on the grid.  Dropping them -- the first version did --
    # left a station without KMA whenever an explicit target shared its cell.
    # Only the uncovered grids compete for the fill budget.
    on_covered: list[WeatherTarget] = []
    by_grid: dict[tuple[int, int], list[WeatherTarget]] = {}
    for station in sorted(stations, key=lambda item: item.location.location_id):
        grid = (station.location.nx, station.location.ny)
        if grid in covered:
            on_covered.append(station)
        else:
            by_grid.setdefault(grid, []).append(station)
    covered_station_grids = len({(s.location.nx, s.location.ny) for s in on_covered})
    budget = min(fill_max_grids, max_grids - len(covered))
    chosen: list[ProviderLocation] = []
    cells: dict[str, tuple[tuple[int, int], ProviderLocation]] = {}
    if budget > 0 and by_grid:
        from kma import to_latlon

        for nx, ny in by_grid:
            latitude, longitude = to_latlon(nx, ny)
            cells[f"grid-{nx}-{ny}"] = (nx, ny), ProviderLocation(
                location_id=f"grid-{nx}-{ny}", latitude=latitude, longitude=longitude
            )
        # 같은 앞부분만 영구 수집하지 않는다. 실행에 고정한 슬롯으로 전체
        # 공간 분산 순서의 연속 구간을 방문하고 끝에서는 처음으로 돌아간다.
        ordered = spatially_even_subset([cell for _, cell in cells.values()], len(cells))
        take = min(budget, len(ordered))
        start = (rotation_slot * take) % len(ordered)
        chosen = [ordered[(start + index) % len(ordered)] for index in range(take)]
    filled = [
        *on_covered,
        *(station for picked in chosen for station in by_grid[cells[picked.location_id][0]]),
    ]
    return StationFill(
        filled,
        grids_filled=len(chosen),
        grids_shared=covered_station_grids,
        stations_total=len(stations),
        grids_total=len(by_grid) + covered_station_grids,
        rotation_slot=rotation_slot,
        rotation_runs=((len(by_grid) + budget - 1) // budget if budget > 0
                       else None if by_grid else 0),
    )


def _kma_rotation_slot(context: AssetExecutionContext, dataset_key: str) -> int:
    """대기·재실행이 시간 경계를 넘어도 최초 run 생성 시각의 구간을 유지한다."""
    if context.run_id == "EPHEMERAL":
        # build_asset_context의 직접 호출에는 저장된 Dagster run이 없다.
        created = kst_now()
    else:
        run = context.run
        record = context.instance.get_run_record_by_id(run.root_run_id or run.run_id)
        if record is None:
            raise RuntimeError("KMA 순환 기준 run을 찾지 못했습니다.")
        # 관리자 재수집도 동일 cohort를 지정할 수 있다. 재시도에서는 root의
        # tag만 읽어 후속 run이 다른 구간으로 바뀌지 않도록 한다.
        override = record.dagster_run.tags.get(KMA_ROTATION_SLOT_TAG)
        if override is not None:
            if not (override.isascii() and override.isdigit() and len(override) <= 12):
                raise ValueError("KMA 순환 slot tag는 12자리 이하 비음수 정수여야 합니다.")
            return int(override)
        created = record.create_timestamp
    if not isinstance(created, datetime) or created.tzinfo is None:
        raise ValueError("KMA 순환 기준 시각은 timezone-aware datetime이어야 합니다.")
    seconds = 3 * 3600 if dataset_key == KMA_SHORT_FORECAST else 3600
    return int(created.timestamp() // seconds)


def _kma_alert_targets(
    repository: WeatherRepository, settings: WeatherSettings, disabled_ids: set[str]
) -> list[object]:
    """Every enabled location, KMA-opted-in or not.

    Measurement anchors are excluded from base-grid fetches to keep the grid
    budget bounded, but KMA advisories must still be visible on their map
    markers, so the alert target snapshot is not filtered by
    ``_is_kma_target_location``.
    """
    db_locations = _load_all_kma_locations(repository)
    alert_rows = {
        location.location_id: {**location.model_dump(), **_provider_codes(location.metadata)}
        for location in db_locations
        if location.enabled
    }
    merged = _merge_env_targets(alert_rows, settings, disabled_ids)
    return targets_from_settings(merged.values(), disabled_location_ids=disabled_ids)


def _make_kma_dataset_asset(
    *,
    name: str,
    dataset_key: str,
    description: str,
    base_datasets: frozenset[str] | None,
    include_base: bool,
    include_mid: bool,
    include_alerts: bool,
    needs_data_client: bool,
):
    """Build one independently-scheduled asset for a single KMA dataset.

    KMA's five datasets (초단기실황/초단기예보/단기예보/중기예보/특보) each publish on a
    different real cadence and used to share one bundled asset and one
    ``weather_sync_runs`` row -- a single dataset's failure or backlog was
    invisible next to four others succeeding, and every run paid for all five
    even when only one had new data available.  One asset per dataset keeps
    each schedule, quota accounting and sync-run status honest about what it
    actually fetched.
    """

    @asset(
        name=name,
        required_resource_keys={"kma_client", "weather_repository"},
        description=description,
    )
    @skips_live_overlap
    def _kma_dataset_sync(context: AssetExecutionContext) -> dict[str, object]:
        settings = WeatherSettings()
        client_resource = context.resources.kma_client
        repository = _run_repository(context)
        targets, merged, disabled_ids, fill = _kma_grid_targets(
            repository, settings, station_fill=include_base,
            rotation_slot=_kma_rotation_slot(context, dataset_key) if include_base else 0,
        )
        if fill is not None:
            context.log.info(fill.coverage_line())
            if fill.rotation_runs is None:
                context.log.info("station rotation disabled: no grid budget for remaining stations")
            else:
                context.log.info(
                    "station rotation: slot %s, full sweep needs %s consecutive successful slots",
                    fill.rotation_slot, fill.rotation_runs,
                )
        if include_mid and not any(target.has_mid for target in targets):
            # Mid-forecast regions are configuration (env TARGETS or admin
            # metadata); stations carry none.  An unconfigured source is
            # switched off, not broken -- the same rule as a keyless external
            # provider -- so this is a recorded skip rather than a red run.
            skipped = {
                "provider": "python-kma-api",
                "dataset_key": dataset_key,
                "skipped": True,
                "reason": (
                    "중기예보 지역 코드(mid_land_region_code/"
                    "mid_temperature_region_code)가 설정된 target이 없습니다."
                ),
                "locations_total": len(targets),
            }
            context.add_output_metadata(skipped)
            return skipped
        alert_targets = (
            _kma_alert_targets(repository, settings, disabled_ids) if include_alerts else None
        )
        run = None
        sync_started = False
        try:
            run = repository.start_sync_run(
                provider="python-kma-api",
                dataset_key=dataset_key,
                locations_total=len(alert_targets) if alert_targets is not None else len(targets),
                **({"export_cohort_required": True} if (
                    settings.service_exports_enabled and settings.service_export_artifact_root
                    and include_mid and not include_base and not include_alerts
                ) else {}),
            )
            client = client_resource.create_client(settings=settings, repository=repository)
            data_client = (
                client_resource.create_data_client(settings=settings, repository=repository)
                if needs_data_client
                else None
            )
            sync_started = True
            result = run_weather_sync(
                repository=repository,
                client=client,
                targets=targets,
                max_grids=settings.max_grids_per_run,
                max_targets=settings.max_targets_per_run,
                max_response_rows=settings.max_response_rows_per_run,
                max_values=settings.max_values_per_run,
                include_base=include_base,
                base_datasets=base_datasets,
                include_mid=include_mid,
                include_alerts=include_alerts,
                alert_station_id=settings.kma_alert_station_id,
                alert_targets=alert_targets,
                data_client=data_client,
                # python-kma-api owns the transport retry boundary through the
                # resource above.  Do not retry the same client call a second
                # time here; otherwise one configured retry can multiply
                # network attempts per endpoint.
                retries=0,
                sync_run=run,
                **(
                    {"export_artifact_root": settings.service_export_artifact_root}
                    if (
                        settings.service_exports_enabled and settings.service_export_artifact_root
                        and include_base != include_mid
                        and not include_alerts
                    )
                    else {}
                ),
            )
            if result.get("values_skipped"):
                # KMA reports a station without an observation as Missing
                # sentinels (|v| >= 900); those metrics are skipped, not failed.
                context.log.warning(
                    f"KMA 값 {result['values_skipped']}건을 건너뜀(Missing 센티널/범위 밖): "
                    f"{result['values_skipped_by_reason']}"
                )
            if result.get("alert_locations_skipped"):
                # Also in the run's own log: the module logger reaches only
                # the compute log's stderr.
                context.log.warning(
                    f"특보 location {result['alert_locations_skipped']}곳을 lock 경합으로 "
                    f"건너뜀(다음 tick에 재시도): {result['alert_locations_skipped_sample']}"
                )
            if result.get("alert_locations_starved"):
                context.log.warning(
                    f"특보 location {len(result['alert_locations_starved'])}곳이 연속 "
                    f"{ALERT_STARVED_RUNS}회 건너뛰어짐(lock starvation): "
                    f"{result['alert_locations_starved'][:ALERT_SKIP_LOG_IDS]}"
                )
            context.add_output_metadata(result)
            return result
        except SyncRunAlreadyActive:
            # Nothing was opened, so nothing to finish; see overlap.py.
            raise
        except Exception:
            if run is None:
                # Target parsing failed before a normal run could be opened.
                # Keep the setup failure visible in the sync-run catalog too.
                run = repository.start_sync_run(
                    provider="python-kma-api",
                    dataset_key=dataset_key,
                    locations_total=len(merged),
                )
            if not sync_started:
                repository.finish_sync_run(run.run_id, status="failed", error="asset setup failed")
            raise
        # No `finally: client.close()` here -- KmaClient/DataGoKrClient are
        # async-only now, and run_weather_sync closes both itself, inside the
        # same asyncio.run() that used them (see kma_weather.py).

    return _kma_dataset_sync


kma_ultra_short_nowcast_sync = _make_kma_dataset_asset(
    name="kma_ultra_short_nowcast_sync",
    dataset_key=KMA_ULTRA_SHORT_NOWCAST,
    description="KMA 초단기실황(관측)을 매시 publish한다.",
    base_datasets=frozenset({KMA_ULTRA_SHORT_NOWCAST}),
    include_base=True,
    include_mid=False,
    include_alerts=False,
    needs_data_client=False,
)

kma_ultra_short_forecast_sync = _make_kma_dataset_asset(
    name="kma_ultra_short_forecast_sync",
    dataset_key=KMA_ULTRA_SHORT_FORECAST,
    description="KMA 초단기예보(6시간)를 매시 publish한다.",
    base_datasets=frozenset({KMA_ULTRA_SHORT_FORECAST}),
    include_base=True,
    include_mid=False,
    include_alerts=False,
    needs_data_client=False,
)

kma_short_forecast_sync = _make_kma_dataset_asset(
    name="kma_short_forecast_sync",
    dataset_key=KMA_SHORT_FORECAST,
    description=("KMA 단기예보(3일)를 발표시각(02/05/08/11/14/17/20/23시)마다 publish한다."),
    base_datasets=frozenset({KMA_SHORT_FORECAST}),
    include_base=True,
    include_mid=False,
    include_alerts=False,
    needs_data_client=False,
)

kma_mid_forecast_sync = _make_kma_dataset_asset(
    name="kma_mid_forecast_sync",
    dataset_key="kma_mid_forecast",
    description="KMA 중기예보(3~10일)를 발표시각(06/18시)마다 publish한다.",
    base_datasets=None,
    include_base=False,
    include_mid=True,
    include_alerts=False,
    needs_data_client=True,
)

kma_weather_alerts_sync = _make_kma_dataset_asset(
    name="kma_weather_alerts_sync",
    dataset_key="kma_weather_alerts",
    description="KMA 기상특보를 매시 publish한다.",
    base_datasets=None,
    include_base=False,
    include_mid=False,
    include_alerts=True,
    needs_data_client=True,
)


@asset(
    name="airkorea_weather_sync",
    required_resource_keys={"airkorea_client", "weather_repository"},
    description="AirKorea 측정소 catalog와 최신 대기질 관측을 hourly publish한다.",
)
@skips_live_overlap
def airkorea_weather_sync(context: AssetExecutionContext) -> dict[str, object]:
    runtime = WeatherSettings()
    repository = _run_repository(context)
    client = context.resources.airkorea_client.create_client(
        settings=runtime, repository=repository
    )
    # No `client.close()` here -- AirKoreaClient is async-only now, and
    # run_airkorea_weather_sync closes it itself, inside the same
    # asyncio.run() that used it.
    result = run_airkorea_weather_sync(
        repository=repository,
        client=client,
        max_stations=runtime.airkorea_max_stations,
        max_values=runtime.max_values_per_run,
        **(
            {"export_artifact_root": runtime.service_export_artifact_root}
            if runtime.service_exports_enabled and runtime.service_export_artifact_root else {}
        ),
    )
    context.add_output_metadata(result)
    return result


#: Providers that own a dedicated asset already.  Everything else in the
#: catalog is an external HTTP source and gets one generated below.
_DEDICATED_PROVIDER_KEYS = frozenset(
    {
        "python-kma-api",
        "python-airkorea-api",
        "python-khoa-api",
        "python-krforest-api",
        "python-krex-api",
    }
)

EXTERNAL_PROVIDER_KEYS = tuple(
    spec.key for spec in PROVIDER_CATALOG if spec.key not in _DEDICATED_PROVIDER_KEYS
)

#: Runs of the generated jobs carry this tag so ``tag_concurrency_limits`` in
#: deploy/dagster.yaml can cap how many providers fetch at the same time.
EXTERNAL_RUN_GROUP_TAG = "kortravelweather/run_group"
EXTERNAL_RUN_GROUP = "external_weather"
#: 같은 태그 키의 두 번째 값. 실제로 location sweep을 하는 provider만 여기에 둔다.
#: 한 그룹에 묶여 있으면 instance가 CPU 때문에 상한을 1로 낮췄을 때, 기본 설정에서
#: 몇 초 만에 끝나는 run(credential 없음 skip 등)이 73~90분짜리 open_meteo 뒤에서
#: 한 시간 가까이 기다린다. 값을 나누면 instance는 slow를 1로, 나머지는 따로 제한한다.
EXTERNAL_SLOW_RUN_GROUP = "external_weather_slow"

#: 소속은 실행 시간 측정이 아니라 코드가 정하는 요청 수로 정한다. 한 run의 요청 수는
#: ``provider_location_caps`` 기본값 × catalog dataset 수(각 2)다.
#:
#: - ``open_meteo``: 380곳 × 2 = 760 요청.
#: - ``openweathermap``: 350곳 × 2 = 700 요청, 그리고 요청 사이 1.25초 간격
#:   (``provider_min_request_interval_seconds``)만으로 최소 약 14.6분.
#: - ``weatherapi``: 150곳 × 2 = 300 요청. 앞의 둘보다 작지만 같은 종류의 일(위치마다
#:   HTTP 호출과 DB publish)이라, fast 쪽에 두면 CPU가 포화된 호스트에서 sweep 둘이
#:   동시에 도는 것을 다시 허용하게 된다. 3시간 주기 안의 phase가 weatherapi +7분,
#:   openweathermap +27분, open_meteo +47분이라 weatherapi가 가장 먼저 시작하고
#:   (2026-10-21 관측 약 9분), 늦어지면 openweathermap이 그 뒤에서 기다린다. 세 sweep의
#:   관측 합(약 9 + 30 + 73~90분)은 3시간 주기 안이다.
#:
#: 나머지 provider는 기본 location 상한이 없다. 기본 설정에서 그들의 run이 빠른 것은
#: 꺼져 있거나(visual_crossing, wttr_in) credential이 없어 skip하기 때문이다. 하나를
#: 켜면(env 또는 admin UI의 DB credential — 코드 변경 없이 가능) catalog 전체(약
#: 1,400곳)를 sweep한다. 태그는 정의 시점에 정해지므로 런타임 credential로 분류할 수
#: 없다: **켜기 전에** 이 집합에 추가하는 변경이 먼저 배포돼야 한다(runbook
#: docs/runbooks/dagster-recovery.md). 위치 상한을 env로 풀 때도 같다.
#: tests/test_definitions.py가 기본 상한이 있는 provider는 모두 여기 있음을 고정한다.
SLOW_EXTERNAL_PROVIDER_KEYS = frozenset({"open_meteo", "openweathermap", "weatherapi"})
if not set(EXTERNAL_PROVIDER_KEYS) >= SLOW_EXTERNAL_PROVIDER_KEYS:
    raise RuntimeError(
        "SLOW_EXTERNAL_PROVIDER_KEYS에 외부 provider가 아닌 key가 있습니다: "
        f"{sorted(SLOW_EXTERNAL_PROVIDER_KEYS - set(EXTERNAL_PROVIDER_KEYS))}"
    )


def external_run_group(provider_key: str) -> str:
    """외부 provider job이 받을 ``EXTERNAL_RUN_GROUP_TAG`` 값."""
    if provider_key in SLOW_EXTERNAL_PROVIDER_KEYS:
        return EXTERNAL_SLOW_RUN_GROUP
    return EXTERNAL_RUN_GROUP


#: Dagster's per-run override of ``run_monitoring.max_runtime_seconds``.
RUN_MAX_RUNTIME_TAG = "dagster/max_runtime"
#: The wall-clock bound every run of this project has today, carried on the
#: runs themselves instead of only in deploy/dagster.yaml.  That file's
#: ``max_runtime_seconds`` is instance-wide: on a Dagster instance shared with
#: other projects there is one value for all of them, and it is not ours to
#: size -- the measured ~13h weatherapi sweeps that set 57600 (see the comment
#: there) would be cancelled by a bound chosen for someone else's jobs.  With
#: the tag, every weather run keeps exactly this bound under either instance.
#: tests/test_dagster_location_scope.py binds this to deploy/dagster.yaml.
RUN_MAX_RUNTIME_SECONDS = 57600


def _job_policy(job_name: str) -> RecoveryPolicy:
    # 외부 provider의 실측 13시간 sweep 상한은 유지하고 최신성 작업은 더 일찍 회수한다.
    seconds = (
        RUN_MAX_RUNTIME_SECONDS
        if job_name.startswith(tuple(_external_slug(key) for key in EXTERNAL_PROVIDER_KEYS))
        else 7200
    )
    if job_name == "regional_weather_job":
        seconds = 21600
    if job_name == "address_refresh_job":
        seconds = 120
    if job_name == "weather_short_forecast_retention_job":
        seconds = 300
    return RecoveryPolicy(seconds, idempotent=True, infrastructure_retries=1)


def _job_tags(job_name: str) -> dict[str, str]:
    return _job_policy(job_name).tags(project="weather", job_name=job_name)


def _weather_schedule(**kwargs):
    from kortravelweather.export_status_scopes import dataset_scopes, status_scopes

    from .export_schedule_status import schedule_with_export_status

    job_name = kwargs["job"].name
    kma_datasets = {
        "kma_ultra_short_nowcast_job": "kma_ultra_short_nowcast",
        "kma_ultra_short_forecast_job": "kma_ultra_short_forecast",
        "kma_short_forecast_job": "kma_short_forecast",
        "kma_mid_forecast_job": "kma_mid_forecast",
    }

    def sources(_context):
        settings = WeatherSettings()
        if not settings.service_exports_enabled:
            return ()
        enabled = set(settings.enabled_providers)
        if job_name in kma_datasets:
            return dataset_scopes("python-kma-api", {kma_datasets[job_name]}) if (
                "python-kma-api" in enabled
            ) else ()
        if job_name == "airkorea_weather_job":
            return status_scopes(enabled & {"python-airkorea-api"})
        if job_name == "regional_weather_job":
            return status_scopes(enabled & {
                "python-khoa-api", "python-krforest-api", "python-krex-api",
            })
        return status_scopes({
            provider for provider in enabled if external_job_name(provider) == job_name
        })

    def repository_factory(_context):
        settings = WeatherSettings()
        return WeatherRepository(settings.database_url, export_status_enabled=True)

    return schedule_with_export_status(
        project="weather", location_name="kortravelweather_dagster.definitions",
        source_keys=sources, repository_factory=repository_factory, **kwargs,
    )


def _run_repository(context) -> WeatherRepository:
    repository = context.resources.weather_repository.create_repository()
    repository.orchestrator_run_id = context.run_id
    reconcile = getattr(repository, "reconcile_interrupted_sync_runs", None)
    if callable(reconcile):
        reconcile(lambda run_id: terminal_worker(context.instance, run_id))
    return repository


def _external_slug(provider_key: str) -> str:
    return provider_key.replace("-", "_").replace(".", "_")


def external_asset_name(provider_key: str) -> str:
    return f"{_external_slug(provider_key)}_weather_sync"


def external_job_name(provider_key: str) -> str:
    return f"{_external_slug(provider_key)}_weather_job"


def _external_targets(repository: WeatherRepository) -> list[ProviderLocation]:
    """External APIs are intentionally anchored to the AirKorea measurement
    catalog.  This keeps hourly quota predictable and gives consumers a station
    identity/distance alongside every provider bundle.
    """
    return [
        ProviderLocation(
            location_id=location.location_id,
            latitude=location.latitude,
            longitude=location.longitude,
            metadata=location.metadata,
        )
        for location in repository.list_locations(enabled_only=True, limit=None)
        if isinstance(location.metadata.get("measurement_point"), dict)
    ]


def _fetch_deadline_seconds(runtime: WeatherSettings) -> float:
    """Wall-clock ceiling for a single provider call.

    Derived from the provider's own retry budget rather than configured
    separately, so raising the timeout or the retry count cannot silently
    push a legitimate call past its own deadline.
    """
    attempts = runtime.provider_retries + 1
    return runtime.provider_http_timeout_seconds * attempts + 60.0


def _make_external_provider_asset(provider_key: str):
    """Build the collection asset for one external provider.

    One asset per provider, rather than the single asset that looped over all
    of them: that shape meant the slowest source set the pace for every other,
    and a source that stopped responding stalled all nine at once.  A run that
    wedged this way held a concurrency slot until somebody noticed -- which
    took eighteen hours the last time, with every other schedule queued behind
    it.  Split, a wedged provider costs one slot and one source.
    """
    spec = provider_spec(provider_key)

    @asset(
        name=external_asset_name(provider_key),
        required_resource_keys={"weather_repository"},
        description=f"{spec.label}의 응답을 atomic publish한다.",
    )
    @skips_live_overlap
    def _external_provider_sync(context: AssetExecutionContext) -> dict[str, object]:
        runtime = WeatherSettings()
        skipped = skipped_when_disabled(provider_key, runtime)
        if skipped is not None:
            context.add_output_metadata(skipped)
            return skipped
        repository = _run_repository(context)
        catalog_targets = _external_targets(repository)
        cap = runtime.provider_location_caps.get(provider_key)
        targets = (
            spatially_even_subset(catalog_targets, cap) if cap is not None else catalog_targets
        )
        if cap is not None and len(catalog_targets) > len(targets):
            # Worth a line in the run log: the difference between "this provider
            # covers the country" and "this provider covers a 55-point sample of
            # it" is invisible in the published values themselves.
            context.log.info(
                "%s: free-tier 범위 제한으로 %s곳 중 %s곳만 수집합니다",
                provider_key,
                len(catalog_targets),
                len(targets),
            )
        try:
            provider = create_configured_provider(
                provider_key, settings=runtime, repository=repository
            )
        except Exception as exc:
            # A paid provider without a key is switched off, not broken, and a
            # schedule that goes red for it trains people to ignore red.
            if spec.auth_required and "credential" in str(exc).lower():
                missing = {
                    "provider": provider_key,
                    "skipped": True,
                    "reason": "credential이 설정되지 않았습니다.",
                    "locations_total": len(targets),
                }
                context.add_output_metadata(missing)
                return missing
            raise
        results: list[dict[str, object]] = []
        failed: list[dict[str, object]] = []
        skipped_datasets: list[dict[str, object]] = []
        abandoned = False
        try:
            for dataset in spec.datasets:
                try:
                    results.append(
                        run_external_weather_sync(
                            repository=repository,
                            provider=provider,
                            targets=targets,
                            dataset_key=dataset.key,
                            max_targets=runtime.max_targets_per_run,
                            max_response_rows=runtime.max_response_rows_per_run,
                            max_values=runtime.max_values_per_run,
                            max_payload_bytes=runtime.max_payload_bytes_per_run,
                            fetch_timeout_seconds=_fetch_deadline_seconds(runtime),
                            min_request_interval_seconds=(
                                runtime.provider_min_request_interval_seconds.get(provider_key, 0.0)
                            ),
                            **(
                                {"export_artifact_root": runtime.service_export_artifact_root}
                                if runtime.service_exports_enabled
                                and runtime.service_export_artifact_root
                                else {}
                            ),
                        )
                    )
                except SyncRunAlreadyActive as overlap:
                    skip = overlap_skip_result(overlap)
                    log_overlap_skip(context, skip)
                    skipped_datasets.append(skip)
                except DeadlineExceeded:
                    # timeout의 daemon thread는 아직 client를 사용 중일 수 있다.
                    # 다음 dataset/close와 경합시키지 않고 이 worker 실행을 끝낸다.
                    abandoned = True
                    raise
                except Exception as exc:
                    failed.append(
                        {
                            "provider": provider_key,
                            "dataset_key": dataset.key,
                            "error": f"{type(exc).__name__}: {redact_secrets(str(exc))[:500]}",
                        }
                    )
        finally:
            close = getattr(provider, "close", None)
            if callable(close) and not abandoned:
                close()
        if failed:
            status = "partial"
        elif skipped_datasets and not results:
            status = "skipped"
        else:
            status = "success"
        result = {
            "provider": provider_key,
            "status": status,
            "datasets": results,
            "failed_datasets": failed,
            "skipped_datasets": skipped_datasets,
            "locations_total": len(targets),
            "locations_available": len(catalog_targets),
        }
        context.add_output_metadata(result)
        if failed and not results:
            # Nothing was collected. The old shared asset reported this as a
            # green "partial" because some *other* provider had succeeded;
            # alone, a provider that published nothing is simply a failure and
            # its own schedule is now the honest place to say so.
            raise RuntimeError(
                f"{provider_key}의 모든 dataset 수집이 실패했습니다: "
                f"{[entry['dataset_key'] for entry in failed]}"
            )
        return result

    return _external_provider_sync


_EXTERNAL_PROVIDER_ASSETS = tuple(
    _make_external_provider_asset(key) for key in EXTERNAL_PROVIDER_KEYS
)


@asset(
    name="khoa_beach_index_sync",
    required_resource_keys={"khoa_client", "weather_repository"},
    description="국립해양조사원 해수욕장 해양지수(파고·수온·기온·풍속)를 publish한다.",
)
@skips_live_overlap
def khoa_beach_index_sync(context: AssetExecutionContext) -> dict[str, object]:
    runtime = WeatherSettings()
    skipped = skipped_when_disabled(KHOA_PROVIDER, runtime)
    if skipped is not None:
        # Before the resource is built: constructing the client is
        # what demands the credential, and a provider switched off
        # on purpose must not need one.
        context.add_output_metadata(skipped)
        return skipped
    repository = _run_repository(context)
    result = run_khoa_beach_index_sync(
        repository=repository,
        api_key=context.resources.khoa_client.api_key(
            settings=runtime, repository=repository
        ),
        max_places=runtime.regional_max_records,
        max_values=runtime.max_values_per_run,
        retries=runtime.provider_retries,
        timeout=runtime.provider_http_timeout_seconds,
        settings=runtime,
    )
    if result.get("produced_nothing"):
        # Distinguishable from a healthy run only here: the counts
        # alone cannot tell an empty upstream from a broken adapter.
        context.log.warning(
            "%s fetched %s records and published nothing",
            result["provider"],
            result["records_fetched"],
        )
    context.add_output_metadata(result)
    return result


@asset(
    name="krforest_mountain_sync",
    required_resource_keys={"krforest_client", "weather_repository"},
    description="산림청 산악기상관측망 관측값을 publish한다.",
)
@skips_live_overlap
def krforest_mountain_sync(context: AssetExecutionContext) -> dict[str, object]:
    runtime = WeatherSettings()
    skipped = skipped_when_disabled(KRFOREST_PROVIDER, runtime)
    if skipped is not None:
        context.add_output_metadata(skipped)
        return skipped
    repository = _run_repository(context)
    result = run_krforest_mountain_sync(
        repository=repository,
        api_key=context.resources.krforest_client.api_key(
            settings=runtime, repository=repository
        ),
        max_records=runtime.regional_max_records,
        max_values=runtime.max_values_per_run,
        settings=runtime,
        timeout=runtime.provider_http_timeout_seconds,
    )
    context.add_output_metadata(result)
    return result


@asset(
    name="krforest_dust_sync",
    required_resource_keys={"krforest_client", "weather_repository"},
    description="산림 청정넷(AICAN) 미세먼지 PM10·PM2.5·PM1.0과 기상값을 publish한다.",
)
@skips_live_overlap
def krforest_dust_sync(context: AssetExecutionContext) -> dict[str, object]:
    runtime = WeatherSettings()
    skipped = skipped_when_disabled(KRFOREST_PROVIDER, runtime)
    if skipped is not None:
        context.add_output_metadata(skipped)
        return skipped
    repository = _run_repository(context)
    result = run_krforest_dust_sync(
        repository=repository,
        api_key=context.resources.krforest_client.api_key(
            settings=runtime, repository=repository
        ),
        hours=runtime.regional_dust_hours,
        max_records=runtime.regional_dust_max_records,
        max_values=runtime.max_values_per_run,
        timeout=runtime.provider_http_timeout_seconds,
        settings=runtime,
    )
    if result.get("skipped"):
        # Not a failure: the readings are fine and the catalog is a separate
        # data.go.kr approval, so nothing here can fix it this morning.
        context.log.warning("청정넷 수집을 건너뜁니다: %s", result.get("reason"))
    elif result.get("produced_nothing"):
        context.log.warning(
            "%s fetched %s readings and published nothing",
            result["provider"],
            result["records_fetched"],
        )
    context.add_output_metadata(result)
    return result


@asset(
    name="krex_restarea_sync",
    required_resource_keys={"krex_client", "weather_repository"},
    description="한국도로공사 고속도로 휴게소 기상 관측값을 publish한다.",
)
@skips_live_overlap
def krex_restarea_sync(context: AssetExecutionContext) -> dict[str, object]:
    runtime = WeatherSettings()
    skipped = skipped_when_disabled(KREX_PROVIDER, runtime)
    if skipped is not None:
        # Before the resource is built: constructing the client is
        # what demands the credential, and a provider switched off
        # on purpose must not need one.
        context.add_output_metadata(skipped)
        return skipped
    repository = _run_repository(context)
    client = context.resources.krex_client.create_client(settings=runtime, repository=repository)
    # No `client.close()` here -- KrexClient is async-only now, and
    # run_krex_restarea_sync closes it itself, inside the same asyncio.run()
    # that used it.
    try:
        result = run_krex_restarea_sync(
            repository=repository,
            client=client,
            max_records=runtime.regional_max_records,
            max_values=runtime.max_values_per_run,
            settings=runtime,
        )
    except UpstreamBlocked as blocked:
        # The step fails -- nothing was collected, and a block can last days
        # -- but with failure_kind in its error and metadata rather than a
        # bare HTML page.  The other regional assets are separate steps and
        # are not affected.
        context.log.error(str(blocked))
        raise Failure(description=str(blocked), metadata=blocked.metadata) from blocked
    if result.get("produced_nothing"):
        # Distinguishable from a healthy run only here: the counts
        # alone cannot tell an empty upstream from a broken adapter.
        context.log.warning(
            "%s fetched %s records and published nothing",
            result["provider"],
            result["records_fetched"],
        )
    context.add_output_metadata(result)
    return result


#: Days a retention run missed but could not mark on their table (the
#: ``COMMENT`` kept losing its lock race).  Kept in the run's own Dagster
#: record -- an asset observation, which needs no table lock and no migration
#: and is written even when the run then fails -- and handed to the next run.
_UNMARKED_KEY = "partitions_unmarked"


def _previous_unmarked(context: AssetExecutionContext) -> tuple[str, ...]:
    """The previous retention run's ``partitions_unmarked``, from its observation."""
    records = context.instance.fetch_observations(context.asset_key, limit=1).records
    observation = records[0].asset_observation if records else None
    value = observation.metadata.get(_UNMARKED_KEY) if observation else None
    return tuple(str(name) for name in (getattr(value, "value", None) or ()))


@asset(
    name="weather_retention_purge",
    required_resource_keys={"weather_repository"},
    description="보존 기간이 지난 fact/source 이력을 매일 정리한다.",
)
def weather_retention_purge(context: AssetExecutionContext) -> dict[str, object]:
    runtime = WeatherSettings()
    repository = _run_repository(context)
    result = run_weather_retention_purge(
        repository=repository,
        retention_days=runtime.retention_days,
        ahead_days=runtime.retention_ahead_days,
        prior_unmarked=_previous_unmarked(context),
    )
    # Recorded before any Failure below: a failed run materializes nothing,
    # and its lost marks must still reach the next run.
    context.log_event(
        AssetObservation(
            asset_key=context.asset_key,
            metadata={
                _UNMARKED_KEY: MetadataValue.json(list(result[_UNMARKED_KEY])),
            },
        )
    )
    if result["default_floor"] is None:
        # No forward partition could be created: DEFAULT holds rows and has no
        # floor, and proving one is a full scan this job does not run.
        context.log.warning(
            "DEFAULT partition has rows and no known_at floor; no forward "
            "partition was created. Run scripts/weather_values_forward_partitions.py"
        )
    elif result["rows_outside_any_partition"]:
        # With a floor every DEFAULT row is dated before it: a backfill into a
        # day nobody partitioned, or what predates the floor.  Expected, not an
        # anomaly -- but retention never drops them; the purge script does.
        context.log.info(
            "%s rows (counted up to the cap) are in DEFAULT, all before the floor; "
            "scripts/weather_values_purge_default.py removes them",
            result["rows_outside_any_partition"],
        )
    days = result["forward_partition_days"]
    if days is None or days <= FORWARD_PARTITION_ALERT_DAYS:
        context.log.warning(
            "only %s day(s) of forward partitions remain; facts dated past them are refused",
            days,
        )
    if result["partitions_deferred"] or result["partitions_not_created"]:
        # Lost lock races against ingestion, which never pauses: retried by
        # the next run.  One night of it is normal; see ``retention_status``.
        context.log.warning(
            "retention %s: deferred to the next run -- not dropped %s (overdue %s), "
            "not created %s",
            result["status"],
            result["partitions_deferred"],
            result["partitions_overdue"],
            result["partitions_not_created"],
        )
    if result["status"] == "failed":
        raise Failure(
            description=(
                "retention made no progress on work already missed on an earlier run: "
                f"overdue {result['partitions_overdue']}, "
                f"not created {result['partitions_not_created']} "
                f"(forward window {days} day(s))"
            ),
            metadata=result,
        )
    context.add_output_metadata(result)
    return result


@asset(
    name="weather_short_forecast_retention",
    required_resource_keys={"weather_repository"},
    description="단기예보의 지난 대상시각6시간 밖을 유한 batch로 정리한다.",
)
def weather_short_forecast_retention(context: AssetExecutionContext) -> dict[str, object]:
    repository = context.resources.weather_repository.create_repository()

    def log_progress(progress: dict[str, object]) -> None:
        if progress["phase"] == "BATCH_COMMITTED":
            # Python INFO 로그가 차단되어도 다음 SQL 예외 전에 확정 숫자를 영속화한다.
            context.log_event(AssetObservation(
                asset_key=context.asset_key,
                metadata={
                    key: progress[key] for key in (
                        "phase", "operation_id", "batches", "matched_in_batches",
                        "facts_deleted", "pointers_deleted", "remaining", "elapsed_seconds",
                    )
                },
            ))
            context.log.info(
                "단기예보 정리 BATCH_COMMITTED operation_id=%s batches=%s "
                "facts_deleted=%s pointers_deleted=%s remaining=%s elapsed_seconds=%s",
                progress["operation_id"], progress["batches"], progress["facts_deleted"],
                progress["pointers_deleted"], progress["remaining"], progress["elapsed_seconds"],
            )

    try:
        result = run_short_forecast_retention(
            repository, cutoff=short_forecast_cutoff(kst_now()), dry_run=False,
            on_progress=log_progress,
        )
    finally:
        repository.engine.dispose()
    context.add_output_metadata(result)
    if result["status"] != "complete_snapshot":
        # raise 뒤 STEP_OUTPUT이 없어도 Failure 이벤트 자체에 미완료 이유를 남긴다.
        raise Failure(
            description="단기예보 정리가 유한 예산 안에 완료되지 않았습니다.",
            metadata=result,
        )
    return result


#: 모든 작업이 같은 asset graph를 사용하도록 자산을 한 번만 선언한다.
_ASSETS = [
    kma_ultra_short_nowcast_sync,
    kma_ultra_short_forecast_sync,
    kma_short_forecast_sync,
    kma_mid_forecast_sync,
    kma_weather_alerts_sync,
    airkorea_weather_sync,
    *_EXTERNAL_PROVIDER_ASSETS,
    khoa_beach_index_sync,
    krforest_mountain_sync,
    krforest_dust_sync,
    krex_restarea_sync,
    weather_retention_purge,
    weather_short_forecast_retention,
]

_unresolved_kma_ultra_short_nowcast_job = define_asset_job(
    "kma_ultra_short_nowcast_job",
    selection=[kma_ultra_short_nowcast_sync],
    tags=_job_tags("kma_ultra_short_nowcast_job"),
)
_unresolved_kma_ultra_short_forecast_job = define_asset_job(
    "kma_ultra_short_forecast_job",
    selection=[kma_ultra_short_forecast_sync],
    tags=_job_tags("kma_ultra_short_forecast_job"),
)
_unresolved_kma_short_forecast_job = define_asset_job(
    "kma_short_forecast_job",
    selection=[kma_short_forecast_sync],
    tags=_job_tags("kma_short_forecast_job"),
)
_unresolved_kma_mid_forecast_job = define_asset_job(
    "kma_mid_forecast_job",
    selection=[kma_mid_forecast_sync],
    tags=_job_tags("kma_mid_forecast_job"),
)
_unresolved_kma_weather_alerts_job = define_asset_job(
    "kma_weather_alerts_job",
    selection=[kma_weather_alerts_sync],
    tags=_job_tags("kma_weather_alerts_job"),
)
_unresolved_airkorea_job = define_asset_job(
    "airkorea_weather_job",
    selection=[airkorea_weather_sync],
    tags=_job_tags("airkorea_weather_job"),
)
_unresolved_external_jobs = {
    key: define_asset_job(
        external_job_name(key),
        selection=[asset_def],
        tags={**_job_tags(external_job_name(key)), EXTERNAL_RUN_GROUP_TAG: external_run_group(key)},
    )
    for key, asset_def in zip(EXTERNAL_PROVIDER_KEYS, _EXTERNAL_PROVIDER_ASSETS, strict=True)
}
_unresolved_retention_job = define_asset_job(
    "weather_retention_job",
    selection=[weather_retention_purge],
    tags=_job_tags("weather_retention_job"),
)
_unresolved_short_retention_job = define_asset_job(
    "weather_short_forecast_retention_job",
    selection=[weather_short_forecast_retention],
    tags=_job_tags("weather_short_forecast_retention_job"),
)
_unresolved_regional_job = define_asset_job(
    "regional_weather_job",
    selection=[
        khoa_beach_index_sync,
        krforest_mountain_sync,
        krforest_dust_sync,
        krex_restarea_sync,
    ],
    tags=_job_tags("regional_weather_job"),
)

# Resolve the asset job before exposing it from ``Definitions``.  Passing an
# ``UnresolvedAssetJobDefinition`` directly emits a deprecation warning today
# and becomes an error in newer Dagster releases.
_resources = {
    "kma_client": KmaClientResource(),
    "khoa_client": KhoaResource(),
    "krex_client": KrexResource(),
    "krforest_client": KrforestResource(),
    "weather_repository": WeatherRepositoryResource(),
    "airkorea_client": AirKoreaResource(),
    "external_weather": ExternalWeatherProviderResource(),
}


_BOUNDED_MULTIPROCESS_EXECUTOR = multiprocess_executor.configured({"max_concurrent": 1})


@cache
def _job_resolution_context():
    """프로세스마다 전체 graph와 resource 정의를 한 번만 구성한다."""
    asset_defs = Definitions(
        assets=_ASSETS,
        resources=_resources,
        executor=_BOUNDED_MULTIPROCESS_EXECUTOR,
    )
    return (
        asset_defs.resolve_asset_graph(),
        asset_defs.get_repository_def().get_top_level_resources(),
    )


def _resolve_job(unresolved, *, executor_def=in_process_executor):
    """같은 graph에서 작업을 resolve하며 별도의 module Definitions는 노출하지 않는다."""
    asset_graph, resource_defs = _job_resolution_context()
    return unresolved.resolve(
        asset_graph,
        default_executor_def=executor_def,
        resource_defs=resource_defs,
    )


kma_ultra_short_nowcast_job = _resolve_job(_unresolved_kma_ultra_short_nowcast_job)
kma_ultra_short_forecast_job = _resolve_job(_unresolved_kma_ultra_short_forecast_job)
kma_short_forecast_job = _resolve_job(_unresolved_kma_short_forecast_job)
kma_mid_forecast_job = _resolve_job(_unresolved_kma_mid_forecast_job)
kma_weather_alerts_job = _resolve_job(_unresolved_kma_weather_alerts_job)
airkorea_job = _resolve_job(_unresolved_airkorea_job)
external_weather_jobs = {
    key: _resolve_job(unresolved) for key, unresolved in _unresolved_external_jobs.items()
}
weather_retention_job = _resolve_job(_unresolved_retention_job)
weather_short_forecast_retention_job = _resolve_job(_unresolved_short_retention_job)
regional_weather_job = _resolve_job(
    _unresolved_regional_job, executor_def=_BOUNDED_MULTIPROCESS_EXECUTOR
)

# The three hourly jobs that publish into the AirKorea station anchors run one
# after another, not together: 초단기예보 at :00, 초단기실황 at :20, AirKorea at
# :35.  They take the same ``location:<id>`` advisory locks, and the KMA two
# also walk the same targets in the same order -- started on one tick, the
# second ran a few seconds behind the first and lost a lock wait on the same
# station.  Shared Dagster 2026-10-08..12 (96 runs each): all 13 nowcast
# failures were that ``LockNotAvailable`` with the ultra-short forecast still
# running, and both AirKorea lock failures were inside its tail.  The owner
# chose on 2026-10-06 not to add per-location skip to more jobs, so the jobs
# are kept apart instead of taught to skip.
#
# Measured then (start to end, success): ultra-short forecast p50 6.4 /
# p95 13.9 / max 24.3 min, ended past :20 once in 95 runs; nowcast p50 2.5 /
# p95 7.1 / max 12.5 min; AirKorea p50 1.7 / p95 12.0 / max 18.4 min.
#
# :20 costs the nowcast no data.  python-kma-api serves 초단기실황 base HH:00
# from HH:40 (ULTRA_SRT_NCST_DELAY), so any tick in :00-:39 asks for the
# previous hour's observation -- the one the :00 tick fetched -- and it has
# been published for 40 minutes by then.  The run finishes (max 12.5 min)
# well before :40; if one ever ran past it, the client asks for the newer base
# only once the library considers it published.
hourly_kma_ultra_short_nowcast_schedule = _weather_schedule(
    name="hourly_kma_ultra_short_nowcast",
    cron_schedule="20 * * * *",
    job=kma_ultra_short_nowcast_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.RUNNING,
)

hourly_kma_ultra_short_forecast_schedule = _weather_schedule(
    name="hourly_kma_ultra_short_forecast",
    cron_schedule="0 * * * *",
    job=kma_ultra_short_forecast_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.RUNNING,
)

# python-kma-api's VILAGE_PUBLISH_HOURS: 단기예보 only actually publishes eight
# times a day (KST 02/05/08/11/14/17/20/23), each covering the next ~3 days.
# Fetching it hourly like the other two datasets bought nothing but 3x the
# quota spend -- 16 of every 24 hourly calls would have returned a response
# identical to the one already staged. :15 clears VILAGE_FCST_DELAY (10min).
kma_short_forecast_schedule = _weather_schedule(
    name="kma_short_forecast_publish_hours",
    cron_schedule="15 2,5,8,11,14,17,20,23 * * *",
    job=kma_short_forecast_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.RUNNING,
)

# python-kma-api's MID_FCST_PUBLISH_HOURS: 중기예보 publishes twice a day
# (KST 06/18). Until some target carries mid region codes (env TARGETS or
# admin metadata) the run records a skip with that reason rather than failing
# -- kept scheduled so configuring a region later needs no code change.
kma_mid_forecast_schedule = _weather_schedule(
    name="kma_mid_forecast_publish_hours",
    cron_schedule="30 6,18 * * *",
    job=kma_mid_forecast_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.RUNNING,
)

# Advisories can be issued at any time, so this stays hourly like the two
# ultra-short datasets; offset five minutes past the ultra-short forecast so
# the two do not start on the same tick.  It still overlaps that run's tail;
# its station publishes skip a held location (ingest_skip_locked) instead of
# waiting on it.
hourly_kma_weather_alerts_schedule = _weather_schedule(
    name="hourly_kma_weather_alerts",
    cron_schedule="5 * * * *",
    job=kma_weather_alerts_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.RUNNING,
)

# :35, after the nowcast (see the ultra-short schedules above).  It is also
# fresher than the old :10: AirKorea posts a station's HH:00 reading somewhere
# around HH:10-HH:13, and on 2026-10-11..12 a :10-:11 fetch got the previous
# hour for about half the sampled stations while a :13-:14 fetch got the
# current hour for all of them.  Of the :30 jobs, the mid forecast (06/18)
# took at most 1.4 min and the regional sweep (05/17) writes its own
# khoa-/krforest-/krex- anchors, not stations.
hourly_airkorea_weather_schedule = _weather_schedule(
    name="hourly_airkorea_weather",
    cron_schedule="35 * * * *",
    job=airkorea_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.RUNNING,
)

# Every three hours, not hourly. Free-tier quotas are a budget per month, and
# these providers are supplementary to KMA: spending that budget on eight sweeps
# a day instead of twenty-four is what lets each sweep still cover the country
# rather than a thin sample of it. Hourly is also what the throughput could not
# sustain -- a sweep that is throttled into taking hours cannot usefully be
# asked for again sixty minutes later.
#
# 동일 분에 아홉 작업의 시작 비용이 몰리지 않도록 3시간 주기 안에서 분산한다.
# provider 순서나 활성화 여부가 바뀌어도 다른 provider의 phase는 움직이지 않는다.
# 각 provider의 하루 8회 예약과 run-group 제한·중복 예약 병합은 유지한다.
_EXTERNAL_PHASE_MINUTES = {
    "weatherapi": 7,
    "openweathermap": 27,
    "open_meteo": 47,
    "visual_crossing": 67,
    "tomorrow_io": 87,
    "weatherbit": 107,
    "weatherstack": 127,
    "accuweather": 147,
    "wttr_in": 167,
}
external_weather_schedules = [
    _weather_schedule(
        name=f"three_hourly_{_external_slug(key)}_weather",
        cron_schedule=staggered_cron(
            interval_minutes=180, phase_minutes=_EXTERNAL_PHASE_MINUTES[key]
        ),
        job=job,
        execution_timezone="Asia/Seoul",
        default_status=DefaultScheduleStatus.RUNNING,
    )
    for key, job in external_weather_jobs.items()
]


# Twice a day, on the half hour so it never collides with the hourly
# ingests.  These three sources publish a few times a day at most; asking
# hourly would multiply an already 3M-fact-per-day pipeline for readings
# that have not changed.
regional_weather_schedule = _weather_schedule(
    name="twice_daily_regional_weather",
    cron_schedule="30 5,17 * * *",
    job=regional_weather_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.RUNNING,
)

# 01:45 KST는 기존 실측(2026-10-04..07)의 저부하 시각으로 유지한다.
# 외부 수집의 phase 분산 후에는 이 시간에도 실행이 시작될 수 있다.
# retention의 짧은 lock/재시도·다음 실행으로 연기하는 계약을 유지하고,
# 새 시간표의 실제 부하를 측정한 뒤 필요할 때 별도로 조정한다.
daily_weather_retention_schedule = _weather_schedule(
    name="daily_weather_retention",
    cron_schedule="45 1 * * *",
    job=weather_retention_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.RUNNING,
)


short_forecast_retention_schedule = _weather_schedule(
    name="short_forecast_retention_every_ten_minutes",
    cron_schedule="*/10 * * * *",
    job=weather_short_forecast_retention_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.RUNNING,
)


_COLLECTION_JOBS = [
    kma_ultra_short_nowcast_job,
    kma_ultra_short_forecast_job,
    kma_short_forecast_job,
    kma_mid_forecast_job,
    kma_weather_alerts_job,
    airkorea_job,
    *external_weather_jobs.values(),
    regional_weather_job,
    weather_retention_job,
    weather_short_forecast_retention_job,
]


defs = Definitions(
    assets=_ASSETS,
    jobs=[*_COLLECTION_JOBS, address_refresh_job],
    schedules=[
        hourly_kma_ultra_short_nowcast_schedule,
        hourly_kma_ultra_short_forecast_schedule,
        kma_short_forecast_schedule,
        kma_mid_forecast_schedule,
        hourly_kma_weather_alerts_schedule,
        hourly_airkorea_weather_schedule,
        *external_weather_schedules,
        regional_weather_schedule,
        daily_weather_retention_schedule,
        short_forecast_retention_schedule,
    ],
    resources=_resources,
    executor=_BOUNDED_MULTIPROCESS_EXECUTOR,
    sensors=[
        address_refresh_sensor,
        weather_sync_recovery_sensor,
        weather_export_publisher_sensor,
        *optional_recovery_sensors(
            {job.name: (job, _job_policy(job.name)) for job in _COLLECTION_JOBS}
        ),
    ],
)
