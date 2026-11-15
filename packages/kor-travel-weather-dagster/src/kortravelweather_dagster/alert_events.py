"""공개 SDK의 getPwnCd 페이지를 완결 검사한 뒤 짧은 transaction으로 게시한다."""

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
    ALERT_PROVIDER,
    ALERT_SAFE_ERROR_CODES,
    ALERT_VERSION,
    canonical_hash,
    normalize_alert,
)
from kortravelweather.metrics import observe_alert_cleanup_failure
from kortravelweather.models import KST, kst_now
from kortravelweather.repository import SyncRunAlreadyActive, WeatherRepository

PAGE_SIZE = 100
MAX_PAGES = 100
MAX_BYTES = 8 * 1024 * 1024
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
    if total > PAGE_SIZE * MAX_PAGES:
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
    expected = min(PAGE_SIZE, max(0, total - (page_no - 1) * PAGE_SIZE))
    if len(rows) != expected:
        raise AlertCollectionError("ALERT_PAGE_ROWS", diagnostics={
            **metadata, "expected_rows": expected, "returned_rows": len(rows),
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
    records = []
    first_fingerprint = None
    expected_total = None
    seen = set()
    bytes_seen = 0
    unknown = 0
    requests = 0
    for page_no in range(1, MAX_PAGES + 1):
        if heartbeat:
            await heartbeat()
        try:
            body, _metadata = await client.request_with_metadata(
                "WthrWrnInfoService",
                "getPwnCd",
                query,
                page_no=page_no,
                num_of_rows=PAGE_SIZE,
            )
        except KmaError as exc:
            raise AlertCollectionError(safe_collection_error(exc), diagnostics={
                "stage": "page_request", "requested_page": page_no,
                "requests_fetched": requests,
                **({"http_status": exc.status_code} if type(exc.status_code) is int else {}),
                **_rate_limit_diagnostics(exc),
            }) from None
        requests += 1
        progress = {
            "stage": "page_validation", "requested_page": page_no,
            "requests_fetched": requests,
        }
        try:
            rows, total = _page(body, page_no)
        except AlertCollectionError as exc:
            raise AlertCollectionError(exc.code, diagnostics={
                **exc.diagnostics, "requested_page": page_no, "requests_fetched": requests,
            }) from None
        if expected_total is not None and total != expected_total:
            raise AlertCollectionError("ALERT_PAGE_TOTAL_CHANGED", diagnostics={
                **progress, "total_count": total,
            })
        expected_total = total
        fingerprint = sorted(canonical_hash(row) for row in rows)
        if page_no == 1:
            first_fingerprint = fingerprint
        payload = {
            "rows": rows,
            "request": {**query, "pageNo": page_no, "numOfRows": PAGE_SIZE},
            "pagination": {"totalCount": total, "pageNo": page_no, "numOfRows": PAGE_SIZE},
            "normalization_version": ALERT_VERSION,
        }
        bytes_seen += len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode())
        if bytes_seen > MAX_BYTES:
            raise AlertCollectionError("ALERT_RESPONSE_TOO_LARGE", diagnostics=progress)
        for raw in rows:
            event = normalize_alert(raw)
            if event.event_id in seen:
                raise AlertCollectionError("ALERT_PAGE_DUPLICATE", diagnostics={
                    **progress, "returned_rows": len(rows), "total_count": total,
                })
            seen.add(event.event_id)
            unknown += event.action == "unknown"
        records.append(
            {
                "source_record_key": "was_" + canonical_hash(payload),
                "provider": ALERT_PROVIDER,
                "dataset_key": ALERT_DATASET,
                "source_entity_type": "weather_response",
                "source_entity_id": "getPwnCd:108",
                "payload": payload,
                "fetched_at": kst_now(),
            }
        )
        if len(seen) == total:
            break
    else:
        raise AlertCollectionError("ALERT_TOO_MANY_ROWS")
    if len(records) > 1:
        if heartbeat:
            await heartbeat()
        body, _metadata = await client.request_with_metadata(
            "WthrWrnInfoService",
            "getPwnCd",
            query,
            page_no=1,
            num_of_rows=PAGE_SIZE,
        )
        requests += 1
        rows, total = _page(body, 1)
        if (
            total != expected_total
            or sorted(canonical_hash(row) for row in rows) != first_fingerprint
        ):
            raise AlertCollectionError("ALERT_FIRST_PAGE_CHANGED", diagnostics={
                "stage": "page_validation", "requested_page": 1,
                "requests_fetched": requests, "total_count": total,
            })
    return AlertCollection(records, requests, len(seen), unknown)


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
        return {
            "status": finished.status,
            "run_id": finished.run_id,
            "events_loaded": loaded,
            "observations": collected.observations,
            "unknown_observations": collected.unknown_observations,
            "requests_fetched": collected.requests_fetched,
            "pages_complete": True,
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
