"""공개·대기·frozen 참조 밖의 export artifact만 지우는 bounded GC.

artifact 파일 이름은 내용 sha256이다. 아래 참조 중 하나라도 가리키는 digest는 지우지 않는다.

- 아직 공개 전인 attempt(``preparing``/``sealed``, ``published_at`` NULL)의 receipt
- 승격 중 pin(``weather_export_pending_refs``)
- 현재 head generation과 승인 inventory(``weather_export_approved_locations``)의 generation
- 아직 유효하거나 보존 창 안에서 만료된 frozen snapshot, 그리고 가장 최근
  ``LATEST_SNAPSHOTS``개 snapshot이 가리키는 generation(이전 승인 fallback 포함)
- 보존 창(기본 24시간) 안에 만든 generation
- 일별 집계 checkpoint 뒤의 generation(아직 읽지 않은 세대)

참조가 없고 mtime이 유예(기본 1시간)보다 오래된 파일만 지운다. 지우기 직전에 배타
guard를 잡고 그 묶음의 digest만 참조를 다시 확인하고 mtime도 다시 본다. 승격·collector는
같은 guard 안에서 commit하거나 파일을 다시 쓴다. reader는 freeze를 guard 없이 commit한 뒤
공유 guard 안에서 ref 파일 존재를 확인하고(없으면 다시 발급), 그 뒤의 GC 재확인은
commit된 snapshot ref를 본다(export_snapshots.ensure_snapshot_files). ``dry_run``은 지울
대상과 byte만 센다. 이전 승인 fallback이 보존 창보다 오래됐고 최근 snapshot에도 없으면 그
fallback 응답은 파일 누락으로 실패할 수 있다(stale 대신 오류).
"""

from __future__ import annotations

import logging
import os
import re
import stat
import time
from collections import deque
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path
from typing import Any

from sqlalchemy import String, column, literal_column, or_, select, text, tuple_, union, values

from .export_artifacts import ExportArtifactError, ExportArtifactStore

logger = logging.getLogger(__name__)

GC_MODES = frozenset({"dry_run", "delete"})
DEFAULT_RETENTION = timedelta(hours=24)
DEFAULT_GRACE = timedelta(hours=1)
#: 보존 창과 무관하게 지키는 가장 최근 snapshot 수(선택별 이전 승인 fallback).
LATEST_SNAPSHOTS = 64
#: 배타 guard 한 번에 지우는 파일 수. reader·승격이 기다리는 시간을 짧게 둔다.
UNLINK_BATCH = 256
#: 한 번의 GC가 지우는 파일 상한. 넘는 나머지는 다음 주기에 본다(complete=False).
MAX_UNLINKS = 20000
#: 파일 수와 무관하게 한 번에 기억하는 오래된 후보 수. 나머지는 열린 iterator에서 이어간다.
MAX_SCAN_CANDIDATES = 20000
GC_REFERENCE_SECONDS = 3.0
#: 참조 조회 transaction의 상한. publisher tick 상한 안에서 GC의 최악 시간을 묶는다.
GC_LOCK_TIMEOUT = "1s"
#: 지우기 묶음마다 배타 guard를 기다리는 상한(초).
GC_GUARD_TIMEOUT_SECONDS = 1.0
_ARTIFACT = re.compile(r"([0-9a-f]{64})\.jsonl")
_CANDIDATE = re.compile(r"\.candidate-[A-Za-z0-9_]+")


