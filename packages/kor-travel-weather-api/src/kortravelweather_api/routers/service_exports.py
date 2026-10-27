"""Map 전용 export 계약. 성공 head 구현 전에는 빈 성공 대신 503을 반환한다."""

from __future__ import annotations

import gzip
import json
import logging
from collections.abc import Callable
from datetime import date
from functools import partial
from typing import Annotated, Any, Generic, Literal, TypeVar

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

logger = logging.getLogger(__name__)

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


_PAGE_MODELS: dict[str, Any] = {
    "locations": ExportPage[ExportLocation],
    "current": ExportPage[ExportCurrentValue],
    "forecasts": ExportPage[ExportForecastGroup],
    "daily-summaries": ExportPage[ExportDailySummary],
    "status": ExportPage[ExportSourceStatus],
}


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


def _log_unavailable(request: Request, resource: str, reason: str) -> None:
    # 원인 분류(예외 클래스·source_selection)만 남긴다. 메시지·SQL·DSN·토큰은 남기지 않는다.
    rid = "".join(c if c.isprintable() else "?" for c in request_id(request)[:64])
    logger.warning(
        "weather_service_export_unavailable resource=%s reason=%s request_id=%s",
        resource, reason, rid,
    )


def _render_page(
    read: Callable[[], tuple[dict[str, Any], str]],
    model: Any,
    if_none_match: str,
    accept_encoding: str,
    *,
    gzip_minimum: int,
) -> tuple[bytes | None, dict[str, str]]:
    """worker thread에서 조회·검증·ETag 비교·직렬화·gzip을 끝낸다.

    최대4MiB 페이지의 검증·직렬화·압축이 event loop(일반 API·관리 proxy·health)를
    막지 않게 한다. 304이면 payload는 None이다.
    """
    page, etag = read()
    body = model.model_validate(page).model_dump(mode="json")
    headers = {
        "etag": etag,
        "vary": "Accept-Encoding",
        "cache-control": "private, max-age=0, must-revalidate",
        "x-source-status": body["meta"]["source_status"],
    }
    if _matches_etag(if_none_match, etag):
        return None, headers
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    if len(payload) >= gzip_minimum and _accepts_gzip(accept_encoding):
        payload = gzip.compress(payload, compresslevel=4, mtime=0)
        headers["content-encoding"] = "gzip"
    return payload, headers


def _rendered(payload: bytes | None, headers: dict[str, str]) -> Response:
    if payload is None:
        return Response(status_code=304, headers=headers)
    return Response(payload, media_type="application/json", headers=headers)


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
    if resource in {"current", "forecasts", "daily-summaries"}:
        try:
            selection = select_export_sources(
                settings.enabled_providers, resource=resource, style=style, provider=provider,
            )
        except ExportArtifactError:
            _log_unavailable(request, resource, "source_selection")
            if cursor:
                return _unavailable(request, status=422, code="EXPORT_CURSOR_INVALID")
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
        payload, headers = await to_thread.run_sync(
            partial(
                _render_page,
                partial(
                    reader.page,
                    *([] if resource in {"forecasts", "daily-summaries"} else [resource]),
                    limit=limit,
                    cursor=cursor,
                ),
                _PAGE_MODELS[resource],
                request.headers.get("if-none-match", ""),
                request.headers.get("accept-encoding", ""),
                gzip_minimum=512,
            ),
            limiter=request.app.state.service_export_limiter,
        )
    except ExportCursorInvalid as exc:
        _log_unavailable(request, resource, type(exc).__name__)
        return _unavailable(request, status=422, code="EXPORT_CURSOR_INVALID")
    except ExportSnapshotExpired:
        return _unavailable(request, status=410, code="EXPORT_SNAPSHOT_EXPIRED")
    except (ExportArtifactError, OSError, SQLAlchemyError) as exc:
        _log_unavailable(request, resource, type(exc).__name__)
        return _unavailable(request)
    return _rendered(payload, headers)


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
    보존하고 실패·부분 응답·미확인 청정넷 coverage는 stale로 표시한다. export 경로가
    없는 provider가 활성이면 축소 성공 대신503이다.
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
        payload, headers = await to_thread.run_sync(
            partial(
                _render_page,
                partial(
                    read_status,
                    request.app.state.repository,
                    set(settings.enabled_providers),
                    as_of=kst_now(),
                ),
                _PAGE_MODELS["status"],
                request.headers.get("if-none-match", ""),
                request.headers.get("accept-encoding", ""),
                gzip_minimum=0,
            ),
            limiter=request.app.state.service_export_limiter,
        )
    except (ExportArtifactError, SQLAlchemyError) as exc:
        _log_unavailable(request, "status", type(exc).__name__)
        return _unavailable(request)
    return _rendered(payload, headers)


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
    ultra_short·short·mid는 KMA만 공급하므로 다른 활성 provider와 무관하고, external은
    export 경로가 없는 provider가 활성이면503이다. 최신 승인 예보에 미래 시각이 없는
    위치는 빼고 stale로 표시한다.
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
    """승인 관측의 사전 요약. 미계산은 이전 완전 집합stale/이전 집합이 없으면503.

    전체 목록에서 한 번도 승인되지 않은 관측 anchor는 stale로 표시하고, 단일 위치 최초
    조회의 필수 원천 미승인만503이다. AirKorea는 요약 지표가 없어 필수 원천이 아니다.
    강수 합계와 강수 없음(false)은 구간6시간 RN1이 모두 있을 때만 확정한다.
    """
    return await _frozen_response(
        request, "daily-summaries", limit, cursor, days=days, location_id=location_id
    )
