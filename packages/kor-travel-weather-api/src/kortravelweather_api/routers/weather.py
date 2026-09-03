"""Public weather catalog/fact routes and token-protected admin routes."""

from __future__ import annotations

import re
from datetime import datetime
from time import perf_counter
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from kortravelweather.alerts import active_alert_values
from kortravelweather.models import SyncRun, WeatherLocation, WeatherValue
from kortravelweather.providers import PROVIDER_CATALOG, catalog_dicts
from kortravelweather.repository import (
    WeatherRepository,
    provider_credential_fingerprint,
    provider_credential_last4,
)

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
    measurement_point: MeasurementPointOut | None = None
    latest: list[WeatherValueOut] = Field(default_factory=list)
    forecast: list[WeatherValueOut] = Field(default_factory=list)
    alerts: list[WeatherValueOut] = Field(default_factory=list)


class MeasurementPointOut(BaseModel):
    """Public, allow-listed AirKorea measurement-point metadata."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    station_id: str | None = None
    station_name: str
    address: str | None = None
    network: str | None = None
    latitude: float
    longitude: float
    distance_km: float


class CoordinateRequestOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float
    longitude: float


class ResolvedWeatherOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: CoordinateRequestOut
    location: LocationOut
    distance_km: float
    measurement_point: MeasurementPointOut | None = None
    source_locations: list[LocationOut] = Field(default_factory=list)
    latest: list[WeatherValueOut] = Field(default_factory=list)
    forecast: list[WeatherValueOut] = Field(default_factory=list)
    alerts: list[WeatherValueOut] = Field(default_factory=list)


class WeatherMarkerOut(BaseModel):
    """Bounded marker projection used by the map without full nearby payloads."""

    model_config = ConfigDict(extra="forbid")

    location_id: str
    measurement_point: MeasurementPointOut | None = None
    latest: list[WeatherValueOut] = Field(default_factory=list)
    alerts: list[WeatherValueOut] = Field(default_factory=list)


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
ResolvedWeatherResponse = Envelope[ResolvedWeatherOut]
WeatherMarkerListResponse = Envelope[list[WeatherMarkerOut]]
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


class ProviderCredentialOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    configured: bool
    source: Literal["database", "environment", "none"]
    fingerprint: str | None = None
    last4: str | None = None
    updated_at: datetime | None = None


class ProviderCredentialPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=8, max_length=4096)

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 8:
            raise ValueError("provider api key는 8자 이상이어야 합니다.")
        return normalized


ProviderCredentialListResponse = Envelope[list[ProviderCredentialOut]]
ProviderCredentialResponse = Envelope[ProviderCredentialOut]


class AdminSessionAction(BaseModel):
    """Internal web-session revocation payload; the token is never persisted."""

    model_config = ConfigDict(extra="forbid")

    session: str = Field(min_length=1, max_length=4096)


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


def measurement_point_out(
    location: WeatherLocation, *, distance_km: float
) -> MeasurementPointOut | None:
    point = location.metadata.get("measurement_point")
    if not isinstance(point, dict):
        return None
    station_name = point.get("station_name")
    if not isinstance(station_name, str) or not station_name.strip():
        return None
    return MeasurementPointOut(
        provider=str(point.get("provider") or "unknown"),
        station_id=(str(point["station_id"]) if point.get("station_id") else None),
        station_name=station_name,
        address=(str(point["address"]) if point.get("address") else None),
        network=(str(point["network"]) if point.get("network") else None),
        latitude=location.latitude,
        longitude=location.longitude,
        distance_km=distance_km,
    )


def _split_weather_values(
    rows: list[WeatherValue],
) -> tuple[list[WeatherValue], list[WeatherValue], list[WeatherValue]]:
    alerts: list[WeatherValue] = []
    forecast: list[WeatherValue] = []
    latest: list[WeatherValue] = []
    for row in rows:
        marker = f"{row.dataset_key} {row.weather_domain}".lower()
        if "alert" in marker or "warning" in marker or row.metric_key == "ALERT":
            alerts.append(row)
        elif row.forecast_style.value in {"short", "mid", "ultra_short"}:
            forecast.append(row)
        else:
            latest.append(row)
    return latest, forecast, active_alert_values(alerts)


def _weather_bundle(
    location: WeatherLocation,
    distance_km: float,
    *,
    latest_rows: list[WeatherValue],
    forecast_rows: list[WeatherValue],
    alert_rows: list[WeatherValue],
) -> dict[str, Any]:
    return {
        **location_out(location).model_dump(mode="json"),
        "distance_km": distance_km,
        "measurement_point": (
            measurement_point_out(location, distance_km=distance_km).model_dump(mode="json")
            if measurement_point_out(location, distance_km=distance_km)
            else None
        ),
        "latest": [value_out(row).model_dump(mode="json") for row in latest_rows],
        "forecast": [value_out(row).model_dump(mode="json") for row in forecast_rows],
        "alerts": [value_out(row).model_dump(mode="json") for row in alert_rows],
    }


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
    # ``latest_values`` includes alert facts for the location.  Apply the
    # same active-alert projection used by markers/resolve so a named latest
    # request cannot resurrect a released or stale notice.
    current_rows, forecast_rows, alert_rows = _split_weather_values(rows)
    rows = [*current_rows, *forecast_rows, *alert_rows]
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
        exclude_alerts=True,
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
    location_ids = [location.location_id for location, _ in rows]
    latest_many = getattr(repo, "latest_values_many", None)
    latest_by_location = (
        await run_in_threadpool(latest_many, location_ids, limit_per_location=200)
        if callable(latest_many)
        else {}
    )
    timeline_many = getattr(repo, "timeline_many", None)
    timeline_by_location = (
        await run_in_threadpool(timeline_many, location_ids, limit_per_location=500)
        if callable(timeline_many)
        else {}
    )
    data = []
    for location, distance in rows:
        latest_rows = latest_by_location.get(location.location_id, [])
        if not latest_rows and not callable(latest_many):
            latest_rows = await run_in_threadpool(
                repo.latest_values, location.location_id, limit=20
            )
        all_rows = timeline_by_location.get(location.location_id, [])
        _, forecast_rows, alert_rows = _split_weather_values(all_rows)
        current_rows, _, current_alerts = _split_weather_values(latest_rows)
        data.append(
            _weather_bundle(
                location,
                distance,
                latest_rows=current_rows,
                forecast_rows=forecast_rows,
                alert_rows=alert_rows or current_alerts,
            )
        )
    return envelope(request, started, data, limit=limit, returned=len(data))


@router.get("/resolve", response_model=ResolvedWeatherResponse)
async def resolve_weather(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    lat: float = Query(ge=33, le=43),
    lon: float = Query(ge=124, le=132),
    radius_km: float = Query(default=100, gt=0, le=500),
) -> dict[str, Any]:
    """Resolve a coordinate to one nearest anchor and all source projections."""
    started = perf_counter()
    # Prefer the nearest AirKorea measurement anchor when the catalog has one;
    # the requested coordinate contract is specifically station-centred while
    # the returned bundle still contains every provider fact for that anchor.
    rows = await run_in_threadpool(
        repo.nearest_locations, lat, lon, radius_km=radius_km, limit=10_000
    )
    if not rows:
        raise HTTPException(status_code=404, detail="요청 좌표 주변에 위치가 없습니다.")
    station_rows = [
        (location, distance)
        for location, distance in rows
        if measurement_point_out(location, distance_km=distance) is not None
    ]
    location, distance = (station_rows or rows)[0]
    # A station anchor and a KMA/external anchor can be separate catalog rows
    # even when they represent the same requested place.  Include nearby
    # anchors in a small source-radius projection and merge their current,
    # forecast, and alert facts instead of silently returning only the station
    # row.  ``nearby`` already returns deterministic distance ordering.
    source_radius = max(5.0, distance + 5.0)
    # External providers are anchored to the selected AirKorea station.  Do
    # not fan a coordinate resolve out to every other station in a 5 km
    # circle: a dense catalog can turn one request into hundreds of thousands
    # of historical rows and exceed the gateway timeout.  Keep the selected
    # station plus nearby non-station anchors (for example a KMA grid anchor)
    # that can actually represent an additional source for that station.
    source_rows = [(location, distance)]
    source_rows.extend(
        (candidate, candidate_distance)
        for candidate, candidate_distance in rows
        if candidate.location_id != location.location_id
        and candidate_distance <= source_radius
        and measurement_point_out(candidate, distance_km=candidate_distance) is None
    )
    source_ids = [candidate.location_id for candidate, _ in source_rows]
    latest_many = getattr(repo, "latest_values_many", None)
    timeline_many = getattr(repo, "timeline_many", None)
    if callable(latest_many) and callable(timeline_many):
        latest_by_location = await run_in_threadpool(
            latest_many, source_ids, limit_per_location=500
        )
        timeline_by_location = await run_in_threadpool(
            timeline_many, source_ids, limit_per_location=2000
        )
        latest_rows = [
            value
            for candidate_id in source_ids
            for value in latest_by_location.get(candidate_id, [])
        ]
        timeline_rows = [
            value
            for candidate_id in source_ids
            for value in timeline_by_location.get(candidate_id, [])
        ]
    else:
        latest_rows = []
        timeline_rows = []
        for candidate_id in source_ids:
            latest_rows.extend(
                await run_in_threadpool(repo.latest_values, candidate_id, limit=500)
            )
            timeline_rows.extend(
                await run_in_threadpool(
                    repo.timeline, candidate_id, limit=2000, include_revisions=False
                )
            )
    _, forecast_values, alert_values = _split_weather_values(timeline_rows)
    latest_values, _, latest_alerts = _split_weather_values(latest_rows)
    point = measurement_point_out(location, distance_km=distance)
    data = ResolvedWeatherOut(
        requested=CoordinateRequestOut(latitude=lat, longitude=lon),
        location=location_out(location),
        distance_km=distance,
        measurement_point=point,
        source_locations=[location_out(candidate) for candidate, _ in source_rows],
        latest=[value_out(row) for row in latest_values],
        forecast=[value_out(row) for row in forecast_values],
        alerts=[value_out(row) for row in (alert_values or latest_alerts)],
    )
    return envelope(request, started, data.model_dump(mode="json"))


@router.get("/markers", response_model=WeatherMarkerListResponse)
async def marker_summaries(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    location_ids: Annotated[list[str] | None, Query(alias="location_id", min_length=1)] = None,
) -> dict[str, Any]:
    """Return current/alert marker state for a bounded visible-location batch."""
    started = perf_counter()
    # Keep the public marker endpoint bounded even when a client accidentally
    # sends a whole unbounded catalog.  The UI chunks larger maps by request.
    requested_ids = location_ids or []
    if len(requested_ids) > 500:
        raise HTTPException(status_code=422, detail="marker location 수는 500개 이하여야 합니다.")
    unique_ids = list(dict.fromkeys(requested_ids))
    locations = await run_in_threadpool(repo.list_locations, enabled_only=True, limit=None)
    by_id = {location.location_id: location for location in locations}
    valid_ids = [location_id for location_id in unique_ids if location_id in by_id]
    marker_many = getattr(repo, "marker_values_many", None)
    if callable(marker_many):
        # The marker projection is deliberately allow-listed at the
        # repository boundary.  It avoids a second full current-row scan for
        # alerts while retaining the weather-code/temperature rows needed by
        # the map.
        marker_by_location = await run_in_threadpool(
            marker_many, valid_ids, limit_per_location=80
        )
        latest_by_location = marker_by_location
        alert_by_location = marker_by_location
    else:
        latest_by_location = None
        alert_by_location = None
    latest_many = getattr(repo, "latest_values_many", None)
    if latest_by_location is None and callable(latest_many):
        latest_by_location = await run_in_threadpool(
            latest_many, valid_ids, limit_per_location=40
        )
        alert_by_location = await run_in_threadpool(
            latest_many,
            valid_ids,
            limit_per_location=80,
            weather_domain="weather_alert",
        )
    elif latest_by_location is None:
        latest_by_location = {
            location_id: await run_in_threadpool(repo.latest_values, location_id, limit=80)
            for location_id in valid_ids
        }
        alert_by_location = {
            location_id: [
                row
                for row in latest_by_location.get(location_id, [])
                if row.weather_domain == "weather_alert"
            ]
            for location_id in valid_ids
        }
    data: list[dict[str, Any]] = []
    for location_id in valid_ids:
        _, _, latest_alerts = _split_weather_values(latest_by_location.get(location_id, []))
        _, _, filtered_alerts = _split_weather_values(alert_by_location.get(location_id, []))
        alerts = filtered_alerts or latest_alerts
        data.append(
            WeatherMarkerOut(
                location_id=location_id,
                measurement_point=measurement_point_out(
                    by_id[location_id], distance_km=0.0
                ),
                latest=[value_out(row) for row in _split_weather_values(
                    latest_by_location.get(location_id, [])
                )[0]],
                alerts=[value_out(row) for row in alerts],
            ).model_dump(mode="json")
        )
    return envelope(request, started, data, limit=len(valid_ids), returned=len(data))


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
    repo = request.app.state.repository
    database_credentials = {
        row["provider"]
        for row in await run_in_threadpool(repo.list_provider_credential_metadata)
    }
    configured = {
        spec.key: (
            not spec.auth_required
            or spec.key in database_credentials
            or runtime_settings.provider_api_key(spec.key) is not None
        )
        for spec in PROVIDER_CATALOG
    }
    return envelope(request, started, catalog_dicts(configured=configured))


def _credential_provider_spec(provider: str) -> Any:
    for spec in PROVIDER_CATALOG:
        if spec.key == provider:
            if not spec.auth_required:
                raise HTTPException(
                    status_code=409, detail="이 provider는 API key를 사용하지 않습니다."
                )
            return spec
    raise HTTPException(status_code=404, detail="provider를 찾을 수 없습니다.")


def _credential_out(
    provider: str,
    *,
    database: dict[str, Any] | None,
    environment_key: str | None,
) -> ProviderCredentialOut:
    if database is not None:
        return ProviderCredentialOut(
            provider=provider,
            configured=True,
            source="database",
            fingerprint=f"sha256:{database['fingerprint']}",
            last4=database["last4"],
            updated_at=database["updated_at"],
        )
    if environment_key is not None:
        return ProviderCredentialOut(
            provider=provider,
            configured=True,
            source="environment",
            fingerprint=f"sha256:{provider_credential_fingerprint(environment_key)}",
            last4=provider_credential_last4(environment_key),
        )
    return ProviderCredentialOut(provider=provider, configured=False, source="none")


@admin_router.get(
    "/provider-credentials",
    response_model=ProviderCredentialListResponse,
    dependencies=[Depends(require_admin)],
)
async def provider_credentials(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> dict[str, Any]:
    """List provider credential status without exposing key material."""
    started = perf_counter()
    runtime_settings = request.app.state.settings
    rows = {
        row["provider"]: row
        for row in await run_in_threadpool(repo.list_provider_credential_metadata)
    }
    data = [
        _credential_out(
            spec.key,
            database=rows.get(spec.key),
            environment_key=runtime_settings.provider_api_key(spec.key),
        ).model_dump(mode="json")
        for spec in PROVIDER_CATALOG
        if spec.auth_required
    ]
    return envelope(request, started, data)


@admin_router.put(
    "/provider-credentials/{provider}",
    response_model=ProviderCredentialResponse,
    dependencies=[Depends(require_admin)],
)
async def put_provider_credential(
    provider: str,
    body: ProviderCredentialPut,
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> dict[str, Any]:
    """Encrypt and store an admin-managed provider API key."""
    spec = _credential_provider_spec(provider)
    started = perf_counter()
    runtime_settings = request.app.state.settings
    try:
        encryption_key = runtime_settings.require_credential_encryption_key()
        metadata = await run_in_threadpool(
            repo.set_provider_credential, spec.key, body.api_key, encryption_key
        )
    except RuntimeError as exc:
        # Do not return Fernet validation details or any key material.
        raise HTTPException(
            status_code=503,
            detail="provider credential 저장 기능이 설정되지 않았습니다.",
        ) from exc
    result = _credential_out(spec.key, database=metadata, environment_key=None)
    return envelope(request, started, result.model_dump(mode="json"))


@admin_router.delete(
    "/provider-credentials/{provider}",
    response_model=ProviderCredentialResponse,
    dependencies=[Depends(require_admin)],
)
async def delete_provider_credential(
    provider: str,
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> dict[str, Any]:
    """Remove a database override and reveal only the resulting status."""
    spec = _credential_provider_spec(provider)
    started = perf_counter()
    await run_in_threadpool(repo.delete_provider_credential, spec.key)
    runtime_settings = request.app.state.settings
    metadata = await run_in_threadpool(repo.get_provider_credential_metadata, spec.key)
    result = _credential_out(
        spec.key,
        database=metadata,
        environment_key=runtime_settings.provider_api_key(spec.key),
    )
    return envelope(request, started, result.model_dump(mode="json"))


@admin_router.post(
    "/session-revocations/revoke",
    include_in_schema=False,
    dependencies=[Depends(require_admin)],
)
async def revoke_admin_session(
    body: AdminSessionAction,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> dict[str, bool]:
    """Persist a web logout marker for the server-side Next.js middleware."""
    try:
        await run_in_threadpool(repo.revoke_admin_session, body.session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="session 값이 올바르지 않습니다.") from exc
    return {"revoked": True}


@admin_router.post(
    "/session-revocations/check",
    include_in_schema=False,
    dependencies=[Depends(require_admin)],
)
async def check_admin_session(
    body: AdminSessionAction,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> dict[str, bool]:
    """Check one web session without exposing its digest or bearer value."""
    try:
        revoked = await run_in_threadpool(repo.is_admin_session_revoked, body.session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="session 값이 올바르지 않습니다.") from exc
    return {"revoked": revoked}


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
