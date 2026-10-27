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
guard를 잡고 그 묶음의 digest만 참조를 다시 확인하고 mtime도 다시 본다. reader·승격·
collector는 같은 guard 안에서 snapshot·receipt를 commit하거나 파일을 다시 쓰므로 그 사이
새로 참조된 파일은 남는다. ``dry_run``은 지울 대상과 byte만 센다. 이전 승인 fallback이
보존 창보다 오래됐고 최근 snapshot에도 없으면 그 fallback 응답은 파일 누락으로 실패할 수
있다(stale 대신 오류).
"""

from __future__ import annotations

import logging
import os
import re
import stat
import time
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import literal_column, select, text, tuple_, union

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
#: 참조 조회 transaction의 상한. publisher tick 상한 안에서 GC의 최악 시간을 묶는다.
GC_STATEMENT_TIMEOUT = "3s"
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
    with repository._session_factory.begin() as session:
        session.execute(text(f"SET LOCAL statement_timeout = '{GC_STATEMENT_TIMEOUT}'"))
        session.execute(text(f"SET LOCAL lock_timeout = '{GC_LOCK_TIMEOUT}'"))
        checkpoint = session.execute(
            select(progress.c.published_at, progress.c.generation_id).where(
                progress.c.progress_id == 1,
            )
        ).first()
        unread = select(generations.c.generation_id)
        if checkpoint is not None:
            unread = unread.where(
                tuple_(generations.c.created_at, generations.c.generation_id)
                > tuple_(checkpoint.published_at, checkpoint.generation_id)
            )
        latest = (
            select(snapshots.c.snapshot_id)
            .order_by(snapshots.c.issue_order.desc())
            .limit(LATEST_SNAPSHOTS)
            .subquery()
        )
        live_generations = union(
            select(heads.heads.c.generation_id),
            select(inventory.c.generation_id),
            select(generations.c.generation_id).where(generations.c.created_at > cutoff),
            select(snapshot_refs.c.generation_id)
            .join(snapshots, snapshots.c.snapshot_id == snapshot_refs.c.snapshot_id)
            .where(snapshots.c.expires_at > cutoff),
            select(snapshot_refs.c.generation_id).where(
                snapshot_refs.c.snapshot_id.in_(select(latest.c.snapshot_id))
            ),
            unread,
        ).subquery()
        referenced = select(refs.c.artifact_digest).where(
            refs.c.generation_id.in_(select(live_generations.c.generation_id))
        )
        pending = select(attempts.c.attempt_id).where(
            attempts.c.status.in_([literal_column("'preparing'"), literal_column("'sealed'")]),
            attempts.c.published_at.is_(None),
        )
        received = select(chunks.c.artifact_digest).where(chunks.c.attempt_id.in_(pending))
        pinned = select(pins.c.artifact_digest)
        if wanted is not None:
            referenced = referenced.where(refs.c.artifact_digest.in_(wanted))
            received = received.where(chunks.c.artifact_digest.in_(wanted))
            pinned = pinned.where(pins.c.artifact_digest.in_(wanted))
        live = set(session.scalars(referenced))
        live.update(session.scalars(received))
        live.update(session.scalars(pinned))
    return live


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
) -> dict[str, Any]:
    """참조가 없는 오래된 artifact를 지운다(``dry_run``은 세기만 한다).

    ``deadline``은 목록 단계의 ``time.monotonic()`` 협력 예산이고, 지우기 단계는 시작부터
    ``delete_budget_seconds``를 따로 쓴다. 넘으면 다음 주기에 이어간다(complete=False).
    """
    if mode not in GC_MODES:
        raise ValueError("artifact GC mode는 dry_run 또는 delete입니다.")
    if retention < timedelta(hours=1) or grace < timedelta(minutes=5):
        raise ValueError("artifact 보존 창은 1시간, 유예는 5분 이상이어야 합니다.")
    if not 0 < delete_budget_seconds <= 30:
        raise ValueError("artifact 삭제 예산이 올바르지 않습니다.")
    store = ExportArtifactStore(artifact_root)
    root = store.root
    report: dict[str, Any] = {
        "mode": mode, "scanned": 0, "live": 0, "eligible": 0, "eligible_bytes": 0,
        "deleted": 0, "deleted_bytes": 0, "errors": 0, "complete": True,
    }
    if not root.is_dir():
        return report
    clock = now or datetime.now(UTC)
    live = live_artifact_digests(repository, now=clock, retention=retention)
    cut = (clock - grace).timestamp()
    eligible: list[tuple[str, int]] = []
    with os.scandir(root) as entries:
        for entry in entries:
            match = _ARTIFACT.fullmatch(entry.name)
            if match is None and _CANDIDATE.fullmatch(entry.name) is None:
                # lock 파일과 알 수 없는 이름은 건드리지 않는다.
                continue
            if match is not None:
                report["scanned"] += 1
                if match.group(1) in live:
                    report["live"] += 1
                    continue
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_mtime < cut:
                eligible.append((entry.name, info.st_size))
            if time.monotonic() >= deadline:
                report["complete"] = False
                break
    report["eligible"] = len(eligible)
    report["eligible_bytes"] = sum(size for _, size in eligible)
    if mode == "dry_run":
        return report
    if len(eligible) > MAX_UNLINKS:
        report["complete"] = False
        eligible = eligible[:MAX_UNLINKS]
    delete_deadline = time.monotonic() + delete_budget_seconds
    for start in range(0, len(eligible), UNLINK_BATCH):
        if time.monotonic() >= delete_deadline:
            report["complete"] = False
            break
        batch = eligible[start:start + UNLINK_BATCH]
        batch_digests = [
            match.group(1) for name, _ in batch
            if (match := _ARTIFACT.fullmatch(name)) is not None
        ]
        try:
            with store.guard(timeout_seconds=GC_GUARD_TIMEOUT_SECONDS):
                # reader의 freeze·승격·collector 쓰기는 같은 guard 안에서 commit한다.
                # 배타 guard를 잡은 뒤 이 묶음의 digest만 참조를 다시 읽는다.
                rechecked = live_artifact_digests(
                    repository, now=clock, retention=retention, digests=batch_digests,
                )
                for name, _ in batch:
                    match = _ARTIFACT.fullmatch(name)
                    if match is not None and match.group(1) in rechecked:
                        continue
                    path = root / name
                    try:
                        info = os.lstat(path)
                        # guard 대기 중 같은 digest를 다시 쓴 파일은 mtime이 새것이다.
                        if not stat.S_ISREG(info.st_mode) or info.st_mtime >= cut:
                            continue
                        os.unlink(path)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        report["errors"] += 1
                        continue
                    report["deleted"] += 1
                    report["deleted_bytes"] += info.st_size
        except ExportArtifactError:
            # reader·승격이 guard를 잡고 있다. 남은 파일은 다음 주기에 본다.
            report["complete"] = False
            break
    if report["deleted"] or report["errors"]:
        logger.info(
            "weather_export_artifact_gc deleted=%s bytes=%s errors=%s complete=%s",
            report["deleted"], report["deleted_bytes"], report["errors"], report["complete"],
        )
    return report
