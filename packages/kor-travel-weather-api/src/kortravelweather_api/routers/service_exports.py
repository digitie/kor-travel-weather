"""Map 전용 export 계약. 성공 head 구현 전에는 빈 성공 대신 503을 반환한다."""

from __future__ import annotations

import gzip
import json
from datetime import date
from functools import partial
from typing import Annotated, Generic, Literal, TypeVar

from anyio import to_thread
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from kortravelweather.export_artifacts import ExportArtifactError, ExportArtifactStore
from kortravelweather.export_daily_reader import DailySummaryExports
from kortravelweather.export_forecasts import FrozenForecastExports
from kortravelweather.export_locations import FrozenLocationExports
from kortravelweather.export_snapshots import (
    ExportCursorInvalid,
    ExportSnapshotExpired,
    FrozenCurrentExports,
)
from kortravelweather.export_source_registry import select_export_sources
from kortravelweather.export_status_reader import read_status
from kortravelweather.models import kst_now

from ..auth import require_admin
from ..response import Problem, request_id

SourceStatus = Literal["ok", "stale", "failed"]
ExportStyle = Literal["observed", "nowcast", "ultra_short", "short", "mid", "external"]


class ExportMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: AwareDatetime
    source_collected_at: AwareDatetime | None
    source_status: SourceStatus
    item_count: int = Field(ge=0, le=5000)
    snapshot_id: str | None = None
    snapshot_generated_at: AwareDatetime | None = None
    as_of: AwareDatetime | None = None
    next_cursor: str | None = None
    has_more: bool = False
    next_since: AwareDatetime | None = None


class ExportLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: str
    name: str
    latitude: float | None
    longitude: float | None
    legal_dong_code: str | None = Field(default=None, pattern=r"^[0-9]{10}$")
    sido_name: str | None = None
    sigungu_name: str | None = None


class ExportCurrentValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: str
    provider: str
    metric_key: str
    value: float | str | None
    unit: str | None
    target_at: AwareDatetime
    known_at: AwareDatetime
    forecast_style: Literal["observed", "nowcast"]


class ExportSourceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    dataset_key: str
    forecast_style: ExportStyle
    last_status: str | None
    last_success_at: AwareDatetime | None
    last_failed_at: AwareDatetime | None
    last_attempt_failed: bool
    next_scheduled_at: AwareDatetime | None


class ExportForecastGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: str
    provider: str
    forecast_style: Literal["ultra_short", "short", "mid", "external"]
    known_at: AwareDatetime
    published_at: AwareDatetime
    target_at: list[AwareDatetime]
    metrics: dict[str, list[float | str | None]]


class ExportDailySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: str
    date: date
    slot: Literal["morning", "noon", "evening"]
    temp_min: float | None
    temp_max: float | None
    temp_avg: float | None
    humidity_avg: float | None
    precip_sum_mm: float | None
    precip_occurred: bool | None
    sky_code: int | None
    wind_speed_avg: float | None
    sample_count: int = Field(ge=0, le=6)
    source_providers: list[str]
    partial: bool


Item = TypeVar("Item")


class ExportPage(BaseModel, Generic[Item]):
    model_config = ConfigDict(extra="forbid")

    items: list[Item]
    meta: ExportMeta


class ExportUnavailable(Problem):
    meta: ExportMeta


router = APIRouter(
    prefix="/v1/service/exports",
    tags=["service exports"],
    dependencies=[Depends(require_admin)],
    responses={
        410: {
            "model": ExportUnavailable,
            "description": "고정 snapshot TTL 만료. 첫 페이지부터 재시작",
        },
        304: {"description": "데이터/vector/페이지/상태가 변경되지 않음"},
        503: {
            "model": ExportUnavailable,
            "description": "완결된 성공 export가 없습니다. 빈 정상 집합으로 해석하지 마세요.",
        },
    },
)
Limit = Annotated[int, Query(ge=1, le=5000)]
Cursor = Annotated[str | None, Query(min_length=1, max_length=4096)]


