"""Public weather catalog/fact routes and token-protected admin routes."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from time import perf_counter
from typing import Annotated, Any, Literal

import anyio
from anyio import to_thread
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from kortravelweather.alerts import active_alert_values, alert_location_scope
from kortravelweather.export_conditional import matches_etag
from kortravelweather.models import SyncRun, WeatherKind, WeatherLocation, WeatherValue, kst_now
from kortravelweather.providers import PROVIDER_CATALOG, catalog_dicts
from kortravelweather.repository import (
    WeatherRepository,
    provider_credential_fingerprint,
    provider_credential_last4,
)

from ..auth import require_admin
from ..public_response import (
    NotModified,
    PublicRequest,
    Rendered,
    data_etag,
    read_scope,
    render,
    respond,
)
from ..read_limits import PublicReadBusy
from ..response import BundleMeta, Envelope, envelope

ProviderFilter = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=120)]] | None,
    Query(alias="provider", min_length=1, max_length=32,
          description="날씨 제공처. 여러 번 지정하면 OR로 선택"),
]


KindFilter = Annotated[
    list[WeatherKind] | None,
    Query(alias="kind", min_length=1, max_length=6,
          description="종류. alert/nowcast/ultra_short/short/mid/observed를 OR로 선택"),
]


def _kind_kwargs(kinds: list[WeatherKind] | None) -> dict[str, Any]:
    return {"kinds": kinds} if kinds is not None else {}


def _provider_kwargs(providers: list[str] | None) -> dict[str, Any]:
    return {"providers": providers} if providers is not None else {}


router = APIRouter(prefix="/v1/weather", tags=["weather"])
admin_router = APIRouter(prefix="/v1/admin", tags=["admin"])

# One ``/nearby`` row carries a whole weather bundle.  With a fixed cap per
# location the response grew linearly with ``limit`` -- roughly 525 KB and 690
# rows each -- so the documented ``limit=100`` produced a ~52 MB body that the
# gateway cut off at 30 s (measured server-side: 41.6 s, 52.5 MB).  Spend a
# fixed row budget across the requested locations instead.  A single-location
# request still gets the original depth; the per-location detail stays
# available from ``/resolve`` and ``/locations/{id}/forecast``.
#
# The forecast read is anchored at the current hour and ascends, so a smaller
# cap shortens the horizon from the far end.  Capping a newest-first read would
# do the opposite -- keep the last few hours of a multi-day horizon and drop
# everything between now and then.
_NEARBY_LATEST_PER_LOCATION = 200
_NEARBY_FORECAST_PER_LOCATION = 500
_NEARBY_LATEST_BUDGET = 1500
_NEARBY_FORECAST_BUDGET = 2500
_NEARBY_LATEST_FLOOR = 60
_NEARBY_FORECAST_FLOOR = 25
# Warnings are read outside the row budget.  A warning is announced once and
# stays active for as long as its validity window says, while current values
# keep arriving behind it, so any shared cap eventually hides it.  The alert
# reducer already collapses these to one row per warning identity.
_NEARBY_ALERT_PER_LOCATION = 20
# ``/resolve`` answers one anchor plus its co-located sources.  Its warnings are
# read on their own budget for the same reason as /nearby: otherwise the two
# endpoints can disagree about the warning state of the same coordinate.
_RESOLVE_FORECAST_PER_LOCATION = 2000
_RESOLVE_ALERT_PER_LOCATION = 40
#: 마커 자리(``marker_limiter``) 대기 상한(초). 넘으면 503·``retry-after``로 돌려보낸다.
MARKER_ADMISSION_SECONDS = 3.0


def _per_location_rows(count: int, *, budget: int, ceiling: int, floor: int) -> int:
    """Split a whole-response row budget across ``count`` locations."""
    if count <= 0:
        return ceiling
    return max(floor, min(ceiling, budget // count))


def _forecast_window_start() -> datetime:
    """Open the bundle's forecast window at the top of the current hour."""
    return kst_now().replace(minute=0, second=0, microsecond=0)


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
    sido_name: str | None = None
    sigungu_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegionLocationOut(BaseModel):
    level: Literal["sido", "sigungu"]
    region_key: str
    label: str
    member_count: int
    location: LocationOut


class RegionLocationsOut(BaseModel):
    ready: bool
    calculated_at: datetime | None = None
    items: list[RegionLocationOut] = Field(default_factory=list)


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
    alert_scope_unverified: bool = False


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
    alert_scope_unverified: bool = False


class WeatherMarkerOut(BaseModel):
    """Bounded marker projection used by the map without full nearby payloads."""

    model_config = ConfigDict(extra="forbid")

    location_id: str
    measurement_point: MeasurementPointOut | None = None
    latest: list[WeatherValueOut] = Field(default_factory=list)
    forecast: list[WeatherValueOut] = Field(default_factory=list)
    alerts: list[WeatherValueOut] = Field(default_factory=list)
    alert_scope_unverified: bool = False


class WeatherMarkerValueOut(BaseModel):
    """``view=compact`` 마커의 현재값·예보 행. 관리 지도가 아이콘·기온을 고르는 필드만 담는다.

    행 선택(위치당 상한·순서)은 ``view=full``과 같고 필드만 줄인다(호출 검토 B8).
    """

    model_config = ConfigDict(extra="forbid")

    provider: str
    metric_key: str
    metric_name: str | None = None
    value_number: float | None = None
    value_text: str | None = None
    target_at: datetime