def live_artifact_digests(
    repository,
    *,
    now: datetime,
    retention: timedelta,
    digests: Collection[str] | None = None,
    timeout_seconds: float = GC_REFERENCE_SECONDS,
) -> set[str]:
    """지우면 안 되는 artifact digest 집합. 하나의 transaction 시점에서 읽는다.

    ``digests``를 주면 그 digest만 확인한다(artifact_digest 색인 사용). 짧은 statement·
    잠금 상한을 쓴다.
    """
    from .export_daily_store import daily_summary_tables
    from .export_heads import ExportHeads
    from .export_inventory import export_inventory_table
    from .export_snapshots import export_snapshot_tables

    heads = ExportHeads(repository)
    attempts, chunks = heads.lifecycle.attempts, heads.lifecycle.chunks
    generations, refs, pins = heads.generations, heads.refs, heads.pins
    snapshots, snapshot_refs = export_snapshot_tables(heads.metadata)
    _, progress = daily_summary_tables(heads.metadata)
    inventory = export_inventory_table(heads.metadata)
    cutoff = now - retention
    wanted = None if digests is None else sorted(set(digests))
    if wanted == []:
        return set()
    checkpoint = select(progress).where(progress.c.progress_id == 1).subquery()
    unread = or_(
        ~select(checkpoint.c.progress_id).exists(),
        tuple_(generations.c.created_at, generations.c.generation_id) > tuple_(
            select(checkpoint.c.published_at).scalar_subquery(),
            select(checkpoint.c.generation_id).scalar_subquery(),
        ),
    )
    latest = select(snapshots.c.snapshot_id).order_by(
        snapshots.c.issue_order.desc(),
    ).limit(LATEST_SNAPSHOTS)
    # 후보 digest 색인에서 시작해 그 generation의 보호 사유만 확인한다. 운영의 수십만
    # generation/inventory 전체 UNION을 먼저 만들지 않는다. 보호 종류는 이전과 동일하다.
    protected = or_(
        generations.c.created_at > cutoff,
        unread,
        select(heads.heads.c.generation_id).where(
            heads.heads.c.generation_id == generations.c.generation_id,
        ).exists(),
        select(inventory.c.generation_id).where(
            inventory.c.generation_id == generations.c.generation_id,
        ).exists(),
        select(snapshot_refs.c.generation_id).join(
            snapshots, snapshots.c.snapshot_id == snapshot_refs.c.snapshot_id,
        ).where(
            snapshot_refs.c.generation_id == generations.c.generation_id,
            or_(snapshots.c.expires_at > cutoff, snapshots.c.snapshot_id.in_(latest)),
        ).exists(),
    )
    referenced = select(refs.c.artifact_digest).join(
        generations, generations.c.generation_id == refs.c.generation_id,
    ).where(protected)
    received = select(chunks.c.artifact_digest).join(
        attempts, attempts.c.attempt_id == chunks.c.attempt_id,
    ).where(
        attempts.c.status.in_([literal_column("'preparing'"), literal_column("'sealed'")]),
        attempts.c.published_at.is_(None),
    )
    pinned = select(pins.c.artifact_digest)
    if wanted is None:
        statement = union(referenced, received, pinned)
    else:
        # planner가 receipt의 전체 hashed SubPlan을 선택해도 후보 digest 밖을
        # 읽지 않도록 내부 조회에도 같은 유한 집합을 전달한다.
        received = received.where(chunks.c.artifact_digest.in_(wanted))
        candidates = values(column("digest", String(64)), name="gc_candidates").data(
            [(digest,) for digest in wanted],
        )
        # 같은 내용 파일이 수십만 generation에 재사용돼도 첫 live 참조에서 끝낸다.
        # digest별 EXISTS이므로 전체 일치 행을 읽어 Python set으로 줄이지 않는다.
        statement = select(candidates.c.digest).where(or_(
            pinned.where(pins.c.artifact_digest == candidates.c.digest).exists(),
            received.where(chunks.c.artifact_digest == candidates.c.digest).exists(),
            referenced.where(refs.c.artifact_digest == candidates.c.digest).exists(),
        ))
    with repository._session_factory.begin() as session:
        # 세 참조 종류도 한 문장으로 묶어 3초를 종류마다 다시 쓰지 않는다.
        timeout_ms = max(1, int(min(GC_REFERENCE_SECONDS, timeout_seconds) * 1000))
        session.execute(text(f"SET LOCAL statement_timeout = '{timeout_ms}ms'"))
        # GC는 작은 후보만 조회한다. parameter별 planner 비용 변동으로 JIT가
        # guard 안에서 컴파일 시간을 쓰지 않도록 이 transaction에서만 끈다.
        session.execute(text("SET LOCAL jit = off"))
        session.execute(text(f"SET LOCAL lock_timeout = '{GC_LOCK_TIMEOUT}'"))
        return set(session.scalars(statement))


class ArtifactGCScan:
    """publisher 프로세스가 tick 사이에 보유하는 한 root의 유한 탐색 위치.

    디스크 cursor/목록 파일을 만들지 않는다. 재시작하면 처음부터 다시 읽어도 안전하고,
    같은 프로세스에서는 긴 live/최근 파일 앞부분을 tick마다 재탐색하지 않는다.
    참조 조회 실패·취소 때 pending을 남겨 다음 실행이 파일을 재검사하게 한다.
    호출자는 기존 publisher의 단일 tick slot 안에서만 같은 scan을 사용한다.
    """

    def __init__(self) -> None:
        self.root: Path | None = None
        self.entries = None
        self.pending: deque[tuple[str, int]] = deque()
        self.exhausted = False

    def close(self) -> None:
        if self.entries is not None:
            self.entries.close()
        self.entries = None
        self.root = None
        self.pending.clear()
        self.exhausted = False

    def read(self, root: Path, *, deadline: float, cut: float, report: dict[str, Any]) -> None:
        if root != self.root:
            self.close()
            self.root = root
            self.entries = os.scandir(root)
        # 먼저 지난 실행이 아직 처리하지 못한 묶음을 비운다.
        if self.pending or self.exhausted:
            return
        while len(self.pending) < MAX_SCAN_CANDIDATES and time.monotonic() < deadline:
            try:
                entry = next(self.entries)
            except StopIteration:
                self.entries.close()
                self.entries = None
                self.exhausted = True
                break
            report["visited"] += 1
            match = _ARTIFACT.fullmatch(entry.name)
            if match is None and _CANDIDATE.fullmatch(entry.name) is None:
                continue
            report["scanned"] += int(match is not None)
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_mtime < cut:
                self.pending.append((entry.name, info.st_size))