def _unavailable(
    request: Request, *, status: int = 503, code: str = "EXPORT_NOT_READY"
) -> JSONResponse:
    # mutable current의 부분 commit을 성공 snapshot으로 공개하지 않는다.
    # 다음 단계의 receipt·terminal 성공 승격 전에는 DB/provider도 조회하지 않는다.
    body = ExportUnavailable(
        title="서비스 export 준비 중",
        status=status,
        detail="완결된 성공 export 집합이 아직 없습니다.",
        code=code,
        request_id=request_id(request),
        meta=ExportMeta(
            generated_at=kst_now(),
            source_collected_at=None,
            source_status="failed",
            item_count=0,
        ),
    )
    return JSONResponse(
        status_code=status,
        content=body.model_dump(mode="json"),
        media_type="application/problem+json",
        headers={"cache-control": "no-store, private", "x-source-status": "failed"},
    )


def _accepts_gzip(header: str) -> bool:
    for token in header.lower().split(","):
        parts = [part.strip() for part in token.split(";")]
        if parts[0] != "gzip":
            continue
        try:
            return all(float(part[2:]) > 0 for part in parts[1:] if part.startswith("q="))
        except ValueError:
            return False
    return False


def _matches_etag(header: str, etag: str) -> bool:
    return any(
        value.strip().removeprefix("W/") in {etag.removeprefix("W/"), "*"}
        for value in header.split(",")
    )


async def _frozen_response(
    request: Request,
    resource: str,
    limit: int,
    cursor: str | None,
    *,
    provider: str | None = None,
    style: str | None = None,
    since: AwareDatetime | None = None,
    days: int = 14,
    location_id: str | None = None,
) -> Response:
    settings = request.app.state.settings
    if (
        not settings.service_exports_enabled
        or settings.service_export_artifact_root is None
        or settings.admin_token is None
    ):
        return _unavailable(request)
    # 지원하지 않는 provider를 누락한 작은 집합을 전체 정상으로 내보내지 않는다.
    selection = None
    if resource in {"current", "forecasts"}:
        try:
            selection = select_export_sources(
                settings.enabled_providers, resource=resource, style=style, provider=provider,
            )
        except ExportArtifactError:
            if cursor:
                return _unavailable(request, status=422, code="EXPORT_CURSOR_INVALID")
            return _unavailable(request)
    elif resource == "daily-summaries" and set(settings.enabled_providers) != {"python-kma-api"}:
        return _unavailable(request)
    try:
        reader_class = {
            "forecasts": FrozenForecastExports,
            "daily-summaries": DailySummaryExports,
            "locations": FrozenLocationExports,
        }.get(resource, FrozenCurrentExports)
        if resource == "forecasts" and style == "mid":
            from kortravelweather.export_mid_reader import FrozenMidForecastExports

            reader_class = FrozenMidForecastExports
        reader = reader_class(
            request.app.state.repository,
            ExportArtifactStore(settings.service_export_artifact_root),
            signing_key=settings.admin_token.get_secret_value(),
            **(
                {"providers": selection.providers, "source_inventory": True,
                 "expected_sources": selection.expected, "optional_sources": selection.optional}
                if selection is not None
                else {}
            ),
            **({"style": style, "since": since} if resource == "forecasts" else {}),
            **({"days": days, "location_id": location_id} if resource == "daily-summaries" else {}),
        )
        page, etag = await to_thread.run_sync(
            partial(
                reader.page,
                *([] if resource in {"forecasts", "daily-summaries"} else [resource]),
                limit=limit,
                cursor=cursor,
            ),
            limiter=request.app.state.service_export_limiter,
        )
    except ExportCursorInvalid:
        return _unavailable(request, status=422, code="EXPORT_CURSOR_INVALID")
    except ExportSnapshotExpired:
        return _unavailable(request, status=410, code="EXPORT_SNAPSHOT_EXPIRED")
    except (ExportArtifactError, OSError, SQLAlchemyError):
        return _unavailable(request)
    model = {
        "locations": ExportLocation,
        "current": ExportCurrentValue,
        "forecasts": ExportForecastGroup,
        "daily-summaries": ExportDailySummary,
    }[resource]
    page = ExportPage[model].model_validate(page).model_dump(mode="json")
    headers = {
        "etag": etag,
        "vary": "Accept-Encoding",
        "cache-control": "private, max-age=0, must-revalidate",
        "x-source-status": page["meta"]["source_status"],
    }
    if _matches_etag(request.headers.get("if-none-match", ""), etag):
        return Response(status_code=304, headers=headers)
    payload = json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()
    if len(payload) >= 512 and _accepts_gzip(request.headers.get("accept-encoding", "")):
        payload = gzip.compress(payload, compresslevel=4, mtime=0)
        headers["content-encoding"] = "gzip"
    return Response(payload, media_type="application/json", headers=headers)