class WeatherMarkerAlertOut(BaseModel):
    """``view=compact`` 마커의 특보 행. 배지 수와 특보 이름·등급 표시에 쓰는 필드만 담는다."""

    model_config = ConfigDict(extra="forbid")

    value_id: str
    metric_name: str | None = None
    value_text: str | None = None
    severity: str | None = None


class WeatherMarkerCompactOut(BaseModel):
    """``GET /v1/weather/markers?view=compact`` 항목. 기본(``view=full``) 계약은 바꾸지 않는다."""

    model_config = ConfigDict(extra="forbid")

    location_id: str
    measurement_point: MeasurementPointOut | None = None
    latest: list[WeatherMarkerValueOut] = Field(default_factory=list)
    forecast: list[WeatherMarkerValueOut] = Field(default_factory=list)
    alerts: list[WeatherMarkerAlertOut] = Field(default_factory=list)
    alert_scope_unverified: bool = False


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


RegionLocationsResponse = Envelope[RegionLocationsOut]
LocationListResponse = Envelope[list[LocationOut]]
LocationResponse = Envelope[LocationOut]
WeatherValueListResponse = Envelope[list[WeatherValueOut]]
NearbyListResponse = Envelope[list[NearbyOut]]
ResolvedWeatherResponse = Envelope[ResolvedWeatherOut]
WeatherMarkerListResponse = Envelope[list[WeatherMarkerOut]]
WeatherMarkerCompactListResponse = Envelope[list[WeatherMarkerCompactOut]]
MarkerView = Literal["full", "compact"]
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


class AdminLoginRateLimitAction(BaseModel):
    """Hashed web-login bucket used by the internal Next.js auth boundary."""

    model_config = ConfigDict(extra="forbid")

    bucket_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")


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


async def repository(request: Request) -> WeatherRepository:
    return request.app.state.repository


def _location_fields(value: WeatherLocation, *, public: bool = True) -> dict[str, Any]:
    return {
        "location_id": value.location_id,
        "name": value.name,
        "latitude": value.latitude,
        "longitude": value.longitude,
        "nx": value.nx,
        "ny": value.ny,
        "region_code": value.region_code,
        "enabled": value.enabled,
        # metadata may contain operational notes or credentials. It is an
        # admin-only field; public consumers receive an intentionally empty map.
        "metadata": value.metadata if not public else {},
    }


def location_out(
    value: WeatherLocation,
    *,
    public: bool = True,
    region: dict[str, Any] | None = None,
) -> LocationOut:
    """``region``은 공개 행정구역(``sido_name``·``sigungu_name``)이다."""
    return LocationOut(**_location_fields(value, public=public), **(region or {}))


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


def marker_value_out(value: WeatherValue) -> WeatherMarkerValueOut:
    """``value_out``과 같은 값을 compact 마커 필드로만 옮긴다(``target_at`` 대체 순서도 같다)."""
    return WeatherMarkerValueOut(
        provider=value.provider,
        metric_key=value.metric_key,
        metric_name=value.metric_name,
        value_number=float(value.value_number) if value.value_number is not None else None,
        value_text=value.value_text,
        target_at=value.target_at or value.valid_at or value.observed_at or value.collected_at,
    )


def marker_alert_out(value: WeatherValue) -> WeatherMarkerAlertOut:
    return WeatherMarkerAlertOut(
        value_id=value.identity_key(),
        metric_name=value.metric_name,
        value_text=value.value_text,
        severity=value.severity,
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
    location: WeatherLocation,
) -> tuple[list[WeatherValue], list[WeatherValue], list[WeatherValue]]:
    alerts: list[WeatherValue] = []
    forecast: list[WeatherValue] = []
    latest: list[WeatherValue] = []
    for row in rows:
        marker = f"{row.dataset_key} {row.weather_domain}".lower()
        if "alert" in marker or "warning" in marker or row.metric_key == "ALERT":
            if alert_location_scope(row, location) == "matched":
                alerts.append(row)
        elif row.forecast_style.value in {"short", "mid", "ultra_short"}:
            forecast.append(row)
        else:
            latest.append(row)
    return latest, forecast, active_alert_values(alerts)


def _unverified_alert_scope(rows: list[WeatherValue], location: WeatherLocation) -> bool:
    return any(
        (row.metric_key == "ALERT" or "alert" in f"{row.dataset_key} {row.weather_domain}".lower()
         or "warning" in f"{row.dataset_key} {row.weather_domain}".lower())
        and alert_location_scope(row, location) == "unverified"
        for row in rows
    )


def _weather_bundle(
    location: WeatherLocation,
    distance_km: float,
    *,
    latest_rows: list[WeatherValue],
    forecast_rows: list[WeatherValue],
    alert_rows: list[WeatherValue],
    alert_scope_unverified: bool = False,
) -> NearbyOut:
    return NearbyOut(
        **_location_fields(location),
        distance_km=distance_km,
        measurement_point=measurement_point_out(location, distance_km=distance_km),
        latest=[value_out(row) for row in latest_rows],
        forecast=[value_out(row) for row in forecast_rows],
        alerts=[value_out(row) for row in alert_rows],
        alert_scope_unverified=alert_scope_unverified,
    )


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="location을 찾을 수 없습니다.")


