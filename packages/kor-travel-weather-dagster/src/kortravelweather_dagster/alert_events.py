"""공개 SDK의 getPwnCd 응답을 완결 검사한 뒤 짧은 transaction으로 게시한다.

한 번의 요청(pageNo=1, numOfRows=ALERT_MAX_ROWS)으로 요청 구간 전체를 받는다.
2026-11-24 실측: getPwnCd는 같은 발표 시각(tmFc) 행 사이의 순서가 요청마다 고정되지 않아
100행 페이지로 나누면 페이지 경계의 행이 다음 페이지에 다시 나오고 그만큼 다른 행이 빠졌다
(381행 중 33행 반복·33행 누락). 한 페이지 요청은 같은 구간에서 381행을 중복 없이 모두
돌려줬다. 페이지 경계 반복을 병합만 하면 누락이 숨으므로 페이지 분할 자체를 하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from kma.exceptions import KmaError

from kortravelweather.alert_events import (
    ALERT_DATASET,
    ALERT_MAX_ROWS,
    ALERT_PROVIDER,
    ALERT_SAFE_ERROR_CODES,
    ALERT_VERSION,
    canonical_hash,
    quarantine_limit,
    reconcile_alert_rows,
)
from kortravelweather.metrics import observe_alert_cleanup_failure, observe_alert_rows_reconciled
from kortravelweather.models import KST, kst_now
from kortravelweather.repository import SyncRunAlreadyActive, WeatherRepository

#: 한 요청의 numOfRows. 요청 구간 전체가 한 페이지에 들어와야 한다(모듈 설명).
PAGE_SIZE = ALERT_MAX_ROWS
MAX_BYTES = 8 * 1024 * 1024
#: 격리한 특보구역 코드를 metadata에 남길 때의 상한(관리자 화면용 요약).
QUARANTINE_AREA_SAMPLE = 20
_AREA_CODE = re.compile(r"[LS][0-9]{7}")
COLLECTION_TIMEOUT_SECONDS = 180
CLEANUP_TIMEOUT_SECONDS = 5
TERMINAL_TIMEOUT_MS = 5000
logger = logging.getLogger(__name__)


class AlertCollectionError(RuntimeError):
    def __init__(self, code: str, *, diagnostics: Mapping[str, Any] | None = None):
        if code not in ALERT_SAFE_ERROR_CODES:
            raise ValueError("알 수 없는 특보 오류 분류")
        self.code = code
        # 제공기관 원문/예외 문자열은 받지 않는다. 관리자 진단도 고정 식별자와
        # 개수/상태 코드만 전달하여 Dagster metadata에 credential이 섞이지 않게 한다.
        choices = {
            "stage": {"run_start", "page_request", "page_validation", "publication"},
            "metadata_field": {"totalCount", "pageNo", "numOfRows"},
            "metadata_type": {"null", "boolean", "integer", "number", "string", "object", "array"},
            "rate_limit_scope": {"daily", "per_second"},
            "recovery_hint": {"WAIT_QUOTA_RESET", "RETRY_AFTER_BACKOFF"},
        }
        numeric = {
            "requested_page", "returned_page", "page_size", "total_count",
            "expected_rows", "returned_rows", "requests_fetched", "http_status",
            "quarantined_rows", "quarantine_limit",
        }
        self.diagnostics: dict[str, str | int | bool] = {"error_code": code}
        for key, value in (diagnostics or {}).items():
            if (
                key in choices and isinstance(value, str) and value in choices[key]
                or key in numeric and type(value) is int and 0 <= value <= 10_000_000
                or key == "retryable" and type(value) is bool
            ):
                self.diagnostics[key] = value
        super().__init__(code)


@dataclass(frozen=True)
class AlertCollection:
    records: list[dict[str, Any]]
    requests_fetched: int
    observations: int
    unknown_observations: int
    duplicates_merged: int = 0
    quarantined_events: int = 0
    quarantined_rows: int = 0
    quarantined_area_codes: tuple[str, ...] = ()


def _integer(raw: object, *, field: str) -> int:
    if isinstance(raw, bool) or not re.fullmatch(r"[0-9]{1,10}", str(raw)):
        kind = (
            "null" if raw is None else "boolean" if isinstance(raw, bool)
            else "integer" if isinstance(raw, int) else "number" if isinstance(raw, float)
            else "string" if isinstance(raw, str) else "array" if isinstance(raw, list)
            else "object"
        )
        raise AlertCollectionError("ALERT_PAGE_METADATA", diagnostics={
            "stage": "page_validation", "metadata_field": field, "metadata_type": kind,
        })
    return int(str(raw))


def _page(body: Mapping[str, Any], page_no: int) -> tuple[list[dict[str, Any]], int]:
    total = _integer(body.get("totalCount"), field="totalCount")
    items = body.get("items")
    # SDK는 정상 NO_DATA(03)를 이 모양으로 정규화하며 원문에 없는 pageNo/
    # numOfRows를 만들지 않는다. 첫 빈 결과만 수용하고 누락된 비어 있지 않은
    # 페이지나 자료/개수 불일치는 아래의 기존 완결 검증에서 계속 거절한다.
    if (
        total == 0
        and page_no == 1
        and body.get("pageNo") is None
        and body.get("numOfRows") is None
        and isinstance(items, Mapping)
        and items.get("item") == []
    ):
        return [], 0
    returned_page = _integer(body.get("pageNo"), field="pageNo")
    size = _integer(body.get("numOfRows"), field="numOfRows")
    metadata = {
        "stage": "page_validation", "requested_page": page_no,
        "returned_page": returned_page, "page_size": size, "total_count": total,
    }
    if returned_page != page_no or size != PAGE_SIZE and not (total == 0 and size == 0):
        raise AlertCollectionError("ALERT_PAGE_METADATA", diagnostics=metadata)
    if total > PAGE_SIZE:
        # 두 번째 페이지는 요청하지 않는다. 원천 순서가 고정되지 않아 페이지를 나누면
        # 행이 반복·누락된다(모듈 설명). 상한을 넘으면 일부만 게시하지 않고 실패한다.
        raise AlertCollectionError("ALERT_TOO_MANY_ROWS", diagnostics=metadata)
    raw = items.get("item") if isinstance(items, Mapping) else None
    if raw in (None, "") or raw == {}:
        rows = []
    elif isinstance(raw, Mapping):
        rows = [dict(raw)]
    elif isinstance(raw, list) and all(isinstance(row, Mapping) for row in raw):
        rows = [dict(row) for row in raw]
    else:
        raise AlertCollectionError("ALERT_PAGE_ROWS", diagnostics=metadata)
    if len(rows) != total:
        raise AlertCollectionError("ALERT_PAGE_ROWS", diagnostics={
            **metadata, "expected_rows": total, "returned_rows": len(rows),
        })
    return rows, total


async def collect_alert_pages(
    client: Any,
    *,
    when: datetime | None = None,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
) -> AlertCollection:
    when = when or kst_now()
    if when.tzinfo is None:
        raise ValueError("특보 조회 시각은 timezone-aware여야 합니다.")
    when = when.astimezone(KST)
    query = {
        "stnId": "108",
        "fromTmFc": (when - timedelta(days=3)).strftime("%Y%m%d"),
        "toTmFc": when.strftime("%Y%m%d"),
    }
    if heartbeat:
        await heartbeat()
    try:
        body, _metadata = await client.request_with_metadata(
            "WthrWrnInfoService",
            "getPwnCd",
            query,
            page_no=1,
            num_of_rows=PAGE_SIZE,
        )
    except KmaError as exc:
        raise AlertCollectionError(safe_collection_error(exc), diagnostics={
            "stage": "page_request", "requested_page": 1, "requests_fetched": 0,
            **({"http_status": exc.status_code} if type(exc.status_code) is int else {}),
            **_rate_limit_diagnostics(exc),
        }) from None
    progress = {"stage": "page_validation", "requested_page": 1, "requests_fetched": 1}
    try:
        rows, total = _page(body, 1)
    except AlertCollectionError as exc:
        raise AlertCollectionError(exc.code, diagnostics={
            **exc.diagnostics, "requested_page": 1, "requests_fetched": 1,
        }) from None
    reconciled = reconcile_alert_rows(rows)
    limit = quarantine_limit(len(rows))
    if reconciled.quarantined_rows > limit:
        # 일부 격리는 정상 진행하지만, 이만큼 충돌하면 원천 응답 자체를 믿을 수 없다.
        raise AlertCollectionError("ALERT_QUARANTINE_LIMIT", diagnostics={
            **progress, "total_count": total, "returned_rows": len(rows),
            "quarantined_rows": reconciled.quarantined_rows, "quarantine_limit": limit,
        })
    payload = {
        "rows": reconciled.accepted,
        # 게시하지 않은 원천 행을 그대로 남긴다(격리 기록). 게시는 ``rows``만 읽는다.
        "quarantined": [
            {"event_id": group.event_id, "rows": list(group.rows)}
            for group in reconciled.quarantined
        ],
        "duplicates_merged": reconciled.duplicates_merged,
        "request": {**query, "pageNo": 1, "numOfRows": PAGE_SIZE},
        "pagination": {"totalCount": total, "pageNo": 1, "numOfRows": PAGE_SIZE},
        "normalization_version": ALERT_VERSION,
    }
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()) > MAX_BYTES:
        raise AlertCollectionError("ALERT_RESPONSE_TOO_LARGE", diagnostics=progress)
    record = {
        "source_record_key": "was_" + canonical_hash(payload),
        "provider": ALERT_PROVIDER,
        "dataset_key": ALERT_DATASET,
        "source_entity_type": "weather_response",
        "source_entity_id": "getPwnCd:108",
        "payload": payload,
        "fetched_at": kst_now(),
    }
    areas = sorted({
        group.area_code for group in reconciled.quarantined
        if _AREA_CODE.fullmatch(group.area_code)
    })
    return AlertCollection(
        [record],
        1,
        len(reconciled.events),
        sum(event.action == "unknown" for event in reconciled.events),
        duplicates_merged=reconciled.duplicates_merged,
        quarantined_events=len(reconciled.quarantined),
        quarantined_rows=reconciled.quarantined_rows,
        quarantined_area_codes=tuple(areas[:QUARANTINE_AREA_SAMPLE]),
    )


def safe_collection_error(exc: Exception) -> str:
    if isinstance(exc, AlertCollectionError):
        return exc.code
    if isinstance(exc, TimeoutError):
        return "ALERT_TIMEOUT"
    if isinstance(exc, KmaError):
        if exc.failure_kind == "auth" or exc.status_code in {401, 403}:
            return "ALERT_PROVIDER_AUTH"
        if exc.failure_kind in {"quota", "rate_limit"} or exc.status_code == 429:
            return "ALERT_PROVIDER_RATE_LIMIT"
        return "ALERT_PROVIDER_UNAVAILABLE"
    if isinstance(exc, ValueError) and str(exc) == "ALERT_RUN_OWNERSHIP_LOST":
        return "ALERT_RUN_OWNERSHIP_LOST"
    return "ALERT_COLLECTION_FAILED"


def _rate_limit_diagnostics(exc: KmaError) -> dict[str, str | bool]:
    """SDK 분류만 사용한다. resultCode·메시지·URL을 앱에서 재해석하지 않는다."""
    if safe_collection_error(exc) != "ALERT_PROVIDER_RATE_LIMIT":
        return {}
    daily = exc.failure_kind == "quota"
    return {
        "rate_limit_scope": "daily" if daily else "per_second",
        "retryable": not daily,
        "recovery_hint": "WAIT_QUOTA_RESET" if daily else "RETRY_AFTER_BACKOFF",
    }


def _report_reconciliation(collected: AlertCollection) -> None:
    """게시가 커밋된 뒤의 관측만 남긴다. 실패해도 커밋된 성공을 뒤집지 않는다."""
    with suppress(Exception):
        observe_alert_rows_reconciled(
            merged=collected.duplicates_merged, quarantined=collected.quarantined_rows,
        )
    if collected.quarantined_rows:
        with suppress(Exception):
            logger.warning(
                "ALERT_ROWS_QUARANTINED events=%d rows=%d areas=%s",
                collected.quarantined_events,
                collected.quarantined_rows,
                ",".join(collected.quarantined_area_codes),
            )


def _cleanup_notice(stage: str) -> None:
    # 원문/exception chain은 기록하지 않고 고정 stage만 관측한다.
    with suppress(Exception):
        observe_alert_cleanup_failure(stage)
    with suppress(Exception):
        logger.warning("ALERT_CLEANUP_DEFERRED stage=%s", stage)


async def _safe_terminalize(
    repository: WeatherRepository,
    run_id: str,
    *,
    status: str,
    error: str,
) -> None:
    try:
        await asyncio.to_thread(
            repository.finish_sync_run,
            run_id,
            status=status,
            error=error,
            timeout_ms=TERMINAL_TIMEOUT_MS,
        )
    except Exception:
        # DB 종결이 불가능하면 기존 lease/orchestrator 회수가 처리한다.
        _cleanup_notice("terminalize")


async def _safe_close(client: Any) -> None:
    try:
        async with asyncio.timeout(CLEANUP_TIMEOUT_SECONDS):
            await client.aclose()
    except Exception:
        # 성공한 commit·정제 오류·원래 취소를 cleanup 장애로 뒤집지 않는다.
        _cleanup_notice("client_close")


async def run_alert_event_sync_async(
    *,
    repository: WeatherRepository,
    client: Any,
) -> dict[str, object]:
    run = None
    start_task = None
    stage = "run_start"
    try:
        start_task = asyncio.create_task(
            asyncio.to_thread(
                repository.start_sync_run,
                provider=ALERT_PROVIDER,
                dataset_key=ALERT_DATASET,
            )
        )
        run = await asyncio.shield(start_task)

        run_id = run.run_id

        async def heartbeat() -> None:
            if not await asyncio.to_thread(repository.heartbeat_sync_run, run_id):
                raise AlertCollectionError("ALERT_RUN_OWNERSHIP_LOST")

        async with asyncio.timeout(COLLECTION_TIMEOUT_SECONDS):
            stage = "page_request"
            collected = await collect_alert_pages(client, heartbeat=heartbeat)
        stage = "publication"
        loaded, finished = await asyncio.to_thread(
            repository.publish_alert_events,
            run_id=run.run_id,
            source_records=collected.records,
            requests_fetched=collected.requests_fetched,
        )
        _report_reconciliation(collected)
        return {
            "status": finished.status,
            "run_id": finished.run_id,
            "events_loaded": loaded,
            "observations": collected.observations,
            "unknown_observations": collected.unknown_observations,
            "requests_fetched": collected.requests_fetched,
            "pages_complete": True,
            "duplicates_merged": collected.duplicates_merged,
            "quarantined_events": collected.quarantined_events,
            "quarantined_rows": collected.quarantined_rows,
            # 고정 형식의 특보구역 코드만 남긴다(원천 지역명·원문 없음).
            "quarantined_area_codes": ",".join(collected.quarantined_area_codes),
            "collection_warning": (
                "ALERT_ROWS_QUARANTINED" if collected.quarantined_rows else ""
            ),
        }
    except asyncio.CancelledError:
        # DB 시작 thread는 task 취소로 중단되지 않는다. 결과 회수 뒤 실행을 봉인한다.
        if run is None and start_task is not None:
            with suppress(Exception):
                run = await asyncio.shield(start_task)
        if run is not None:
            await _safe_terminalize(
                repository,
                run.run_id,
                status="cancelled",
                error="ALERT_COLLECTION_CANCELLED",
            )
        raise
    except SyncRunAlreadyActive:
        raise
    except Exception as exc:
        code = safe_collection_error(exc)
        if run is not None:
            await _safe_terminalize(repository, run.run_id, status="failed", error=code)
        # 원문 KMA 오류가 가진 URL·credential은 Dagster 예외 chain에도 남기지 않는다.
        diagnostics = (
            exc.diagnostics if isinstance(exc, AlertCollectionError) else {"stage": stage}
        )
        if isinstance(exc, KmaError) and type(exc.status_code) is int:
            diagnostics = {**diagnostics, "http_status": exc.status_code}
        if isinstance(exc, KmaError):
            diagnostics = {**diagnostics, **_rate_limit_diagnostics(exc)}
        raise AlertCollectionError(code, diagnostics=diagnostics) from None
    finally:
        await _safe_close(client)


def run_alert_event_sync(
    *,
    repository: WeatherRepository,
    client: Any,
) -> dict[str, object]:
    return asyncio.run(run_alert_event_sync_async(repository=repository, client=client))
