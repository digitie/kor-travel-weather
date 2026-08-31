"""Dagster Definitions and hourly KMA sync schedule."""

from dagster import (
    AssetExecutionContext,
    DefaultScheduleStatus,
    Definitions,
    ScheduleDefinition,
    asset,
    define_asset_job,
)

from kortravelweather.providers import ProviderLocation
from kortravelweather.settings import WeatherSettings

from .external_weather import run_external_weather_sync
from .kma_weather import run_weather_sync, targets_from_settings
from .resources import ExternalWeatherProviderResource, KmaClientResource, WeatherRepositoryResource


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
        if location.enabled
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
        # Count the validated target set, including generated extra points,
        # rather than the pre-validation catalog snapshot.
        run = repository.start_sync_run(
            provider="python-kma-api",
            dataset_key="kma_weather_bundle",
            locations_total=len(targets),
        )
        client = client_resource.create_client(settings=settings, repository=repository)
        data_client = (
            client_resource.create_data_client(settings=settings, repository=repository)
            if any(target.has_mid for target in targets)
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
            include_mid=any(target.has_mid for target in targets),
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
    name="external_weather_sync",
    required_resource_keys={"external_weather", "weather_repository"},
    description="provider-independent external weather response를 atomic publish한다.",
)
def external_weather_sync(context: AssetExecutionContext) -> dict[str, object]:
    runtime = WeatherSettings()
    repository = context.resources.weather_repository.create_repository()
    resource = context.resources.external_weather
    provider = resource.create_provider(settings=runtime, repository=repository)
    locations = repository.list_locations(enabled_only=True, limit=None)
    targets = [
        ProviderLocation(
            location_id=location.location_id,
            latitude=location.latitude,
            longitude=location.longitude,
            metadata=location.metadata,
        )
        for location in locations
    ]
    try:
        result = run_external_weather_sync(
            repository=repository,
            provider=provider,
            targets=targets,
            dataset_key=resource.dataset_key,
            max_targets=runtime.max_targets_per_run,
            max_response_rows=runtime.max_response_rows_per_run,
            max_values=runtime.max_values_per_run,
            max_payload_bytes=runtime.max_payload_bytes_per_run,
        )
        context.add_output_metadata(result)
        return result
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()


_unresolved_weather_job = define_asset_job("kma_weather_job", selection=[kma_weather_sync])
_unresolved_external_job = define_asset_job(
    "external_weather_job", selection=[external_weather_sync]
)

# Resolve the asset job before exposing it from ``Definitions``.  Passing an
# ``UnresolvedAssetJobDefinition`` directly emits a deprecation warning today
# and becomes an error in newer Dagster releases.
_resources = {
    "kma_client": KmaClientResource(),
    "weather_repository": WeatherRepositoryResource(),
    "external_weather": ExternalWeatherProviderResource(),
}


def _resolve_weather_job():
    """Resolve the asset job without exposing a second module-level Definitions."""
    asset_defs = Definitions(assets=[kma_weather_sync, external_weather_sync], resources=_resources)
    return _unresolved_weather_job.resolve(
        asset_defs.resolve_asset_graph(),
        resource_defs=asset_defs.get_repository_def().get_top_level_resources(),
    )


weather_job = _resolve_weather_job()


def _resolve_external_job():
    asset_defs = Definitions(assets=[kma_weather_sync, external_weather_sync], resources=_resources)
    return _unresolved_external_job.resolve(
        asset_defs.resolve_asset_graph(),
        resource_defs=asset_defs.get_repository_def().get_top_level_resources(),
    )


external_weather_job = _resolve_external_job()

hourly_kma_weather_schedule = ScheduleDefinition(
    name="hourly_kma_weather",
    cron_schedule="0 * * * *",
    job=weather_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.RUNNING,
)


defs = Definitions(
    assets=[kma_weather_sync, external_weather_sync],
    jobs=[weather_job, external_weather_job],
    schedules=[hourly_kma_weather_schedule],
    resources=_resources,
)
