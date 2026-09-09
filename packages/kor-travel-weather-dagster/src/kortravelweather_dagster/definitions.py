"""Dagster Definitions and hourly KMA sync schedule."""

import logging
import os
from collections.abc import Mapping

from dagster import (
    AssetExecutionContext,
    DefaultScheduleStatus,
    Definitions,
    ScheduleDefinition,
    asset,
    define_asset_job,
)

from kortravelweather.metrics import start_metrics_server
from kortravelweather.providers import (
    PROVIDER_CATALOG,
    ProviderLocation,
    create_configured_provider,
    redact_secrets,
)
from kortravelweather.providers.khoa import KHOA_PROVIDER
from kortravelweather.providers.krex import KREX_PROVIDER
from kortravelweather.providers.krforest import KRFOREST_PROVIDER
from kortravelweather.settings import WeatherSettings

from .airkorea_weather import run_airkorea_weather_sync
from .external_weather import run_external_weather_sync
from .kma_weather import run_weather_sync, targets_from_settings
from .regional_sources import (
    run_khoa_beach_index_sync,
    run_krex_restarea_sync,
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
from .retention import run_weather_retention_purge

logger = logging.getLogger(__name__)


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
    """Keep AirKorea station anchors out of KMA's grid target set by default.

    AirKorea's nationwide station catalog is intentionally the anchor source
    for external providers.  Treating every station as a KMA target would turn
    a 300+ station catalog into hundreds of KMA grid calls and make the KMA
    budget fail.  An administrator can opt a shared anchor into KMA explicitly
    with ``metadata.kma_opt_in=true``.
    """
    metadata = getattr(location, "metadata", None)
    if not isinstance(metadata, Mapping):
        return True
    return not (
        isinstance(metadata.get("measurement_point"), Mapping)
        and metadata.get("kma_opt_in") is not True
    )


@asset(
    name="kma_weather_sync",
    required_resource_keys={"kma_client", "weather_repository"},
    description="KMA grids are staged first, then published as one immutable batch.",
)
def kma_weather_sync(context: AssetExecutionContext) -> dict[str, object]:
    settings = WeatherSettings()
    client_resource = context.resources.kma_client
    repository_resource = context.resources.weather_repository
    repository = repository_resource.create_repository()
    # Admin-managed enabled locations are the canonical target source. Env
    # targets are an additive/override layer for bootstrap and provider-specific
    # fields such as mid_land_region_code/mid_temperature_region_code;
    # disabled rows never enter a run.
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
    disabled_ids = {location.location_id for location in db_locations if not location.enabled}

    def provider_codes(metadata: dict[str, object]) -> dict[str, object]:
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

    db_targets = [
        {
            **location.model_dump(),
            **provider_codes(location.metadata),
        }
        for location in db_locations
        if location.enabled and _is_kma_target_location(location)
    ]
    merged: dict[str, dict[str, object]] = {row["location_id"]: row for row in db_targets}
    for row in settings.targets:
        location_id = row.get("location_id")
        if not isinstance(location_id, str) or location_id in disabled_ids:
            continue
        if location_id in merged:
            # DB anchor coordinates/lifecycle are canonical. Env may only add
            # provider-specific fields to an existing row.
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
    run = None
    sync_started = False
    client = None
    data_client = None
    try:
        targets = targets_from_settings(
            merged.values(),
            extra_points=settings.extra_points,
            disabled_location_ids=disabled_ids,
        )
        # Measurement anchors are intentionally excluded from KMA base-grid
        # fetches to keep the grid budget bounded, but KMA advisories must still
        # be visible on their map markers. Build a separate alert-only target
        # snapshot from every enabled catalog row (plus enabled env targets).
        alert_rows: dict[str, dict[str, object]] = {
            location.location_id: {
                **location.model_dump(),
                **provider_codes(location.metadata),
            }
            for location in db_locations
            if location.enabled
        }
        for row in settings.targets:
            location_id = row.get("location_id")
            if not isinstance(location_id, str) or location_id in disabled_ids:
                continue
            if location_id in alert_rows:
                for key in (
                    "mid_region_code",
                    "mid_land_region_code",
                    "mid_temperature_region_code",
                    "mid_land_reg_id",
                    "mid_ta_reg_id",
                ):
                    if row.get(key):
                        alert_rows[location_id][key] = row[key]
            elif row.get("enabled", True):
                alert_rows[location_id] = dict(row)
        alert_targets = targets_from_settings(
            alert_rows.values(), disabled_location_ids=disabled_ids
        )
        # Count the validated target set, including generated extra points,
        # rather than the pre-validation catalog snapshot.
        run = repository.start_sync_run(
            provider="python-kma-api",
            dataset_key="kma_weather_bundle",
            locations_total=len(targets),
        )
        client = client_resource.create_client(settings=settings, repository=repository)
        data_client = client_resource.create_data_client(settings=settings, repository=repository)
        sync_started = True
        result = run_weather_sync(
            repository=repository,
            client=client,
            targets=targets,
            max_grids=settings.max_grids_per_run,
            max_targets=settings.max_targets_per_run,
            max_response_rows=settings.max_response_rows_per_run,
            max_values=settings.max_values_per_run,
            include_mid=any(target.has_mid for target in targets),
            include_alerts=True,
            alert_station_id=settings.kma_alert_station_id,
            alert_targets=alert_targets,
            data_client=data_client,
            # python-kma-api owns the transport retry boundary through the
            # resource above.  Do not retry the same client call a second time
            # here; otherwise one configured retry can multiply network
            # attempts per endpoint.
            retries=0,
            sync_run=run,
        )
        context.add_output_metadata(result)
        return result
    except Exception:
        if run is None:
            # Target parsing failed before a normal run could be opened. Keep
            # the setup failure visible in the sync-run catalog as well.
            run = repository.start_sync_run(
                provider="python-kma-api",
                dataset_key="kma_weather_bundle",
                locations_total=len(merged),
            )
        if not sync_started:
            repository.finish_sync_run(run.run_id, status="failed", error="asset setup failed")
        raise
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
        close_data = getattr(data_client, "close", None)
        if callable(close_data):
            close_data()


@asset(
    name="airkorea_weather_sync",
    required_resource_keys={"airkorea_client", "weather_repository"},
    description="AirKorea 측정소 catalog와 최신 대기질 관측을 hourly publish한다.",
)
def airkorea_weather_sync(context: AssetExecutionContext) -> dict[str, object]:
    runtime = WeatherSettings()
    repository = context.resources.weather_repository.create_repository()
    client = context.resources.airkorea_client.create_client(
        settings=runtime, repository=repository
    )
    try:
        result = run_airkorea_weather_sync(
            repository=repository,
            client=client,
            max_stations=runtime.airkorea_max_stations,
            max_values=runtime.max_values_per_run,
        )
        context.add_output_metadata(result)
        return result
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


@asset(
    name="external_weather_sync",
    required_resource_keys={"weather_repository"},
    description="provider-independent external weather response를 atomic publish한다.",
)
def external_weather_sync(context: AssetExecutionContext) -> dict[str, object]:
    runtime = WeatherSettings()
    repository = context.resources.weather_repository.create_repository()
    # External APIs are intentionally anchored to the AirKorea measurement
    # catalog.  This keeps hourly quota predictable and gives consumers a
    # station identity/distance alongside every provider bundle.
    locations = [
        location
        for location in repository.list_locations(enabled_only=True, limit=None)
        if isinstance(location.metadata.get("measurement_point"), dict)
    ]
    targets = [
        ProviderLocation(
            location_id=location.location_id,
            latitude=location.latitude,
            longitude=location.longitude,
            metadata=location.metadata,
        )
        for location in locations
    ]
    results: list[dict[str, object]] = []
    skipped: list[str] = []
    failed: list[dict[str, object]] = []
    external_keys = {
        key
        for key in runtime.enabled_providers
        if key not in {"python-kma-api", "python-airkorea-api"}
    }
    for spec in PROVIDER_CATALOG:
        if spec.key not in external_keys:
            continue
        try:
            provider = create_configured_provider(
                spec.key, settings=runtime, repository=repository
            )
        except Exception as exc:
            # Missing optional credentials are represented in the run output;
            # one unavailable provider must not prevent other sources from
            # refreshing on the same hourly tick.
            if spec.auth_required and "credential" in str(exc).lower():
                skipped.append(spec.key)
                continue
            failed.append(
                {
                    "provider": spec.key,
                    "dataset_key": None,
                    "error": f"{type(exc).__name__}: {redact_secrets(str(exc))[:500]}",
                }
            )
            continue
        try:
            for dataset in spec.datasets:
                try:
                    result = run_external_weather_sync(
                        repository=repository,
                        provider=provider,
                        targets=targets,
                        dataset_key=dataset.key,
                        max_targets=runtime.max_targets_per_run,
                        max_response_rows=runtime.max_response_rows_per_run,
                        max_values=runtime.max_values_per_run,
                        max_payload_bytes=runtime.max_payload_bytes_per_run,
                    )
                except Exception as exc:
                    failed.append(
                        {
                            "provider": spec.key,
                            "dataset_key": dataset.key,
                            "error": f"{type(exc).__name__}: {redact_secrets(str(exc))[:500]}",
                        }
                    )
                    continue
                results.append(result)
        finally:
            close = getattr(provider, "close", None)
            if callable(close):
                close()
    result = {
        "status": "partial" if failed else "success",
        "providers": results,
        "skipped_providers": skipped,
        "failed_providers": failed,
        "locations_total": len(targets),
    }
    context.add_output_metadata(result)
    return result


@asset(
    name="khoa_beach_index_sync",
    required_resource_keys={"khoa_client", "weather_repository"},
    description="국립해양조사원 해수욕장 해양지수(파고·수온·기온·풍속)를 publish한다.",
)
def khoa_beach_index_sync(context: AssetExecutionContext) -> dict[str, object]:
    runtime = WeatherSettings()
    skipped = skipped_when_disabled(KHOA_PROVIDER, runtime)
    if skipped is not None:
        # Before the resource is built: constructing the client is
        # what demands the credential, and a provider switched off
        # on purpose must not need one.
        context.add_output_metadata(skipped)
        return skipped
    repository = context.resources.weather_repository.create_repository()
    client = context.resources.khoa_client.create_client(
        settings=runtime, repository=repository
    )
    try:
        result = run_khoa_beach_index_sync(
            repository=repository,
            client=client,
            max_places=runtime.regional_max_records,
            max_values=runtime.max_values_per_run,
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
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


@asset(
    name="krforest_mountain_sync",
    required_resource_keys={"krforest_client", "weather_repository"},
    description="산림청 산악기상관측망 관측값을 publish한다.",
)
def krforest_mountain_sync(context: AssetExecutionContext) -> dict[str, object]:
    runtime = WeatherSettings()
    skipped = skipped_when_disabled(KRFOREST_PROVIDER, runtime)
    if skipped is not None:
        context.add_output_metadata(skipped)
        return skipped
    repository = context.resources.weather_repository.create_repository()
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
    name="krex_restarea_sync",
    required_resource_keys={"krex_client", "weather_repository"},
    description="한국도로공사 고속도로 휴게소 기상 관측값을 publish한다.",
)
def krex_restarea_sync(context: AssetExecutionContext) -> dict[str, object]:
    runtime = WeatherSettings()
    skipped = skipped_when_disabled(KREX_PROVIDER, runtime)
    if skipped is not None:
        # Before the resource is built: constructing the client is
        # what demands the credential, and a provider switched off
        # on purpose must not need one.
        context.add_output_metadata(skipped)
        return skipped
    repository = context.resources.weather_repository.create_repository()
    client = context.resources.krex_client.create_client(
        settings=runtime, repository=repository
    )
    try:
        result = run_krex_restarea_sync(
            repository=repository,
            client=client,
            max_records=runtime.regional_max_records,
            max_values=runtime.max_values_per_run,
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
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


@asset(
    name="weather_retention_purge",
    required_resource_keys={"weather_repository"},
    description="보존 기간이 지난 fact/source 이력을 매일 정리한다.",
)
def weather_retention_purge(context: AssetExecutionContext) -> dict[str, object]:
    runtime = WeatherSettings()
    repository = context.resources.weather_repository.create_repository()
    result = run_weather_retention_purge(
        repository=repository,
        retention_days=runtime.retention_days,
        max_batches=runtime.retention_max_batches,
    )
    if result["truncated"]:
        # Deleted counts alone cannot tell "nothing was due" from "gave up with
        # a backlog", and only the second one means the table keeps growing.
        context.log.warning(
            "retention purge stopped at its batch cap with work remaining; "
            "the ingest rate may exceed what a %s-day window can hold",
            result["retention_days"],
        )
    context.add_output_metadata(result)
    return result


#: Every asset, named once.  Each ``_resolve_*`` below builds a throwaway
#: ``Definitions`` to resolve its job against, and each needs the *whole* graph.
#: When this was four hand-copied lists, adding an asset meant remembering all
#: four -- and a resolution site that missed one still worked, silently.
_ASSETS = [
    kma_weather_sync,
    airkorea_weather_sync,
    external_weather_sync,
    khoa_beach_index_sync,
    krforest_mountain_sync,
    krex_restarea_sync,
    weather_retention_purge,
]

_unresolved_weather_job = define_asset_job("kma_weather_job", selection=[kma_weather_sync])
_unresolved_airkorea_job = define_asset_job(
    "airkorea_weather_job", selection=[airkorea_weather_sync]
)
_unresolved_external_job = define_asset_job(
    "external_weather_job", selection=[external_weather_sync]
)
_unresolved_retention_job = define_asset_job(
    "weather_retention_job", selection=[weather_retention_purge]
)
_unresolved_regional_job = define_asset_job(
    "regional_weather_job",
    selection=[khoa_beach_index_sync, krforest_mountain_sync, krex_restarea_sync],
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


def _resolve_weather_job():
    """Resolve the asset job without exposing a second module-level Definitions."""
    asset_defs = Definitions(
        assets=_ASSETS,
        resources=_resources,
    )
    return _unresolved_weather_job.resolve(
        asset_defs.resolve_asset_graph(),
        resource_defs=asset_defs.get_repository_def().get_top_level_resources(),
    )


weather_job = _resolve_weather_job()


def _resolve_airkorea_job():
    asset_defs = Definitions(
        assets=_ASSETS,
        resources=_resources,
    )
    return _unresolved_airkorea_job.resolve(
        asset_defs.resolve_asset_graph(),
        resource_defs=asset_defs.get_repository_def().get_top_level_resources(),
    )


airkorea_job = _resolve_airkorea_job()


def _resolve_external_job():
    asset_defs = Definitions(
        assets=_ASSETS,
        resources=_resources,
    )
    return _unresolved_external_job.resolve(
        asset_defs.resolve_asset_graph(),
        resource_defs=asset_defs.get_repository_def().get_top_level_resources(),
    )


external_weather_job = _resolve_external_job()


def _resolve_retention_job():
    asset_defs = Definitions(
        assets=_ASSETS,
        resources=_resources,
    )
    return _unresolved_retention_job.resolve(
        asset_defs.resolve_asset_graph(),
        resource_defs=asset_defs.get_repository_def().get_top_level_resources(),
    )


weather_retention_job = _resolve_retention_job()


def _resolve_regional_job():
    asset_defs = Definitions(
        assets=_ASSETS,
        resources=_resources,
    )
    return _unresolved_regional_job.resolve(
        asset_defs.resolve_asset_graph(),
        resource_defs=asset_defs.get_repository_def().get_top_level_resources(),
    )


regional_weather_job = _resolve_regional_job()

# KMA and AirKorea ingest are off by default as of 2026-09-09.  The jobs stay
# defined so a backfill can still be launched deliberately; what is stopped is
# the clock, not the capability.
#
# `STOPPED` is the *default*, not a lock: a status flipped in the Dagster UI is
# stored in the instance and survives deploys.  If these ever need to stay off
# for good, the schedules have to stop being built at all — see the sibling
# `kor-travel-map` repository, which does exactly that.
hourly_kma_weather_schedule = ScheduleDefinition(
    name="hourly_kma_weather",
    cron_schedule="0 * * * *",
    job=weather_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.STOPPED,
)

hourly_airkorea_weather_schedule = ScheduleDefinition(
    name="hourly_airkorea_weather",
    cron_schedule="10 * * * *",
    job=airkorea_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.STOPPED,
)

hourly_external_weather_schedule = ScheduleDefinition(
    name="hourly_external_weather",
    cron_schedule="15 * * * *",
    job=external_weather_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.RUNNING,
)


# Twice a day, on the half hour so it never collides with the hourly
# ingests.  These three sources publish a few times a day at most; asking
# hourly would multiply an already 3M-fact-per-day pipeline for readings
# that have not changed.
regional_weather_schedule = ScheduleDefinition(
    name="twice_daily_regional_weather",
    cron_schedule="30 5,17 * * *",
    job=regional_weather_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.RUNNING,
)

# 03:20 KST: the ingest schedules fire at :00, :10 and :15 of every hour, and
# the purge holds row locks on what it deletes, so it must not land on one.
daily_weather_retention_schedule = ScheduleDefinition(
    name="daily_weather_retention",
    cron_schedule="20 3 * * *",
    job=weather_retention_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.RUNNING,
)


defs = Definitions(
    assets=_ASSETS,
    jobs=[
        weather_job,
        airkorea_job,
        external_weather_job,
        regional_weather_job,
        weather_retention_job,
    ],
    schedules=[
        hourly_kma_weather_schedule,
        hourly_airkorea_weather_schedule,
        hourly_external_weather_schedule,
        regional_weather_schedule,
        daily_weather_retention_schedule,
    ],
    resources=_resources,
)