def collect_export_artifacts(
    repository,
    artifact_root: Path,
    *,
    mode: str = "dry_run",
    deadline: float,
    delete_budget_seconds: float = 1.5,
    retention: timedelta = DEFAULT_RETENTION,
    grace: timedelta = DEFAULT_GRACE,
    now: datetime | None = None,
    scan: ArtifactGCScan | None = None,
) -> dict[str, Any]:
    """유한 후보 묶음의 참조만 확인하고 기존 guard 안에서 오래된 비참조 파일을 지운다.

    deadline은 목록 예산이다. 참조/guard 대기(최대 4초)는 삭제 IO 예산과 분리하고,
    각 문장의 timeout도 남은 처리 예산보다 작게 둔다. publisher는 scan을 재사용한다.
    독립 호출의 기본 dry_run과 알 수 없는 파일·symlink 보존은 유지한다.
    """
    if mode not in GC_MODES:
        raise ValueError("artifact GC mode는 dry_run 또는 delete입니다.")
    if retention < timedelta(hours=1) or grace < timedelta(minutes=5):
        raise ValueError("artifact 보존 창은 1시간, 유예는 5분 이상이어야 합니다.")
    if not 0 < delete_budget_seconds <= 30:
        raise ValueError("artifact 삭제 예산이 올바르지 않습니다.")
    store = ExportArtifactStore(artifact_root)
    report: dict[str, Any] = {
        "mode": mode, "visited": 0, "scanned": 0, "live": 0, "eligible": 0,
        "eligible_bytes": 0, "deleted": 0, "deleted_bytes": 0, "errors": 0,
        "complete": False, "pending": 0,
    }
    if not store.root.is_dir():
        if scan is not None:
            scan.close()
        report["complete"] = True
        return report
    owned = scan is None
    scan = scan if scan is not None else ArtifactGCScan()
    clock = now or datetime.now(UTC)
    cut = (clock - grace).timestamp()
    try:
        scan.read(store.root, deadline=deadline, cut=cut, report=report)
        processing_deadline = (
            time.monotonic() + delete_budget_seconds + GC_REFERENCE_SECONDS
            + GC_GUARD_TIMEOUT_SECONDS
        )
        attempted = 0
        while scan.pending and time.monotonic() < processing_deadline:
            batch = list(islice(scan.pending, UNLINK_BATCH))
            digests = [match.group(1) for name, _ in batch
                       if (match := _ARTIFACT.fullmatch(name)) is not None]
            if mode == "dry_run":
                live = live_artifact_digests(
                    repository, now=clock, retention=retention, digests=digests,
                    timeout_seconds=processing_deadline - time.monotonic(),
                )
                for name, size in batch:
                    match = _ARTIFACT.fullmatch(name)
                    if match is not None and match.group(1) in live:
                        report["live"] += 1
                    else:
                        report["eligible"] += 1
                        report["eligible_bytes"] += size
                    scan.pending.popleft()
                continue
            if attempted >= MAX_UNLINKS:
                break
            try:
                with store.guard(
                    timeout_seconds=min(
                        GC_GUARD_TIMEOUT_SECONDS, max(0, processing_deadline - time.monotonic()),
                    ), holder="gc",
                ):
                    # 참조를 guard 밖에서 미리 전부 읽지 않는다. 각 후보의 유일한 판정은
                    # 삭제 직전 guard 안에서 새 DB snapshot으로 한다. 오류면 이 묶음은 남긴다.
                    live = live_artifact_digests(
                        repository, now=clock, retention=retention, digests=digests,
                        timeout_seconds=processing_deadline - time.monotonic(),
                    )
                    for name, size in batch:
                        match = _ARTIFACT.fullmatch(name)
                        if match is not None and match.group(1) in live:
                            report["live"] += 1
                        else:
                            report["eligible"] += 1
                            report["eligible_bytes"] += size
                    for name, _ in batch:
                        if attempted >= MAX_UNLINKS or time.monotonic() >= processing_deadline:
                            break
                        match = _ARTIFACT.fullmatch(name)
                        if match is None or match.group(1) not in live:
                            path = store.root / name
                            try:
                                info = os.lstat(path)
                                if stat.S_ISREG(info.st_mode) and info.st_mtime < cut:
                                    attempted += 1
                                    os.unlink(path)
                                    report["deleted"] += 1
                                    report["deleted_bytes"] += info.st_size
                            except FileNotFoundError:
                                pass
                            except OSError:
                                report["errors"] += 1
                        scan.pending.popleft()
            except ExportArtifactError:
                break  # guard가 바쁘면 pending을 보존하고 다음 tick에 재확인한다.
        report["pending"] = len(scan.pending)
        report["complete"] = scan.exhausted and not scan.pending
        if report["complete"]:
            scan.close()
        return report
    finally:
        if owned:
            scan.close()
