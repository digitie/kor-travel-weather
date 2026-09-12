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
    provider_spec,
    redact_secrets,
)
from kortravelweather.providers.khoa import KHOA_PROVIDER
from kortravelweather.providers.krex import KREX_PROVIDER
from kortravelweather.providers.krforest import KRFOREST_PROVIDER
from kortravelweather.providers.sampling import spatially_even_subset
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings

from .airkorea_weather import run_airkorea_weather_sync
from .external_weather import run_external_weather_sync
from .kma_weather import run_weather_sync, targets_from_settings
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
    def _external_provider_sync(context: AssetExecutionContext) -> dict[str, object]:
        runtime = WeatherSettings()
        skipped = skipped_when_disabled(provider_key, runtime)
        if skipped is not None:
            context.add_output_metadata(skipped)
            return skipped
        repository = context.resources.weather_repository.create_repository()
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
                        )
                    )
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
            if callable(close):
                close()
        result = {
            "provider": provider_key,
            "status": "partial" if failed else "success",
            "datasets": results,
            "failed_datasets": failed,
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
    name="krforest_dust_sync",
    required_resource_keys={"krforest_client", "weather_repository"},
    description="산림 청정넷(AICAN) 미세먼지 PM10·PM2.5·PM1.0과 기상값을 publish한다.",
)
def krforest_dust_sync(context: AssetExecutionContext) -> dict[str, object]:
    runtime = WeatherSettings()
    skipped = skipped_when_disabled(KRFOREST_PROVIDER, runtime)
    if skipped is not None:
        context.add_output_metadata(skipped)
        return skipped
    repository = context.resources.weather_repository.create_repository()
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
        ahead_days=runtime.retention_ahead_days,
    )
    if result["rows_outside_any_partition"]:
        # These rows are in the DEFAULT partition, which retention never drops.
        # Nothing else in the result distinguishes that from a healthy run.
        context.log.warning(
            "%s fact rows are outside every dated partition and will never be "
            "aged out; a partition was missing when they were inserted",
            result["rows_outside_any_partition"],
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
    *_EXTERNAL_PROVIDER_ASSETS,
    khoa_beach_index_sync,
    krforest_mountain_sync,
    krforest_dust_sync,
    krex_restarea_sync,
    weather_retention_purge,
]

_unresolved_weather_job = define_asset_job("kma_weather_job", selection=[kma_weather_sync])
_unresolved_airkorea_job = define_asset_job(
    "airkorea_weather_job", selection=[airkorea_weather_sync]
)
_unresolved_external_jobs = {
    key: define_asset_job(
        external_job_name(key),
        selection=[asset_def],
        tags={EXTERNAL_RUN_GROUP_TAG: EXTERNAL_RUN_GROUP},
    )
    for key, asset_def in zip(EXTERNAL_PROVIDER_KEYS, _EXTERNAL_PROVIDER_ASSETS, strict=True)
}
_unresolved_retention_job = define_asset_job(
    "weather_retention_job", selection=[weather_retention_purge]
)
_unresolved_regional_job = define_asset_job(
    "regional_weather_job",
    selection=[
        khoa_beach_index_sync,
        krforest_mountain_sync,
        krforest_dust_sync,
        krex_restarea_sync,
    ],
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


def _resolve_job(unresolved):
    """Resolve one asset job without exposing a second module-level Definitions.

    Every job needs the *whole* asset graph to resolve against, so each call
    builds a throwaway ``Definitions``.  This was five hand-copied functions
    before the external sources became nine jobs of their own.
    """
    asset_defs = Definitions(
        assets=_ASSETS,
        resources=_resources,
    )
    return unresolved.resolve(
        asset_defs.resolve_asset_graph(),
        resource_defs=asset_defs.get_repository_def().get_top_level_resources(),
    )


weather_job = _resolve_job(_unresolved_weather_job)
airkorea_job = _resolve_job(_unresolved_airkorea_job)
external_weather_jobs = {
    key: _resolve_job(unresolved) for key, unresolved in _unresolved_external_jobs.items()
}
weather_retention_job = _resolve_job(_unresolved_retention_job)
regional_weather_job = _resolve_job(_unresolved_regional_job)

hourly_kma_weather_schedule = ScheduleDefinition(
    name="hourly_kma_weather",
    cron_schedule="0 * * * *",
    job=weather_job,
    execution_timezone="Asia/Seoul",
    default_status=DefaultScheduleStatus.RUNNING,
)

hourly_airkorea_weather_schedule = ScheduleDefinition(
    name="hourly_airkorea_weather",
    cron_schedule="10 * * * *",
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
# They still share one tick. They no longer *run* together: the run-group tag
# limit in deploy/dagster.yaml drains them a few at a time, so they cannot take
# the concurrency slots KMA, AirKorea and the regional sources need.
external_weather_schedules = [
    ScheduleDefinition(
        name=f"three_hourly_{_external_slug(key)}_weather",
        cron_schedule="15 */3 * * *",
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
        *external_weather_jobs.values(),
        regional_weather_job,
        weather_retention_job,
    ],
    schedules=[
        hourly_kma_weather_schedule,
        hourly_airkorea_weather_schedule,
        *external_weather_schedules,
        regional_weather_schedule,
        daily_weather_retention_schedule,
    ],
    resources=_resources,
)