@router.get("/locations", response_model=ExportPage[ExportLocation])
async def export_locations(
    request: Request,
    limit: Limit = 500,
    cursor: Cursor = None,
) -> JSONResponse:
    """활성 catalog 전체 reference. 원천 조회 시각 미확인은 null/stale로 표시한다."""
    return await _frozen_response(request, "locations", limit, cursor)


@router.get("/current", response_model=ExportPage[ExportCurrentValue])
async def export_current(
    request: Request,
    limit: Limit = 500,
    cursor: Cursor = None,
    provider: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
) -> Response:
    """활성 관측 제공처의 실제 승인 합집합. 필수 source 최초 미승인은503이다.

    해양처럼 예보만 있는 제공처와 catalog dataset은 제외한다. quota 밖 이전 승인은
    보존하고 실패·부분 응답·미확인 청정넷 coverage는 stale로 표시한다.
    """
    return await _frozen_response(request, "current", limit, cursor, provider=provider)


@router.get("/status", response_model=ExportPage[ExportSourceStatus])
async def export_status(
    request: Request,
) -> Response:
    """최대 128개의 작은 상태 목록. 전체를 한 응답으로 반환한다."""
    settings = request.app.state.settings
    if not settings.service_exports_enabled:
        return _unavailable(request)
    # 과거 미구현 계약의 cursor/limit을 조용히 무시하지 않는다.
    if request.query_params:
        return _unavailable(request, status=422, code="EXPORT_STATUS_QUERY_INVALID")
    try:
        page, etag = await to_thread.run_sync(
            partial(
                read_status,
                request.app.state.repository,
                set(settings.enabled_providers),
                as_of=kst_now(),
            ),
            limiter=request.app.state.service_export_limiter,
        )
    except (ExportArtifactError, SQLAlchemyError):
        return _unavailable(request)
    body = ExportPage[ExportSourceStatus].model_validate(page).model_dump(mode="json")
    headers = {
        "etag": etag,
        "vary": "Accept-Encoding",
        "cache-control": "private, max-age=0, must-revalidate",
        "x-source-status": body["meta"]["source_status"],
    }
    if _matches_etag(request.headers.get("if-none-match", ""), etag):
        return Response(status_code=304, headers=headers)
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    if _accepts_gzip(request.headers.get("accept-encoding", "")):
        payload = gzip.compress(payload, compresslevel=4, mtime=0)
        headers["content-encoding"] = "gzip"
    return Response(payload, media_type="application/json", headers=headers)


@router.get("/forecasts", response_model=ExportPage[ExportForecastGroup])
async def export_forecasts(
    request: Request,
    style: Annotated[Literal["ultra_short", "short", "mid", "external"], Query()],
    limit: Limit = 500,
    cursor: Cursor = None,
    provider: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    since: Annotated[
        AwareDatetime | None,
        Query(description="공개 publication watermark. 이전 응답 meta.next_since를 사용"),
    ] = None,
) -> Response:
    """활성 예보 제공처의 승인 집합. external에는 KHOA 해양 예보를 포함한다.

    known_at은 원천 시각이며 since에는 모든 페이지 반영 뒤 meta.next_since를 사용한다.
    """
    return await _frozen_response(
        request, "forecasts", limit, cursor, provider=provider, style=style, since=since
    )


@router.get("/daily-summaries", response_model=ExportPage[ExportDailySummary])
async def export_daily_summaries(
    request: Request,
    days: Annotated[int, Query(ge=1, le=31)] = 14,
    location_id: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    limit: Limit = 500,
    cursor: Cursor = None,
) -> Response:
    return await _frozen_response(
        request, "daily-summaries", limit, cursor, days=days, location_id=location_id
    )