# 아래 공개 handler는 DB 읽기·DTO 변환·직렬화를 worker 함수 하나에서 끝내고(B3) 한 요청의
# 읽기를 연결·transaction 하나로 묶는다(B9). 연결은 DTO 변환 전에 돌려준다.


@router.get("/regions", response_model=RegionLocationsResponse)
async def region_locations(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> Response:
    """대표 위치 참조가 미준비/불일치이면 기존 전체 카탈로그 경로로 돌아간다."""
    public = PublicRequest.of(request)

    def build() -> Rendered:
        # REPEATABLE READ 읽기 하나뿐이라 공유 읽기 묶음을 쓰지 않는다. repository는 dict를
        # 돌려주므로 여기서 한 번 검증한다(예전 FastAPI 응답 검증과 같은 변환).
        result = RegionLocationsOut.model_validate(repo.region_locations())
        return render(public, RegionLocationsResponse, result, public.meta())

    return await respond(build)


@router.get(
    "/locations",
    response_model=LocationListResponse,
    responses={
        200: {
            "headers": {
                "ETag": {
                    "description": (
                        "이 페이지 data와 limit·offset·total의 약한 ETag. 요청마다 바뀌는 meta"
                        "(request_id·generated_at·duration_ms)는 넣지 않는다."
                    ),
                    "schema": {"type": "string"},
                },
            },
        },
        304: {
            "description": (
                "If-None-Match가 지금 ETag와 같음(본문 없음). 이미 가진 이 페이지의 data·"
                "meta.page.total을 그대로 쓴다. 조회는 하므로 DB 비용은 줄지 않고 전송·파싱만 준다."
            ),
            "headers": {
                "ETag": {"description": "200과 같은 약한 ETag", "schema": {"type": "string"}},
            },
        },
    },
)
async def list_locations(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    providers: ProviderFilter = None,
    kinds: KindFilter = None,
    weather_only: bool = Query(default=False, description="특보 외 일반값이 있는 위치만 선택"),
    enabled: bool = Query(default=True, include_in_schema=False),
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    if_none_match: str | None = Header(
        default=None, description="이전 200 응답의 ETag. 같으면 본문 없이 304"
    ),
) -> Response:
    public = PublicRequest.of(request)
    filters = {**_provider_kwargs(providers), **_kind_kwargs(kinds)}
    if weather_only:
        filters["weather_only"] = True

    def build() -> Rendered | NotModified:
        with read_scope(repo):
            rows = repo.list_locations(
                enabled_only=True,
                search=search,
                limit=limit,
                offset=offset,
                include_metadata=False,
                **filters,
            )
            total = repo.count_locations(enabled_only=True, search=search, **filters)
            regions = repo.public_location_regions(rows)
        data = [location_out(row, region=regions.get(row.location_id)) for row in rows]
        # 관리 지도는 화면 진입·필터 변경마다 같은 카탈로그를 다시 받는다. 내용 ETag로 바뀌지 않은
        # 페이지는 본문 없이 304로 돌려준다(호출 검토 B12).
        etag = data_etag(list[LocationOut], data, limit, offset, total)
        if matches_etag(if_none_match or "", etag):
            return NotModified(etag)
        meta = public.meta(limit=limit, offset=offset, total=total, returned=len(rows))
        return render(public, LocationListResponse, data, meta, etag=etag)

    return await respond(build)


@router.get("/locations/{location_id}", response_model=LocationResponse)
async def get_location(
    location_id: str, request: Request, repo: Annotated[WeatherRepository, Depends(repository)]
) -> Response:
    public = PublicRequest.of(request)

    def build() -> Rendered:
        row = repo.get_location(location_id)
        if row is None or not row.enabled:
            raise _not_found()
        return render(public, LocationResponse, location_out(row), public.meta())

    return await respond(build)


@router.get("/locations/{location_id}/latest", response_model=WeatherValueListResponse)
async def latest(
    location_id: str,
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    providers: ProviderFilter = None,
    kinds: KindFilter = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> Response:
    public = PublicRequest.of(request)

    def build() -> Rendered:
        with read_scope(repo):
            location = repo.get_location(location_id)
            if location is None or not location.enabled:
                raise _not_found()
            rows = repo.latest_values(
                location_id, limit=limit,
                **_provider_kwargs(providers), **_kind_kwargs(kinds),
            )
        # ``latest_values`` includes alert facts for the location.  Apply the
        # same active-alert projection used by markers/resolve so a named latest
        # request cannot resurrect a released or stale notice.
        scope_unverified = _unverified_alert_scope(rows, location)
        current_rows, forecast_rows, alert_rows = _split_weather_values(rows, location)
        rows = [*current_rows, *forecast_rows, *alert_rows]
        data = [value_out(row) for row in rows]
        meta = public.meta(
            limit=limit, returned=len(rows), alert_scope_unverified=scope_unverified,
        )
        return render(public, WeatherValueListResponse, data, meta)

    return await respond(build)


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
    providers: ProviderFilter = None,
    kinds: KindFilter = None,
    limit: int = Query(default=500, ge=1, le=5000),
) -> Response:
    try:
        ForecastQuery(from_at=from_at, to_at=to_at)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="from/to 시간 범위가 올바르지 않습니다.",
        ) from exc
    public = PublicRequest.of(request)

    def build() -> Rendered:
        with read_scope(repo):
            location = repo.get_location(location_id)
            if location is None or not location.enabled:
                raise _not_found()
            rows = repo.timeline(
                location_id,
                from_at=from_at,
                to_at=to_at,
                dataset_key=dataset_key,
                metric_key=metric_key,
                limit=limit,
                include_revisions=history,
                exclude_alerts=True,
                **_provider_kwargs(providers), **_kind_kwargs(kinds),
            )
        data = [value_out(row) for row in rows]
        meta = public.meta(limit=limit, returned=len(rows))
        return render(public, WeatherValueListResponse, data, meta)

    return await respond(build)


