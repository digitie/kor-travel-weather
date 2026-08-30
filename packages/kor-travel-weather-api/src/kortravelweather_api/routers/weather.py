"""Public weather catalog/fact routes and token-protected admin routes."""

from __future__ import annotations

import re
from datetime import datetime
from time import perf_counter
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from kortravelweather.models import SyncRun, WeatherLocation, WeatherValue
from kortravelweather.providers import PROVIDER_CATALOG, catalog_dicts
from kortravelweather.repository import WeatherRepository

from ..auth import require_admin
from ..response import Envelope, envelope

router = APIRouter(prefix="/v1/weather", tags=["weather"])
admin_router = APIRouter(prefix="/v1/admin", tags=["admin"])


class LocationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: str
    name: str
    latitude: float
    longitude: float
    nx: int | None = None
    ny: int | None = None
    region_code: str | None = None
    enabled: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class WeatherValueOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value_id: str
    location_id: str
    provider: str
    dataset_key: str
    weather_domain: str
    forecast_style: str
    timeline_bucket: str | None = None
    metric_key: str
    metric_name: str | None = None
    source_metric_key: str | None = None
    source_metric_name: str | None = None
    value_number: float | None = None
    value_text: str | None = None
    unit: str | None = None
    severity: str | None = None
    issued_at: datetime | None = None
    valid_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    observed_at: datetime | None = None
    target_at: datetime
    known_at: datetime | None = None
    normalization_version: str
    collected_at: datetime
    source_record_key: str


class NearbyOut(LocationOut):
    distance_km: float
    latest: list[WeatherValueOut] = Field(default_factory=list)


class SourceRecordSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_record_key: str
    provider: str
    dataset_key: str
    source_entity_type: str
    source_entity_id: str
    raw_payload_hash: str
    fetched_at: datetime
    imported_at: datetime
    row_count: int | None = None
    response_metadata: dict[str, Any] = Field(default_factory=dict)


LocationListResponse = Envelope[list[LocationOut]]
LocationResponse = Envelope[LocationOut]
WeatherValueListResponse = Envelope[list[WeatherValueOut]]
NearbyListResponse = Envelope[list[NearbyOut]]
SyncRunListResponse = Envelope[list[SyncRun]]
SourceRecordListResponse = Envelope[list[SourceRecordSummary]]


class ProviderDatasetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    description: str
    endpoint: str
    cadence: str
    forecast: bool


class ProviderOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    label: str
    auth_required: bool
    credential_configured: bool | None
    base_url: str
    datasets: list[ProviderDatasetOut]


ProviderListResponse = Envelope[list[ProviderOut]]


class LocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    name: str = Field(min_length=1, max_length=200)
    latitude: float = Field(ge=33, le=43)
    longitude: float = Field(ge=124, le=132)
    nx: int | None = Field(default=None, ge=1, le=300)
    ny: int | None = Field(default=None, ge=1, le=300)
    region_code: str | None = Field(default=None, max_length=32)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class LocationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    latitude: float | None = Field(default=None, ge=33, le=43)
    longitude: float | None = Field(default=None, ge=124, le=132)
    nx: int | None = Field(default=None, ge=1, le=300)
    ny: int | None = Field(default=None, ge=1, le=300)
    region_code: str | None = Field(default=None, max_length=32)
    enabled: bool | None = None
    metadata: dict[str, Any] | None = None


class ForecastQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_at: datetime | None = None
    to_at: datetime | None = None

    @field_validator("from_at", "to_at")
    @classmethod
    def timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("from/to는 timezone-aware ISO-8601이어야 합니다.")
        return value

    @model_validator(mode="after")
    def ordered(self) -> ForecastQuery:
        if self.from_at and self.to_at and self.from_at > self.to_at:
            raise ValueError("from_at은 to_at보다 늦을 수 없습니다.")
        return self


def repository(request: Request) -> WeatherRepository:
    return request.app.state.repository


def location_out(value: WeatherLocation, *, public: bool = True) -> LocationOut:
    return LocationOut(
        location_id=value.location_id,
        name=value.name,
        latitude=value.latitude,
        longitude=value.longitude,
        nx=value.nx,
        ny=value.ny,
        region_code=value.region_code,
        enabled=value.enabled,
        # metadata may contain operational notes or credentials. It is an
        # admin-only field; public consumers receive an intentionally empty map.
        metadata=value.metadata if not public else {},
    )


