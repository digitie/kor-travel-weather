"""Map 전용 export 계약. 성공 head 구현 전에는 빈 성공 대신 503을 반환한다."""

from __future__ import annotations

from typing import Annotated, Generic, Literal, TypeVar

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

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
    legal_dong_code: str | None = Field(default=None, pattern=r"^\d{10}$")
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
    responses={503: {
        "model": ExportUnavailable,
        "description": "완결된 성공 export head가 아직 없습니다. 빈 정상 집합으로 해석하지 마세요.",
    }},
)
Limit = Annotated[int, Query(ge=1, le=5000)]
Cursor = Annotated[str | None, Query(min_length=1, max_length=4096)]


def _unavailable(request: Request) -> JSONResponse:
    # mutable current의 부분 commit을 성공 snapshot으로 공개하지 않는다.
    # 다음 단계의 receipt·terminal 성공 승격 전에는 DB/provider도 조회하지 않는다.
    body = ExportUnavailable(
        title="서비스 export 준비 중",
        status=503,
        detail="완결된 성공 export 집합이 아직 없습니다.",
        code="EXPORT_NOT_READY",
        request_id=request_id(request),
        meta=ExportMeta(
            generated_at=kst_now(), source_collected_at=None,
            source_status="failed", item_count=0,
        ),
    )
    return JSONResponse(
        status_code=503,
        content=body.model_dump(mode="json"),
        media_type="application/problem+json",
        headers={"cache-control": "no-store, private", "x-source-status": "failed"},
    )


@router.get("/locations", response_model=ExportPage[ExportLocation])
async def export_locations(
    request: Request, limit: Limit = 500, cursor: Cursor = None,
) -> JSONResponse:
    return _unavailable(request)


@router.get("/current", response_model=ExportPage[ExportCurrentValue])
async def export_current(
    request: Request, limit: Limit = 500, cursor: Cursor = None,
) -> JSONResponse:
    return _unavailable(request)


@router.get("/status", response_model=ExportPage[ExportSourceStatus])
async def export_status(
    request: Request, limit: Limit = 500, cursor: Cursor = None,
) -> JSONResponse:
    return _unavailable(request)