@router.get("/nearby", response_model=NearbyListResponse)
async def nearby(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    lat: float = Query(ge=33, le=43),
    lon: float = Query(ge=124, le=132),
    radius_km: float = Query(default=25, gt=0, le=500),
    limit: int = Query(default=20, ge=1, le=100),
) -> Response:
    public = PublicRequest.of(request)

    def build() -> Rendered:
        with read_scope(repo):
            reads = _nearby_reads(repo, lat, lon, radius_km=radius_km, limit=limit)
        return _nearby_render(public, reads, limit=limit)

    return await respond(build)


@dataclass(frozen=True)
class _NearbyReads:
    rows: list[tuple[WeatherLocation, float]]
    latest: dict[str, list[WeatherValue]]
    forecast: dict[str, list[WeatherValue]]
    alerts: dict[str, list[WeatherValue]]
    dedicated_alerts: bool
    bundle: BundleMeta


def _nearby_reads(
    repo: WeatherRepository, lat: float, lon: float, *, radius_km: float, limit: int,
) -> _NearbyReads:
    """``/nearby``의 DB 읽기. 변환은 :func:`_nearby_render`가 연결을 돌려준 뒤에 한다."""
    rows = repo.nearest_locations(lat, lon, radius_km=radius_km, limit=limit)
    location_ids = [location.location_id for location, _ in rows]
    latest_per_location = _per_location_rows(
        len(location_ids),
        budget=_NEARBY_LATEST_BUDGET,
        ceiling=_NEARBY_LATEST_PER_LOCATION,
        floor=_NEARBY_LATEST_FLOOR,
    )
    forecast_per_location = _per_location_rows(
        len(location_ids),
        budget=_NEARBY_FORECAST_BUDGET,
        ceiling=_NEARBY_FORECAST_PER_LOCATION,
        floor=_NEARBY_FORECAST_FLOOR,
    )
    latest_many = getattr(repo, "latest_values_many", None)
    if callable(latest_many):
        latest_by_location = latest_many(location_ids, limit_per_location=latest_per_location)
    else:
        # 묶음 읽기가 없는 repository double은 위치마다 최근값 20행을 읽는다(예전과 같다).
        latest_by_location = {
            location_id: repo.latest_values(location_id, limit=20)
            for location_id in location_ids
        }
    forecast_many = getattr(repo, "forecast_many", None)
    timeline_many = getattr(repo, "timeline_many", None)
    if callable(forecast_many):
        forecast_by_location = forecast_many(
            location_ids,
            from_at=_forecast_window_start(),
            limit_per_location=forecast_per_location,
        )
    elif callable(timeline_many):
        forecast_by_location = timeline_many(
            location_ids, limit_per_location=forecast_per_location
        )
    else:
        forecast_by_location = {}
    alert_many = getattr(repo, "alert_values_many", None)
    # Warnings are read on their own budget. Sharing the current-value cap drops
    # them as soon as enough observations arrive after the announcement.
    dedicated_alerts_available = callable(alert_many)
    alert_by_location = (
        alert_many(location_ids, limit_per_location=_NEARBY_ALERT_PER_LOCATION)
        if dedicated_alerts_available
        else {}
    )
    return _NearbyReads(
        rows=rows,
        latest=latest_by_location,
        forecast=forecast_by_location,
        alerts=alert_by_location,
        dedicated_alerts=dedicated_alerts_available,
        bundle=BundleMeta(
            latest_per_location=latest_per_location,
            forecast_per_location=forecast_per_location,
        ),
    )


def _nearby_render(public: PublicRequest, reads: _NearbyReads, *, limit: int) -> Rendered:
    latest_by_location = reads.latest
    forecast_by_location = reads.forecast
    alert_by_location = reads.alerts
    dedicated_alerts_available = reads.dedicated_alerts
    data = []
    for location, distance in reads.rows:
        latest_rows = latest_by_location.get(location.location_id, [])
        _, forecast_rows, timeline_alerts = _split_weather_values(
            forecast_by_location.get(location.location_id, []), location,
        )
        current_rows, _, current_alerts = _split_weather_values(latest_rows, location)
        if dedicated_alerts_available:
            # Classify rather than trust the query: ``_split_weather_values``
            # is what decides a row is a warning, and it also applies the
            # active-state reduction. Feeding rows straight to the reducer
            # would promote whatever the read returned -- a temperature row
            # included -- into the alert list.
            _, _, alert_rows = _split_weather_values(
                alert_by_location.get(location.location_id, []), location,
            )
        else:
            # No dedicated read: fall back to the shared batches, which is the
            # behaviour this endpoint had before and is still budget-bound.
            alert_rows = timeline_alerts or current_alerts
        data.append(
            _weather_bundle(
                location,
                distance,
                latest_rows=current_rows,
                forecast_rows=forecast_rows,
                alert_rows=alert_rows,
                alert_scope_unverified=_unverified_alert_scope(
                    [*latest_rows, *forecast_by_location.get(location.location_id, []),
                     *alert_by_location.get(location.location_id, [])], location,
                ),
            )
        )
    meta = public.meta(limit=limit, returned=len(data), bundle=reads.bundle)
    return render(public, NearbyListResponse, data, meta)


