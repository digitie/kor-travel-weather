"""파일 I/O 없이 불변 DB export chunk를 참조 보호·keyset으로 유한 정리한다."""

from __future__ import annotations

import time
from collections import deque
from datetime import UTC, datetime, timedelta
from itertools import islice
from typing import Any

from sqlalchemy import select, text, tuple_

from .export_artifacts import ExportArtifactError
from .export_gc import (
    DEFAULT_GRACE,
    DEFAULT_RETENTION,
    GC_GUARD_TIMEOUT_SECONDS,
    GC_MODES,
    GC_REFERENCE_SECONDS,
    MAX_SCAN_CANDIDATES,
    MAX_UNLINKS,
    UNLINK_BATCH,
    live_artifact_digests,
)


class DatabaseArtifactGCScan:
    """프로세스 메모리의 bounded cursor. 실패한 batch는 다음 tick에 재판정한다.

    ``page_size``·``statement_seconds``는 목록 한 번의 행 수와 문장 상한이다. 기본값은
    예전 sensor 경로 그대로다. 정리 job은 작은 page와 긴 문장 상한으로 여러 page를 읽는다
    (odroid 2026-11-23 실측: 20,000행 목록 한 번 6.7초 — sensor의 1.5초 안에 끝나지 못했다).
    """

    def __init__(
        self, *, page_size: int | None = None,
        statement_seconds: float = GC_REFERENCE_SECONDS,
    ) -> None:
        # 기본 page는 호출 시점의 모듈 상한을 읽는다(시험이 상한을 줄여 keyset을 본다).
        page_size = MAX_SCAN_CANDIDATES if page_size is None else page_size
        if not 1 <= page_size <= MAX_SCAN_CANDIDATES or not 0 < statement_seconds <= 60:
            raise ValueError("artifact GC 목록 page 설정이 올바르지 않습니다.")
        self.page_size = page_size
        self.statement_seconds = statement_seconds
        self.engine = None
        self.after: tuple[datetime, str] | None = None
        self.pending: deque[tuple[str, int]] = deque()
        self.exhausted = False

    def close(self) -> None:
        self.engine = None
        self.after = None
        self.pending.clear()
        self.exhausted = False

    def read(self, repository, table, *, deadline, cutoff, report) -> None:
        if self.engine is not repository.engine:
            self.close()
            self.engine = repository.engine
        if self.pending or self.exhausted or time.monotonic() >= deadline:
            return
        statement = select(table.c.created_at, table.c.digest, table.c.byte_count).where(
            table.c.created_at < cutoff,
        )
        if self.after is not None:
            statement = statement.where(
                tuple_(table.c.created_at, table.c.digest) > self.after,
            )
        statement = statement.order_by(table.c.created_at, table.c.digest).limit(
            self.page_size,
        )
        with repository.engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(text("SET TRANSACTION READ ONLY"))
                timeout_ms = max(1, int(min(
                    self.statement_seconds, max(0, deadline - time.monotonic()),
                ) * 1000))
                connection.execute(text(f"SET LOCAL statement_timeout = '{timeout_ms}ms'"))
                connection.execute(text("SET LOCAL lock_timeout = '1s'"))
                connection.execute(text("SET LOCAL jit = off"))
            rows = connection.execute(statement).all()
        # query/commit가 실패하면 cursor와 pending 어느 것도 전진하지 않는다.
        if rows:
            self.after = (rows[-1].created_at, rows[-1].digest)
            self.pending.extend((row.digest, row.byte_count) for row in rows)
        self.exhausted = len(rows) < self.page_size
        report["visited"] += len(rows)
        report["scanned"] += len(rows)


def _scan_state(scan):
    if isinstance(scan, DatabaseArtifactGCScan):
        return scan
    state = getattr(scan, "_database_artifact_scan", None)
    if state is None:
        state = DatabaseArtifactGCScan()
        scan._database_artifact_scan = state
    return state


def collect_database_export_artifacts(
    repository,
    *,
    mode: str = "dry_run",
    deadline: float,
    delete_budget_seconds: float = 1.5,
    retention: timedelta = DEFAULT_RETENTION,
    grace: timedelta = DEFAULT_GRACE,
    now: datetime | None = None,
    scan=None,
) -> dict[str, Any]:
    """DB guard 안에서 fresh live refs 확인 후 같은 guard transaction으로 삭제한다.

    기존 report key와 참조 조회 3초/guard 1초 reserve를 유지한다. SQL/commit 실패는
    전파하되 pending을 소비하지 않는다. busy guard는 다음 tick에서 재시도한다.
    """
    from .export_database_artifacts import DatabaseExportArtifactStore

    if mode not in GC_MODES:
        raise ValueError("artifact GC mode는 dry_run 또는 delete입니다.")
    if retention < timedelta(hours=1) or grace < timedelta(minutes=5):
        raise ValueError("artifact 보존 창은 1시간, 유예는 5분 이상이어야 합니다.")
    if not 0 < delete_budget_seconds <= 30:
        raise ValueError("artifact 삭제 예산이 올바르지 않습니다.")
    clock = now or datetime.now(UTC)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("artifact GC 시각에는 timezone이 필요합니다.")
    cutoff = clock - grace
    store = DatabaseExportArtifactStore(repository)
    report = {
        "mode": mode, "visited": 0, "scanned": 0, "live": 0, "eligible": 0,
        "eligible_bytes": 0, "deleted": 0, "deleted_bytes": 0, "errors": 0,
        "complete": False, "pending": 0,
    }
    owned = scan is None
    state = DatabaseArtifactGCScan() if owned else _scan_state(scan)
    try:
        state.read(repository, store.table, deadline=deadline, cutoff=cutoff, report=report)
        reserve = GC_REFERENCE_SECONDS + GC_GUARD_TIMEOUT_SECONDS
        processing_deadline = time.monotonic() + delete_budget_seconds + reserve
        attempted = 0
        while state.pending and time.monotonic() + reserve <= processing_deadline:
            batch = list(islice(state.pending, UNLINK_BATCH))
            if mode == "delete" and attempted + len(batch) > MAX_UNLINKS:
                break
            try:
                with store.guard(
                    timeout_seconds=GC_GUARD_TIMEOUT_SECONDS, shared=mode == "dry_run", holder="gc",
                ):
                    live = live_artifact_digests(
                        repository, now=clock, retention=retention,
                        digests=[digest for digest, _ in batch],
                        timeout_seconds=GC_REFERENCE_SECONDS,
                    )
                    victims = [(digest, size) for digest, size in batch if digest not in live]
                    deleted, deleted_bytes = (0, 0)
                    if mode == "delete" and victims:
                        deleted, deleted_bytes = store.delete_many(
                            [digest for digest, _ in victims], created_before=cutoff,
                        )
                # guard commit 뒤에만 진척·실삭제 수를 확정한다. rollback이면 batch 그대로다.
                report["live"] += len(batch) - len(victims)
                report["eligible"] += len(victims)
                report["eligible_bytes"] += sum(size for _, size in victims)
                report["deleted"] += deleted
                report["deleted_bytes"] += deleted_bytes
                attempted += len(batch)
                for _ in batch:
                    state.pending.popleft()
            except ExportArtifactError:
                break
        report["pending"] = len(state.pending)
        report["complete"] = state.exhausted and not state.pending
        if report["complete"]:
            state.close()
        return report
    finally:
        if owned:
            state.close()