def value_out(value: WeatherValue) -> WeatherValueOut:
    return WeatherValueOut(
        value_id=value.identity_key(),
        location_id=value.location_id,
        provider=value.provider,
        dataset_key=value.dataset_key,
        weather_domain=value.weather_domain,
        forecast_style=value.forecast_style.value,
        timeline_bucket=value.timeline_bucket.value if value.timeline_bucket else None,
        metric_key=value.metric_key,
        metric_name=value.metric_name,
        source_metric_key=value.source_metric_key,
        source_metric_name=value.source_metric_name,
        value_number=float(value.value_number) if value.value_number is not None else None,
        value_text=value.value_text,
        unit=value.unit,
        severity=value.severity,
        issued_at=value.issued_at,
        valid_at=value.valid_at,
        valid_from=value.valid_from,
        valid_until=value.valid_until,
        observed_at=value.observed_at,
        target_at=value.target_at or value.valid_at or value.observed_at or value.collected_at,
        known_at=value.known_at,
        normalization_version=value.normalization_version,
        collected_at=value.collected_at,
        source_record_key=value.source_record_key or "",
    )


@router.get("/locations", response_model=LocationListResponse)
async def list_locations(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    enabled: bool = Query(default=True, include_in_schema=False),
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    started = perf_counter()
    rows = await run_in_threadpool(
        repo.list_locations, enabled_only=True, search=search, limit=limit, offset=offset
    )
    total = await run_in_threadpool(repo.count_locations, enabled_only=True, search=search)
    return envelope(
        request,
        started,
        [location_out(row).model_dump(mode="json") for row in rows],
        limit=limit,
        offset=offset,
        total=total,
        returned=len(rows),
    )


@router.get("/locations/{location_id}", response_model=LocationResponse)
async def get_location(
    location_id: str, request: Request, repo: Annotated[WeatherRepository, Depends(repository)]
) -> dict[str, Any]:
    started = perf_counter()
    row = await run_in_threadpool(repo.get_location, location_id)
    if row is None or not row.enabled:
        raise HTTPException(status_code=404, detail="location을 찾을 수 없습니다.")
    return envelope(request, started, location_out(row).model_dump(mode="json"))


@router.get("/locations/{location_id}/latest", response_model=WeatherValueListResponse)
async def latest(
    location_id: str,
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    started = perf_counter()
    location = await run_in_threadpool(repo.get_location, location_id)
    if location is None or not location.enabled:
        raise HTTPException(status_code=404, detail="location을 찾을 수 없습니다.")
    rows = await run_in_threadpool(repo.latest_values, location_id, limit=limit)
    return envelope(
        request,
        started,
        [value_out(row).model_dump(mode="json") for row in rows],
        limit=limit,
        returned=len(rows),
    )


@router.get("/locations/{location_id}/forecast", response_model=WeatherValueListResponse)
async def forecast(
    location_id: str,
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    dataset_key: str | None = None,
    metric_key: str | None = None,
    history: bool = Query(default=False, description="수정 revision까지 반환"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    try:
        ForecastQuery(from_at=from_at, to_at=to_at)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="from/to 시간 범위가 올바르지 않습니다.",
        ) from exc
    started = perf_counter()
    location = await run_in_threadpool(repo.get_location, location_id)
    if location is None or not location.enabled:
        raise HTTPException(status_code=404, detail="location을 찾을 수 없습니다.")
    rows = await run_in_threadpool(
        repo.timeline,
        location_id,
        from_at=from_at,
        to_at=to_at,
        dataset_key=dataset_key,
        metric_key=metric_key,
        limit=limit,
        include_revisions=history,
    )
    return envelope(
        request,
        started,
        [value_out(row).model_dump(mode="json") for row in rows],
        limit=limit,
        returned=len(rows),
    )


@router.get("/nearby", response_model=NearbyListResponse)
async def nearby(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    lat: float = Query(ge=33, le=43),
    lon: float = Query(ge=124, le=132),
    radius_km: float = Query(default=25, gt=0, le=500),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    started = perf_counter()
    rows = await run_in_threadpool(
        repo.nearest_locations, lat, lon, radius_km=radius_km, limit=limit
    )
    latest_many = getattr(repo, "latest_values_many", None)
    latest_by_location = (
        await run_in_threadpool(
            latest_many, [location.location_id for location, _ in rows], limit_per_location=20
        )
        if callable(latest_many)
        else {}
    )
    data = []
    for location, distance in rows:
        latest_rows = latest_by_location.get(location.location_id, [])
        if not latest_rows and not callable(latest_many):
            latest_rows = await run_in_threadpool(
                repo.latest_values, location.location_id, limit=20
            )
        data.append(
            {
                **location_out(location).model_dump(mode="json"),
                "distance_km": distance,
                "latest": [value_out(row).model_dump(mode="json") for row in latest_rows],
            }
        )
    return envelope(request, started, data, limit=limit, returned=len(data))


@admin_router.get(
    "/locations",
    response_model=LocationListResponse,
    dependencies=[Depends(require_admin)],
)
async def admin_locations(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    started = perf_counter()
    rows = await run_in_threadpool(
        repo.list_locations, enabled_only=False, search=search, limit=limit, offset=offset
    )

    total = await run_in_threadpool(repo.count_locations, enabled_only=False, search=search)
    return envelope(
        request,
        started,
        [location_out(row, public=False).model_dump(mode="json") for row in rows],
        limit=limit,
        offset=offset,
        total=total,
        returned=len(rows),
    )


@admin_router.get(
    "/providers",
    response_model=ProviderListResponse,
    dependencies=[Depends(require_admin)],
)
async def admin_providers(request: Request) -> dict[str, Any]:
    """credential 자체가 아닌 configured 여부와 dataset 계약만 노출한다."""
    started = perf_counter()
    runtime_settings = request.app.state.settings
    configured = {
        spec.key: (
            not spec.auth_required or runtime_settings.provider_api_key(spec.key) is not None
        )
        for spec in PROVIDER_CATALOG
    }
    return envelope(request, started, catalog_dicts(configured=configured))


@admin_router.post(
    "/locations",
    status_code=status.HTTP_201_CREATED,
    response_model=LocationResponse,
    dependencies=[Depends(require_admin)],
)
async def create_location(
    body: LocationCreate,
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> dict[str, Any]:
    started = perf_counter()
    try:
        location = WeatherLocation(**body.model_dump())
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="location 값이 API 계약에 맞지 않습니다.",
        ) from exc
    try:
        await run_in_threadpool(repo.create_location, location)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return envelope(request, started, location_out(location, public=False).model_dump(mode="json"))


@admin_router.patch(
    "/locations/{location_id}",
    response_model=LocationResponse,
    dependencies=[Depends(require_admin)],
)
async def patch_location(
    location_id: str,
    body: LocationPatch,
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> dict[str, Any]:
    started = perf_counter()
    changes = body.model_dump(exclude_unset=True)
    try:
        updated = await run_in_threadpool(repo.patch_location, location_id, changes)
    except KeyError:
        raise HTTPException(status_code=404, detail="location을 찾을 수 없습니다.") from None
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail="location patch 값이 올바르지 않습니다."
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    return envelope(request, started, location_out(updated, public=False).model_dump(mode="json"))


@admin_router.get(
    "/sync-runs",
    response_model=SyncRunListResponse,
    dependencies=[Depends(require_admin)],
)
async def sync_runs(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    started = perf_counter()
    rows = await run_in_threadpool(repo.list_sync_runs, limit=limit)
    return envelope(
        request,
        started,
        [row.model_dump(mode="json") for row in rows],
        limit=limit,
        returned=len(rows),
    )


def source_out(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") or {}
    rows = payload.get("rows") if isinstance(payload, dict) else None

    def redact(value: Any) -> Any:
        def normalized_key(key: Any) -> str:
            # Handle snake/kebab as well as provider camelCase aliases such as
            # accessToken and clientSecret.
            return re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).lower().replace("-", "_")

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
        if isinstance(value, dict):
            return {
                key: "[REDACTED]" if normalized_key(key) in secret_names else redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, str):
            return re.sub(
                r"(?i)(service[_-]?key|api[_-]?key|auth[_-]?key|access[_-]?token|token|password|secret|key)(=|:)([^&\s,;]+)",
                r"\1\2[REDACTED]",
                value,
            )
        return value

    response_metadata = payload.get("response_metadata", {}) if isinstance(payload, dict) else {}
    return {
        "source_record_key": record["source_record_key"],
        "provider": record["provider"],
        "dataset_key": record["dataset_key"],
        "source_entity_type": record["source_entity_type"],
        "source_entity_id": record["source_entity_id"],
        "raw_payload_hash": record["raw_payload_hash"],
        "fetched_at": record["fetched_at"],
        "imported_at": record["imported_at"],
        "row_count": len(rows) if isinstance(rows, list) else None,
        "response_metadata": redact(response_metadata),
    }


@admin_router.get(
    "/sync-runs/{run_id}/sources",
    response_model=SourceRecordListResponse,
    dependencies=[Depends(require_admin)],
)
async def sync_run_sources(
    run_id: str,
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> dict[str, Any]:
    started = perf_counter()
    run = await run_in_threadpool(repo.get_sync_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="sync run을 찾을 수 없습니다.")
    keys = await run_in_threadpool(repo.list_sync_run_sources, run_id)
    records = []
    for key in keys:
        record = await run_in_threadpool(repo.get_source_record, key)
        if record is not None:
            records.append(source_out(record))
    return envelope(request, started, records, limit=len(records), returned=len(records))