@router.get("/resolve", response_model=ResolvedWeatherResponse)
async def resolve_weather(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    lat: float = Query(ge=33, le=43),
    lon: float = Query(ge=124, le=132),
    radius_km: float = Query(default=100, gt=0, le=500),
) -> Response:
    """Resolve a coordinate to one nearest anchor and all source projections."""
    public = PublicRequest.of(request)

    def build() -> Rendered:
        with read_scope(repo):
            reads = _resolve_reads(repo, lat, lon, radius_km=radius_km)
        return render(public, ResolvedWeatherResponse, _resolve_out(reads, lat, lon), public.meta())

    return await respond(build)


def _resolve_anchor(
    repo: WeatherRepository, lat: float, lon: float, *, radius_km: float,
) -> tuple[tuple[WeatherLocation, float], list[tuple[WeatherLocation, float]]] | None:
    """기준 위치와 같은 지점의 원천 후보. 없으면 None.

    Prefer the nearest AirKorea measurement anchor when the catalog has one;
    the requested coordinate contract is specifically station-centred while
    the returned bundle still contains every provider fact for that anchor.

    A station anchor and a KMA/external anchor can be separate catalog rows
    even when they represent the same requested place.  Include nearby
    anchors in a small source-radius projection (``max(5, distance + 5)`` km)
    and merge their current, forecast, and alert facts instead of silently
    returning only the station row.

    External providers are anchored to the selected AirKorea station.  Do
    not fan a coordinate resolve out to every other station in a 5 km
    circle: a dense catalog can turn one request into hundreds of thousands
    of historical rows and exceed the gateway timeout.  Keep the selected
    station plus nearby non-station anchors (for example a KMA grid anchor)
    that can actually represent an additional source for that station.

    운영 repository는 이 선택을 SQL 한 문장으로 한다(``resolve_locations``, 호출 검토 B10).
    그 기능이 없는 repository double만 예전처럼 반경 안 후보를 읽어 Python에서 고른다.
    """
    resolve = getattr(repo, "resolve_locations", None)
    if callable(resolve):
        return resolve(lat, lon, radius_km=radius_km)
    rows = repo.nearest_locations(lat, lon, radius_km=radius_km, limit=10_000)
    if not rows:
        return None
    station_rows = [
        (location, distance)
        for location, distance in rows
        if measurement_point_out(location, distance_km=distance) is not None
    ]
    location, distance = (station_rows or rows)[0]
    source_radius = max(5.0, distance + 5.0)
    return (location, distance), [
        (candidate, candidate_distance)
        for candidate, candidate_distance in rows
        if candidate.location_id != location.location_id
        and candidate_distance <= source_radius
        and measurement_point_out(candidate, distance_km=candidate_distance) is None
    ]


@dataclass(frozen=True)
class _ResolveReads:
    location: WeatherLocation
    distance: float
    source_rows: list[tuple[WeatherLocation, float]]
    latest_rows: list[WeatherValue]
    timeline_rows: list[WeatherValue]
    alert_rows: list[WeatherValue]
    dedicated_alerts: bool


def _resolve_reads(
    repo: WeatherRepository, lat: float, lon: float, *, radius_km: float,
) -> _ResolveReads:
    """``/resolve``의 DB 읽기. 변환은 :func:`_resolve_out`이 연결을 돌려준 뒤에 한다."""
    resolved = _resolve_anchor(repo, lat, lon, radius_km=radius_km)
    if resolved is None:
        raise HTTPException(status_code=404, detail="요청 좌표 주변에 위치가 없습니다.")
    (location, distance), candidates = resolved
    source_rows = [(location, distance), *candidates]
    source_ids = [candidate.location_id for candidate, _ in source_rows]
    latest_many = getattr(repo, "latest_values_many", None)
    timeline_many = getattr(repo, "timeline_many", None)
    alert_many = getattr(repo, "alert_values_many", None)
    alert_rows: list[WeatherValue] = []
    dedicated_alerts_available = False
    if callable(latest_many) and callable(timeline_many):
        latest_by_location = latest_many(source_ids, limit_per_location=500)
        latest_rows = [
            value
            for candidate_id in source_ids
            for value in latest_by_location.get(candidate_id, [])
        ]
        # Unlike /nearby this keeps the whole newest-first window.  The
        # near-term loss that forced /nearby forward needs the row cap to be
        # small against the horizon; here one anchor gets 2000 rows -- 16x the
        # widest /nearby cap -- and the window is dominated by past values
        # rather than exhausted by future ones.  /resolve is also the
        # documented full-history bundle for a point, so making it future-only
        # would drop the recent observations its consumers render.
        forecast_by_location = timeline_many(
            source_ids, limit_per_location=_RESOLVE_FORECAST_PER_LOCATION
        )
        timeline_rows = [
            value
            for candidate_id in source_ids
            for value in forecast_by_location.get(candidate_id, [])
        ]
        if callable(alert_many):
            # Warnings would otherwise compete with the forecast horizon for
            # the same cap, so /resolve could answer alerts=[] while /markers
            # shows a badge for the same coordinate.
            dedicated_alerts_available = True
            alert_by_location = alert_many(
                source_ids, limit_per_location=_RESOLVE_ALERT_PER_LOCATION
            )
            alert_rows = [
                value
                for candidate_id in source_ids
                for value in alert_by_location.get(candidate_id, [])
            ]
    else:
        latest_rows = []
        timeline_rows = []
        for candidate_id in source_ids:
            latest_rows.extend(repo.latest_values(candidate_id, limit=500))
            timeline_rows.extend(
                repo.timeline(candidate_id, limit=2000, include_revisions=False)
            )
    return _ResolveReads(
        location=location,
        distance=distance,
        source_rows=source_rows,
        latest_rows=latest_rows,
        timeline_rows=timeline_rows,
        alert_rows=alert_rows,
        dedicated_alerts=dedicated_alerts_available,
    )


