"""Public weather catalog/fact routes and token-protected admin routes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from time import perf_counter
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherRepository

from ..auth import require_admin
from ..response import envelope

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


class LocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: str = Field(min_length=1, max_length=120)
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


def location_out(value: WeatherLocation) -> LocationOut:
    return LocationOut(
        location_id=value.location_id,
        name=value.name,
        latitude=value.latitude,
        longitude=value.longitude,
        nx=value.nx,
        ny=value.ny,
        region_code=value.region_code,
        enabled=value.enabled,
        metadata=value.metadata,
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


@router.get("/locations")
async def list_locations(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    enabled: bool = True,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    started = perf_counter()
    rows = await run_in_threadpool(
        repo.list_locations, enabled_only=enabled, search=search, limit=limit, offset=offset
    )
    return envelope(request, started, [location_out(row).model_dump(mode="json") for row in rows], limit=limit, offset=offset, returned=len(rows))


@router.get("/locations/{location_id}")
async def get_location(
    location_id: str, request: Request, repo: Annotated[WeatherRepository, Depends(repository)]
) -> dict[str, Any]:
    started = perf_counter()
    row = await run_in_threadpool(repo.get_location, location_id)
    if row is None:
        raise HTTPException(status_code=404, detail="location을 찾을 수 없습니다.")
    return envelope(request, started, location_out(row).model_dump(mode="json"))


@router.get("/locations/{location_id}/latest")
async def latest(
    location_id: str,
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    started = perf_counter()
    if await run_in_threadpool(repo.get_location, location_id) is None:
        raise HTTPException(status_code=404, detail="location을 찾을 수 없습니다.")
    rows = await run_in_threadpool(repo.latest_values, location_id, limit=limit)
    return envelope(request, started, [value_out(row).model_dump(mode="json") for row in rows], limit=limit, returned=len(rows))


@router.get("/locations/{location_id}/forecast")
async def forecast(
    location_id: str,
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    dataset_key: str | None = None,
    metric_key: str | None = None,
    history: bool = Query(default=False, description="수정 revision까지 반환"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    try:
        ForecastQuery(from_at=from_at, to_at=to_at)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    started = perf_counter()
    if await run_in_threadpool(repo.get_location, location_id) is None:
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
    return envelope(request, started, [value_out(row).model_dump(mode="json") for row in rows], limit=limit, returned=len(rows))


@router.get("/nearby")
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
    data = [
        {**location_out(location).model_dump(mode="json"), "distance_km": distance}
        for location, distance in rows
    ]
    return envelope(request, started, data, limit=limit, returned=len(data))


@admin_router.get("/locations", dependencies=[Depends(require_admin)])
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
    return envelope(request, started, [location_out(row).model_dump(mode="json") for row in rows], limit=limit, offset=offset, returned=len(rows))


@admin_router.post("/locations", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_location(
    body: LocationCreate,
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> dict[str, Any]:
    started = perf_counter()
    location = WeatherLocation(**body.model_dump())
    if await run_in_threadpool(repo.get_location, location.location_id) is not None:
        raise HTTPException(status_code=409, detail="location_id가 이미 존재합니다.")
    await run_in_threadpool(repo.upsert_location, location)
    return envelope(request, started, location_out(location).model_dump(mode="json"))


@admin_router.patch("/locations/{location_id}", dependencies=[Depends(require_admin)])
async def patch_location(
    location_id: str,
    body: LocationPatch,
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> dict[str, Any]:
    started = perf_counter()
    current = await run_in_threadpool(repo.get_location, location_id)
    if current is None:
        raise HTTPException(status_code=404, detail="location을 찾을 수 없습니다.")
    changes = body.model_dump(exclude_unset=True)
    updated = current.model_copy(update=changes)
    # model_copy does not re-run pydantic validation; reconstruct to keep
    # coordinate/id constraints effective for admin writes.
    updated = WeatherLocation.model_validate(updated.model_dump())
    await run_in_threadpool(repo.upsert_location, updated)
    return envelope(request, started, location_out(updated).model_dump(mode="json"))


@admin_router.get("/sync-runs", dependencies=[Depends(require_admin)])
async def sync_runs(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    started = perf_counter()
    rows = await run_in_threadpool(repo.list_sync_runs, limit=limit)
    return envelope(request, started, [row.model_dump(mode="json") for row in rows], limit=limit, returned=len(rows))
