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
    def __init__(self, code: str):
        if code not in ALERT_SAFE_ERROR_CODES:
            raise ValueError("알 수 없는 특보 오류 분류")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AlertCollection:
    records: list[dict[str, Any]]
    requests_fetched: int
    observations: int
    unknown_observations: int


def _integer(raw: object) -> int:
    if isinstance(raw, bool) or not re.fullmatch(r"[0-9]{1,10}", str(raw)):
        raise AlertCollectionError("ALERT_PAGE_METADATA")
    return int(str(raw))


def _page(body: Mapping[str, Any], page_no: int) -> tuple[list[dict[str, Any]], int]:
    total = _integer(body.get("totalCount"))
    returned_page = _integer(body.get("pageNo"))
    size = _integer(body.get("numOfRows"))
    if returned_page != page_no or size != PAGE_SIZE and not (total == 0 and size == 0):
        raise AlertCollectionError("ALERT_PAGE_METADATA")
    if total > PAGE_SIZE * MAX_PAGES:
        raise AlertCollectionError("ALERT_TOO_MANY_ROWS")
    items = body.get("items")
    raw = items.get("item") if isinstance(items, Mapping) else None
    if raw in (None, "") or raw == {}:
        rows = []
    elif isinstance(raw, Mapping):
        rows = [dict(raw)]
    elif isinstance(raw, list) and all(isinstance(row, Mapping) for row in raw):
        rows = [dict(row) for row in raw]
    else:
        raise AlertCollectionError("ALERT_PAGE_ROWS")
    expected = min(PAGE_SIZE, max(0, total - (page_no - 1) * PAGE_SIZE))
    if len(rows) != expected:
        raise AlertCollectionError("ALERT_PAGE_ROWS")
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
        body, _metadata = await client.request_with_metadata(
            "WthrWrnInfoService",
            "getPwnCd",
            query,
            page_no=page_no,
            num_of_rows=PAGE_SIZE,
        )
        requests += 1
        rows, total = _page(body, page_no)
        if expected_total is not None and total != expected_total:
            raise AlertCollectionError("ALERT_PAGE_TOTAL_CHANGED")
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
            raise AlertCollectionError("ALERT_RESPONSE_TOO_LARGE")
        for raw in rows:
            event = normalize_alert(raw)
            if event.event_id in seen:
                raise AlertCollectionError("ALERT_PAGE_DUPLICATE")
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
            raise AlertCollectionError("ALERT_FIRST_PAGE_CHANGED")
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
            collected = await collect_alert_pages(client, heartbeat=heartbeat)
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
        raise AlertCollectionError(code) from None
    finally:
        await _safe_close(client)


def run_alert_event_sync(
    *,
    repository: WeatherRepository,
    client: Any,
) -> dict[str, object]:
    return asyncio.run(run_alert_event_sync_async(repository=repository, client=client))