def _resolve_out(reads: _ResolveReads, lat: float, lon: float) -> ResolvedWeatherOut:
    location, distance = reads.location, reads.distance
    latest_rows, timeline_rows = reads.latest_rows, reads.timeline_rows
    alert_rows = reads.alert_rows
    dedicated_alerts_available = reads.dedicated_alerts
    # 가장 가까운 관측소의 특보구역을 다른 요청 좌표에 그대로 적용하지 않는다.
    alert_location = location
    if (round(lat, 6), round(lon, 6)) != (location.latitude, location.longitude):
        alert_location = location.model_copy(update={"metadata": {}})
    _, forecast_values, timeline_alerts = _split_weather_values(timeline_rows, alert_location)
    latest_values, _, latest_alerts = _split_weather_values(latest_rows, alert_location)
    if dedicated_alerts_available:
        # Branch on availability, not on whether the read happened to return
        # something. A truthiness test would quietly restore the shared-budget
        # path for every ordinary coordinate -- the exact behaviour the
        # dedicated read exists to replace -- and would let the two paths
        # disagree about the same warning.
        _, _, alert_values = _split_weather_values(alert_rows, alert_location)
    else:
        alert_values = timeline_alerts
    point = measurement_point_out(location, distance_km=distance)
    return ResolvedWeatherOut(
        requested=CoordinateRequestOut(latitude=lat, longitude=lon),
        location=location_out(location),
        distance_km=distance,
        measurement_point=point,
        source_locations=[location_out(candidate) for candidate, _ in reads.source_rows],
        alert_scope_unverified=_unverified_alert_scope(
            [*latest_rows, *timeline_rows, *alert_rows], alert_location,
        ),
        latest=[value_out(row) for row in latest_values],
        forecast=[value_out(row) for row in forecast_values],
        alerts=[
            value_out(row)
            for row in (
                # With a dedicated read the answer is whatever it found,
                # including nothing. Falling back to the budget-shared batch
                # would resurrect warnings the activity bound deliberately
                # excluded, so the two paths would disagree.
                alert_values if dedicated_alerts_available else (alert_values or latest_alerts)
            )
        ],
    )


@router.get("/markers", response_model=WeatherMarkerListResponse)
async def marker_summaries(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    location_ids: Annotated[list[str] | None, Query(alias="location_id", min_length=1)] = None,
    providers: ProviderFilter = None,
    kinds: KindFilter = None,
    view: Annotated[MarkerView, Query(
        description=(
            "응답 모양. 기본 full은 WeatherMarkerOut(값 행 전체 필드)이다. compact는 같은 행을 "
            "지도 표시 필드만 담은 WeatherMarkerCompactOut으로 돌려준다(200 응답의 "
            "x-compact-schema). 행 선택·상한·순서는 둘이 같다."
        ),
    )] = "full",
) -> Response:
    """Return current/alert marker state for a bounded visible-location batch."""
    public = PublicRequest.of(request)
    # Keep the public marker endpoint bounded even when a client accidentally
    # sends a whole unbounded catalog.  The UI chunks larger maps by request.
    if len(location_ids or []) > 500:
        raise HTTPException(status_code=422, detail="marker location 수는 500개 이하여야 합니다.")
    limiter = request.app.state.marker_limiter
    try:
        # 자리 두 개가 모두 오래 걸리는 묶음에 잡혀 있으면 마감(15초)까지 매달리지 않고
        # 곧바로 503·retry-after로 돌려보낸다(호출 검토 B11). 브라우저가 다시 보낸다.
        with anyio.fail_after(MARKER_ADMISSION_SECONDS):
            await limiter.acquire()
    except TimeoutError:
        raise PublicReadBusy("MARKER_BUSY") from None
    try:
        # 대기하는 사이 지도 이동으로 취소된 요청은 DB 작업을 새로 시작하지 않는다.
        if await request.is_disconnected():
            raise HTTPException(status_code=499, detail="요청이 취소되었습니다.")
        return await respond(partial(
            _marker_summaries, public, repo, location_ids or [],
            providers=providers, kinds=kinds, view=view,
        ))
    finally:
        limiter.release()


