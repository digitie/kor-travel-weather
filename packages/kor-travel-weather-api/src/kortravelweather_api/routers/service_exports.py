"""Map 전용 export 계약. 성공 head 구현 전에는 빈 성공 대신 503을 반환한다."""

from __future__ import annotations

import gzip
import json
from functools import partial
from typing import Annotated, Generic, Literal, TypeVar

from anyio import to_thread
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from kortravelweather.export_artifacts import ExportArtifactError, ExportArtifactStore
from kortravelweather.export_snapshots import (
    ExportCursorInvalid,
    ExportSnapshotExpired,
    FrozenCurrentExports,
)
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
    forecast_style: ExportStyle
    last_success_at: AwareDatetime | None
    last_failed_at: AwareDatetime | None
    last_attempt_failed: bool
    next_scheduled_at: AwareDatetime | None


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
    request: Request, resource: str, limit: int, cursor: str | None, *, provider: str | None = None
) -> Response:
    settings = request.app.state.settings
    if (
        not settings.service_exports_enabled
        or settings.service_export_artifact_root is None
        or settings.admin_token is None
    ):
        return _unavailable(request)
    # 지원하지 않는 provider를 누락한 작은 집합을 전체 정상으로 내보내지 않는다.
    if resource == "current":
        required = {provider} if provider else set(settings.enabled_providers)
        if required != {"python-kma-api"}:
            if cursor:
                return _unavailable(request, status=422, code="EXPORT_CURSOR_INVALID")
            return _unavailable(request)
    try:
        reader = FrozenCurrentExports(
            request.app.state.repository,
            ExportArtifactStore(settings.service_export_artifact_root),
            signing_key=settings.admin_token.get_secret_value(),
        )
        page, etag = await to_thread.run_sync(
            partial(reader.page, resource, limit=limit, cursor=cursor),
            limiter=request.app.state.service_export_limiter,
        )
    except ExportCursorInvalid:
        return _unavailable(request, status=422, code="EXPORT_CURSOR_INVALID")
    except ExportSnapshotExpired:
        return _unavailable(request, status=410, code="EXPORT_SNAPSHOT_EXPIRED")
    except (ExportArtifactError, OSError, SQLAlchemyError):
        return _unavailable(request)
    model = ExportLocation if resource == "locations" else ExportCurrentValue
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
    return await _frozen_response(request, "locations", limit, cursor)


@router.get("/current", response_model=ExportPage[ExportCurrentValue])
async def export_current(
    request: Request,
    limit: Limit = 500,
    cursor: Cursor = None,
    provider: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
) -> Response:
    return await _frozen_response(request, "current", limit, cursor, provider=provider)


@router.get("/status", response_model=ExportPage[ExportSourceStatus])
async def export_status(
    request: Request,
    limit: Limit = 500,
    cursor: Cursor = None,
) -> JSONResponse:
    return _unavailable(request)
