"""발표 관측의 원자적 게시와 범위가 명시된 읽기 경계."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .alert_area_mapping import area_mapping, candidate_areas, known_bjd, scope_at
from .alert_events import ALERT_DATASET, ALERT_PROVIDER, AlertEvent, normalize_alert
from .metrics import observe_sync_finished
from .models import SyncRun, kst_now
from .repository import (
    SyncRunRow,
    SyncRunSourceRow,
    WeatherAlertEventRow,
    WeatherRepository,
)

ALERT_RETENTION_DAYS = 31
ALERT_QUERY_LIMIT = 5000


def publish_alert_events(
    repo: WeatherRepository,
    *,
    run_id: str,
    source_records: list[Mapping[str, Any]],
    requests_fetched: int,
) -> tuple[int, SyncRun]:
    if not source_records or len(source_records) > 100:
        raise ValueError("ALERT_PUBLICATION_BOUNDS")
    if requests_fetched < len(source_records):
        raise ValueError("ALERT_PUBLICATION_RECEIPT")
    validated = []
    for record in source_records:
        rows = record["payload"].get("rows")
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("ALERT_SOURCE_ROWS")
        for raw in rows:
            if not isinstance(raw, Mapping):
                raise ValueError("ALERT_SOURCE_ROWS")
            validated.append((normalize_alert(raw), str(record["source_record_key"])))
    if len(validated) > 10000:
        raise ValueError("ALERT_PUBLICATION_BOUNDS")
    if len({event.event_id for event, _ in validated}) != len(validated):
        raise ValueError("ALERT_DUPLICATE_OBSERVATION")

    def step() -> tuple[int, SyncRun]:
        with repo._session_factory.begin() as session:
            repo._set_ingest_lock_timeout(session)
            run = session.scalar(
                select(SyncRunRow).where(SyncRunRow.run_id == run_id).with_for_update()
            )
            if run is None or run.status != "running":
                raise ValueError("ALERT_RUN_OWNERSHIP_LOST")
            if (run.provider, run.dataset_key) != (ALERT_PROVIDER, ALERT_DATASET):
                raise ValueError("ALERT_RUN_SCOPE")
            aliases = {}
            canonical_sources = set()
            for record in source_records:
                if (
                    record["provider"] != ALERT_PROVIDER
                    or record["dataset_key"] != ALERT_DATASET
                    or record.get("run_id", run_id) != run_id
                ):
                    raise ValueError("ALERT_SOURCE_SCOPE")
                key, _ = repo._record_source_session(session, record)
                aliases[str(record["source_record_key"])] = key
                canonical_sources.add(key)
                session.flush()
            for key in sorted(canonical_sources):
                session.add(
                    SyncRunSourceRow(
                        run_id=run_id,
                        source_record_key=key,
                        recorded_at=kst_now(),
                    )
                )
            existing = set(
                session.scalars(
                    select(WeatherAlertEventRow.event_id).where(
                        WeatherAlertEventRow.event_id.in_(
                            [event.event_id for event, _ in validated]
                        )
                    )
                )
            )
            now = kst_now()
            new = []
            for event, source in validated:
                if source not in aliases:
                    raise ValueError("ALERT_SOURCE_REFERENCE")
                if event.event_id not in existing:
                    new.append(
                        WeatherAlertEventRow(
                            event_id=event.event_id,
                            announcement_id=event.announcement_id,
                            area_code=event.area_code,
                            issued_at=event.issued_at,
                            first_seen_at=now,
                            event=event.model_dump(mode="json"),
                            source_record_key=aliases[source],
                        )
                    )
            session.add_all(new)
            session.flush()
            run.status = "success"
            run.finished_at = kst_now()
            run.requests_fetched = requests_fetched
            run.values_loaded = len(new)
            return len(new), repo._sync_model(run)

    loaded, finished = repo._retry_lock_race(step)
    observe_sync_finished(
        ALERT_PROVIDER,
        ALERT_DATASET,
        status="success",
        requests=requests_fetched,
        sources=len(source_records),
        values=loaded,
    )
    return loaded, finished


def purge_alert_events(session: Session, *, cutoff: datetime) -> int:
    cutoff = min(cutoff, kst_now() - timedelta(days=ALERT_RETENTION_DAYS))
    return int(
        session.execute(
            delete(WeatherAlertEventRow).where(
                WeatherAlertEventRow.first_seen_at < cutoff,
            )
        ).rowcount
        or 0
    )


def read_alert_events(
    repo: WeatherRepository,
    *,
    bjd_code: str,
    since: datetime,
) -> dict[str, Any]:
    now = kst_now()
    retained_since = now - timedelta(days=ALERT_RETENTION_DAYS)
    if since.tzinfo is None or not retained_since <= since <= now:
        raise ValueError("ALERT_HISTORY_WINDOW")
    codes = candidate_areas(bjd_code)
    with repo._session_factory() as session:
        runs = list(
            session.scalars(
                select(SyncRunRow)
                .where(
                    SyncRunRow.provider == ALERT_PROVIDER,
                    SyncRunRow.dataset_key == ALERT_DATASET,
                )
                .order_by(SyncRunRow.started_at.desc())
                .limit(1)
            )
        )
        success = session.scalar(
            select(SyncRunRow)
            .where(
                SyncRunRow.provider == ALERT_PROVIDER,
                SyncRunRow.dataset_key == ALERT_DATASET,
                SyncRunRow.status == "success",
            )
            .order_by(SyncRunRow.finished_at.desc())
            .limit(1)
        )
        earliest = session.scalar(
            select(func.min(SyncRunRow.started_at)).where(
                SyncRunRow.provider == ALERT_PROVIDER,
                SyncRunRow.dataset_key == ALERT_DATASET,
                SyncRunRow.status == "success",
            )
        )
        latest = runs[0] if runs else None
        fresh = bool(
            known_bjd(bjd_code)
            and success
            and success.finished_at
            and now - timedelta(minutes=15) <= success.finished_at <= now
            and latest
            and latest.status in {"running", "success"}
        )
        rows = (
            list(
                session.scalars(
                    select(WeatherAlertEventRow)
                    .where(
                        WeatherAlertEventRow.area_code.in_(codes),
                        WeatherAlertEventRow.first_seen_at >= since,
                        WeatherAlertEventRow.first_seen_at <= now,
                    )
                    .order_by(
                        WeatherAlertEventRow.first_seen_at,
                        WeatherAlertEventRow.event_id,
                    )
                    .limit(ALERT_QUERY_LIMIT + 1)
                )
            )
            if codes
            else []
        )
        complete = len(rows) <= ALERT_QUERY_LIMIT
        events = []
        unverified_scope_count = 0
        for row in rows[:ALERT_QUERY_LIMIT]:
            event = AlertEvent.model_validate(row.event)
            scope = scope_at(row.area_code, event.issued_at)
            if scope is None:
                unverified_scope_count += 1
                continue
            events.append(
                {
                    **event.model_dump(mode="json"),
                    "area_name": scope["area_name"],
                    "first_seen_at": row.first_seen_at,
                    "source_record_key": row.source_record_key,
                    "scope_status": "verified",
                    "bjd_prefixes": scope["bjd_prefixes"],
                }
            )
        return {
            "namespace": "kr_bjd",
            "bjd_code": bjd_code,
            "region_status": "known" if known_bjd(bjd_code) else "unknown",
            "mapping_version": area_mapping()["version"],
            "mapping_valid_from": area_mapping()["mapping_valid_from"],
            "unverified_scope_count": unverified_scope_count,
            "mapping_coverage": "partial",
            "collection_status": latest.status if latest else "unavailable",
            "last_attempt_at": latest.started_at if latest else None,
            "last_success_at": success.finished_at if success else None,
            # 저장한 provider 오류 원문 대신 알려진 안전한 분류만 공개한다.
            "last_error_code": (
                "ALERT_COLLECTION_FAILED"
                if latest
                and latest.status
                not in {
                    "running",
                    "success",
                }
                else None
            ),
            "fresh": fresh,
            "events_complete": complete,
            "retained_since": retained_since,
            "history_available_since": earliest,
            "queried_since": since,
            "absence_is_clear": False,
            "events": events,
        }