def _marker_summaries(
    public: PublicRequest,
    repo: WeatherRepository,
    requested_ids: list[str],
    *,
    providers: list[str] | None = None,
    kinds: list[WeatherKind] | None = None,
    view: MarkerView = "full",
) -> Rendered:
    """입장이 허용된 마커 배치만 조회·변환한다(worker thread)."""
    unique_ids = list(dict.fromkeys(requested_ids))
    # 위치 PK 조회는 자기 짧은 transaction에서 끝낸다. 긴 projection 읽기와 한 묶음에 넣으면
    # weather_locations의 ACCESS SHARE를 마커 묶음 내내 쥐어 detached partition DROP의
    # quiet 창(``_DETACHED_DROP_HOT_TABLES``)을 줄인다. projection 읽기는 위치를 다시 읽지
    # 않는다(``locations=by_id``).
    by_id, valid_ids = _marker_locations(repo, unique_ids)
    with read_scope(repo):
        latest_by_location, alert_by_location = _marker_values(
            repo, by_id, valid_ids, providers=providers, kinds=kinds,
        )
    data: list[WeatherMarkerOut] = []
    compact: list[WeatherMarkerCompactOut] = []
    for location_id in valid_ids:
        latest, forecast, latest_alerts = _split_weather_values(
            latest_by_location.get(location_id, []), by_id[location_id],
        )
        # 운영 marker projection은 같은 묶음을 공유한다. 특보 판정은 한 번만
        # 수행하되 별도 alerts를 돌려주는 기존 repository double도 지원한다.
        if alert_by_location is latest_by_location:
            alerts = latest_alerts
        else:
            _, _, filtered_alerts = _split_weather_values(
                alert_by_location.get(location_id, []), by_id[location_id],
            )
            alerts = filtered_alerts or latest_alerts
        point = measurement_point_out(by_id[location_id], distance_km=0.0)
        unverified = _unverified_alert_scope(
            alert_by_location.get(location_id, []), by_id[location_id],
        )
        if kinds is None:
            forecast = []
        if view == "compact":
            compact.append(
                WeatherMarkerCompactOut(
                    location_id=location_id,
                    measurement_point=point,
                    latest=[marker_value_out(row) for row in latest],
                    forecast=[marker_value_out(row) for row in forecast],
                    alerts=[marker_alert_out(row) for row in alerts],
                    alert_scope_unverified=unverified,
                )
            )
            continue
        data.append(
            WeatherMarkerOut(
                location_id=location_id,
                measurement_point=point,
                latest=[value_out(row) for row in latest],
                forecast=[value_out(row) for row in forecast],
                alerts=[value_out(row) for row in alerts],
                alert_scope_unverified=unverified,
            )
        )
    if view == "compact":
        meta = public.meta(limit=len(valid_ids), returned=len(compact))
        return render(public, WeatherMarkerCompactListResponse, compact, meta)
    meta = public.meta(limit=len(valid_ids), returned=len(data))
    return render(public, WeatherMarkerListResponse, data, meta)


#: ``view=compact`` 응답 envelope의 OpenAPI component 이름(FastAPI 이름 규칙과 같다).
MARKER_COMPACT_SCHEMA = "Envelope_list_WeatherMarkerCompactOut__"


def add_marker_compact_schema(schema: dict[str, Any]) -> None:
    """``view=compact`` 응답 모양을 OpenAPI component로 싣는다.

    200의 ``application/json`` schema는 기본(full) envelope 그대로 둔다. 기존 소비자(PinVi)의
    생성 타입·계약 시험이 응답 union으로 바뀌지 않게 하려는 것이다. compact 모양은 200 응답의
    ``x-compact-schema``가 가리킨다.
    """
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    generated = WeatherMarkerCompactListResponse.model_json_schema(
        mode="serialization", ref_template="#/components/schemas/{model}"
    )
    for name, definition in generated.pop("$defs", {}).items():
        components.setdefault(name, definition)
    generated["title"] = "Envelope[list[WeatherMarkerCompactOut]]"
    components[MARKER_COMPACT_SCHEMA] = generated
    operation = schema.get("paths", {}).get("/v1/weather/markers", {}).get("get")
    if isinstance(operation, dict):
        ok = operation.setdefault("responses", {}).setdefault("200", {})
        ok["x-compact-schema"] = {"$ref": f"#/components/schemas/{MARKER_COMPACT_SCHEMA}"}


def _marker_locations(
    repo: WeatherRepository, unique_ids: list[str],
) -> tuple[dict[str, WeatherLocation], list[str]]:
    """마커 묶음의 활성 위치와 요청 순서의 유효 ID."""
    get_by_ids = getattr(repo, "get_locations_by_ids", None)
    if callable(get_by_ids):
        locations = get_by_ids(unique_ids, enabled_only=True)
    else:
        # Keep compatibility with lightweight repository doubles used by
        # downstream integrations.  The production repository takes the
        # primary-key path above and never scans the full catalog per batch.
        locations = repo.list_locations(enabled_only=True, limit=None)
    by_id = {location.location_id: location for location in locations}
    return by_id, [location_id for location_id in unique_ids if location_id in by_id]


