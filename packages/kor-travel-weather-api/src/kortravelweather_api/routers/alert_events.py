"""행정구역 근거가 확인된 공식 발표 관측. 활성 상태/해제를 빈 목록에서 추정하지 않는다."""

from datetime import datetime, timedelta
from time import perf_counter
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from kortravelweather.alert_events import AlertEvent
from kortravelweather.models import kst_now
from kortravelweather.repository import WeatherRepository

from ..response import Envelope, envelope
from .weather import repository

router = APIRouter(prefix="/v1/weather", tags=["weather"])


class ScopedAlertEventOut(AlertEvent):
    first_seen_at: datetime
    source_record_key: str
    scope_status: Literal["verified"]
    bjd_prefixes: list[str]


class AlertEventsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: Literal["kr_bjd"]
    bjd_code: str
    region_status: Literal["known", "unknown"]
    mapping_version: str
    mapping_valid_from: datetime
    mapping_coverage: Literal["partial"]
    unverified_scope_count: int
    collection_status: str
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_error_code: str | None
    fresh: bool
    events_complete: bool
    retained_since: datetime
    history_available_since: datetime | None
    queried_since: datetime
    absence_is_clear: Literal[False]
    events: list[ScopedAlertEventOut]


@router.get(
    "/alert-events",
    response_model=Envelope[AlertEventsOut],
    summary="공식 기상특보 발표 관측 조회",
    description=(
        "BJD namespace의 시도/시군구/검증된 개별 코드로 전체 행정구역 교집합을 조회한다. "
        "확인 전 과거 매핑·산지·일부/제외 구역은 추정하지 않는다. 관측 부재는 해제가 아니다. "
        "since는 최초 관측 시각 기준이며 31일 이내다. 이벤트의 회차 연결은 미확정이다."
    ),
)
async def alert_events(
    request: Request,
    repo: Annotated[WeatherRepository, Depends(repository)],
    bjd_code: Annotated[
        str,
        Query(pattern=r"^(?:[0-9]{2}|[0-9]{5}|[0-9]{10})$", max_length=10),
    ],
    since: datetime | None = None,
) -> dict:
    started = perf_counter()
    since = since if since is not None else kst_now() - timedelta(days=3)
    try:
        data = await run_in_threadpool(repo.read_alert_events, bjd_code=bjd_code, since=since)
    except ValueError as exc:
        if str(exc) != "ALERT_HISTORY_WINDOW":
            raise
        raise HTTPException(
            422, detail="ALERT_HISTORY_WINDOW: timezone-aware 최근31일 조회가 필요합니다."
        ) from None
    return envelope(request, started, data)
