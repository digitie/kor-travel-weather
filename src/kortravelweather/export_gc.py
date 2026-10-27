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
guard를 잡고 참조 집합과 mtime을 다시 확인한다. reader·승격·collector는 같은 guard 안에서
snapshot·receipt를 commit하거나 파일을 다시 쓰므로 그 사이 새로 참조된 파일은 남는다.
``dry_run``은 지울 대상과 byte만 센다. 이전 승인 fallback이 보존 창보다 오래됐고 최근
snapshot에도 없으면 그 fallback 응답은 파일 누락으로 실패할 수 있다(stale 대신 오류).
"""

from __future__ import annotations

import logging
import os
import re
import stat
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import literal_column, select, tuple_, union

from .export_artifacts import ExportArtifactError, ExportArtifactStore

logger = logging.getLogger(__name__)

GC_MODES = frozenset({"dry_run", "delete"})
DEFAULT_RETENTION = timedelta(hours=24)
DEFAULT_GRACE = timedelta(hours=1)
#: 보존 창과 무관하게 지키는 가장 최근 snapshot 수(선택별 이전 승인 fallback).
LATEST_SNAPSHOTS = 64
#: 배타 guard 한 번에 지우는 파일 수. reader·승격이 기다리는 시간을 짧게 둔다.
UNLINK_BATCH = 256
#: 한 번의 GC가 지우는 파일 상한.
MAX_UNLINKS = 20000
_ARTIFACT = re.compile(r"([0-9a-f]{64})\.jsonl")
_CANDIDATE = re.compile(r"\.candidate-[A-Za-z0-9_]+")


def live_artifact_digests(repository, *, now: datetime, retention: timedelta) -> set[str]:
    """지우면 안 되는 artifact digest 집합. 하나의 transaction 시점에서 읽는다."""
    from .export_daily_store import daily_summary_tables
    from .export_heads import ExportHeads
    from .export_inventory import export_inventory_table
    from .export_snapshots import export_snapshot_tables

    heads = ExportHeads(repository)
    attempts, chunks = heads.lifecycle.attempts, heads.lifecycle.chunks
    generations, refs = heads.generations, heads.refs
    snapshots, snapshot_refs = export_snapshot_tables(heads.metadata)
    _, progress = daily_summary_tables(heads.metadata)
    inventory = export_inventory_table(heads.metadata)
    cutoff = now - retention
    with heads._transaction() as session:
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
        live = set(session.scalars(
            select(refs.c.artifact_digest).where(
                refs.c.generation_id.in_(select(live_generations.c.generation_id))
            )
        ))
        pending = select(attempts.c.attempt_id).where(
            attempts.c.status.in_([literal_column("'preparing'"), literal_column("'sealed'")]),
            attempts.c.published_at.is_(None),
        )
        live.update(session.scalars(
            select(chunks.c.artifact_digest).where(chunks.c.attempt_id.in_(pending))
        ))
        live.update(session.scalars(select(heads.pins.c.artifact_digest)))
    return live


def collect_export_artifacts(
    repository,
    artifact_root: Path,
    *,
    mode: str = "delete",
    deadline: float,
    retention: timedelta = DEFAULT_RETENTION,
    grace: timedelta = DEFAULT_GRACE,
    now: datetime | None = None,
) -> dict[str, Any]:
    """참조가 없는 오래된 artifact를 지운다(``dry_run``은 세기만 한다).

    ``deadline``은 ``time.monotonic()`` 기준 협력 예산이다. 넘으면 다음 주기에 이어간다.
    """
    if mode not in GC_MODES:
        raise ValueError("artifact GC mode는 dry_run 또는 delete입니다.")
    if retention < timedelta(hours=1) or grace < timedelta(minutes=5):
        raise ValueError("artifact 보존 창은 1시간, 유예는 5분 이상이어야 합니다.")
    store = ExportArtifactStore(artifact_root)
    root = store.root
    report: dict[str, Any] = {
        "mode": mode, "scanned": 0, "live": 0, "eligible": 0, "eligible_bytes": 0,
        "deleted": 0, "deleted_bytes": 0, "complete": True,
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
    eligible = eligible[:MAX_UNLINKS]
    for start in range(0, len(eligible), UNLINK_BATCH):
        if time.monotonic() >= deadline:
            report["complete"] = False
            break
        try:
            with store.guard(timeout_seconds=2):
                # reader의 freeze·승격·collector 쓰기는 같은 guard 안에서 commit한다.
                # 배타 guard를 잡은 뒤 참조를 다시 읽어 그 사이 새 snapshot·receipt가
                # 가리키게 된 파일을 지킨다.
                live = live_artifact_digests(repository, now=clock, retention=retention)
                for name, _ in eligible[start:start + UNLINK_BATCH]:
                    match = _ARTIFACT.fullmatch(name)
                    if match is not None and match.group(1) in live:
                        continue
                    path = root / name
                    try:
                        info = os.lstat(path)
                    except FileNotFoundError:
                        continue
                    # guard 대기 중 같은 digest를 다시 쓴 파일은 mtime이 새것이다.
                    if not stat.S_ISREG(info.st_mode) or info.st_mtime >= cut:
                        continue
                    os.unlink(path)
                    report["deleted"] += 1
                    report["deleted_bytes"] += info.st_size
        except ExportArtifactError:
            # reader·승격이 guard를 오래 잡고 있다. 남은 파일은 다음 주기에 본다.
            report["complete"] = False
            break
    if report["deleted"]:
        logger.info(
            "weather_export_artifact_gc deleted=%s bytes=%s complete=%s",
            report["deleted"], report["deleted_bytes"], report["complete"],
        )
    return report