def _marker_values(
    repo: WeatherRepository,
    by_id: dict[str, WeatherLocation],
    valid_ids: list[str],
    *,
    providers: list[str] | None,
    kinds: list[WeatherKind] | None,
) -> tuple[dict[str, list[WeatherValue]], dict[str, list[WeatherValue]]]:
    """마커 묶음의 값 읽기: 현재값 묶음·특보 묶음(운영은 같은 묶음)."""
    marker_many = getattr(repo, "marker_values_many", None)
    if callable(marker_many):
        # The marker projection is deliberately allow-listed at the
        # repository boundary.  It avoids a second full current-row scan for
        # alerts while retaining the weather-code/temperature rows needed by
        # the map.
        # 같은 위치 정보로 특보 구역을 거르고 판정한다(두 번 읽으면 그 사이 catalog
        # 수정이 끼어들 수 있다). 이 인자를 모르는 repository double은 그대로 둔다.
        same_catalog = (
            {"locations": by_id}
            if "locations" in inspect.signature(marker_many).parameters else {}
        )
        marker_by_location = marker_many(
            valid_ids, limit_per_location=80,
            **_provider_kwargs(providers), **_kind_kwargs(kinds), **same_catalog,
        )
        return marker_by_location, marker_by_location
    latest_many = getattr(repo, "latest_values_many", None)
    if callable(latest_many):
        latest_by_location = latest_many(
            valid_ids, limit_per_location=40, **_provider_kwargs(providers)
        )
        alert_by_location = latest_many(
            valid_ids,
            limit_per_location=80,
            weather_domain="weather_alert",
            **_provider_kwargs(providers),
        )
        return latest_by_location, alert_by_location
    latest_by_location = {
        location_id: repo.latest_values(location_id, limit=80, **_provider_kwargs(providers))
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
    return latest_by_location, alert_by_location


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
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> dict[str, bool]:
    """Check one web session without exposing its digest or bearer value."""
    try:
        revoked = await to_thread.run_sync(
            repo.is_admin_session_revoked, body.session, limiter=request.app.state.auth_limiter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="session 값이 올바르지 않습니다.") from exc
    return {"revoked": revoked}


@admin_router.post(
    "/login-rate-limit/check",
    include_in_schema=False,
    dependencies=[Depends(require_admin)],
)
async def check_admin_login_rate_limit(
    body: AdminLoginRateLimitAction,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> dict[str, bool | int | None]:
    """Check the shared web login-failure bucket without exposing its key."""
    try:
        retry_after = await run_in_threadpool(
            repo.check_admin_login_rate_limit, body.bucket_hash
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="login rate-limit bucket이 올바르지 않습니다."
        ) from exc
    return {"allowed": retry_after is None, "retry_after": retry_after}


@admin_router.post(
    "/login-rate-limit/failure",
    include_in_schema=False,
    dependencies=[Depends(require_admin)],
)
async def record_admin_login_failure(
    body: AdminLoginRateLimitAction,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> dict[str, bool | int | None]:
    """Atomically record one failed web login attempt."""
    try:
        retry_after = await run_in_threadpool(
            repo.record_admin_login_failure, body.bucket_hash
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="login rate-limit bucket이 올바르지 않습니다."
        ) from exc
    return {"allowed": retry_after is None, "retry_after": retry_after}


@admin_router.post(
    "/login-rate-limit/success",
    include_in_schema=False,
    dependencies=[Depends(require_admin)],
)
async def clear_admin_login_rate_limit(
    body: AdminLoginRateLimitAction,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> dict[str, bool]:
    """Clear the shared failed-attempt bucket after successful authentication."""
    try:
        await run_in_threadpool(repo.clear_admin_login_rate_limit, body.bucket_hash)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="login rate-limit bucket이 올바르지 않습니다."
        ) from exc
    return {"cleared": True}


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
    """원천 메타 요약을 관리 응답으로. 비밀 이름의 키·값은 가린다."""

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

    response_metadata = record.get("response_metadata")
    if not isinstance(response_metadata, dict):
        response_metadata = {}
    return {
        "source_record_key": record["source_record_key"],
        "provider": record["provider"],
        "dataset_key": record["dataset_key"],
        "source_entity_type": record["source_entity_type"],
        "source_entity_id": record["source_entity_id"],
        "raw_payload_hash": record["raw_payload_hash"],
        "fetched_at": record["fetched_at"],
        "imported_at": record["imported_at"],
        "row_count": record.get("row_count"),
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
    # 키마다 threadpool·세션·원문 전체 JSON을 왕복하던 N+1을 한 문장으로 줄였다.
    summaries = await run_in_threadpool(repo.list_sync_run_source_summaries, run_id)
    records = [source_out(summary) for summary in summaries]
    return envelope(request, started, records, limit=len(records), returned=len(records))


class AddressRefreshSummary(BaseModel):
    total: int
    due: int
    failed: int
    last_resolved_at: str | None
    in_progress: bool
    manual_cycle: bool
    refresh_months: int
    configured: bool
    auto_enabled: bool
    items: list[dict[str, Any]]


@admin_router.get(
    "/address-refresh", response_model=Envelope[AddressRefreshSummary],
    dependencies=[Depends(require_admin)],
)
async def address_refresh_summary(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
) -> dict[str, Any]:
    from kortravelweather.address_refresh import address_summary

    started = perf_counter()
    data = await run_in_threadpool(address_summary, repo, request.app.state.settings)
    return envelope(request, started, data)
