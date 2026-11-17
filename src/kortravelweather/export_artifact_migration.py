"""실제로 존재하는 live 파일만 DB로 명시 이관한다. 일반 reader의 fallback은 없다."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text

from .export_artifacts import (
    MAX_CHUNK_BYTES,
    MAX_CHUNK_POINTS,
    ExportArtifactError,
    PreparedExportChunk,
)
from .export_database_artifacts import DatabaseExportArtifactStore
from .export_gc import DEFAULT_RETENTION, live_artifact_statement

_FILE = re.compile(r"([0-9a-f]{64})\.jsonl")


def _legacy_chunk(root, digest, now):
    fd = os.open(root / (digest + ".jsonl"), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ExportArtifactError("이전 artifact는 일반 파일이어야 합니다.")
        payload = stream.read(MAX_CHUNK_BYTES + 1)
    count = len(payload.splitlines())
    if (len(payload) > MAX_CHUNK_BYTES or count > MAX_CHUNK_POINTS
            or hashlib.sha256(payload).hexdigest() != digest):
        raise ExportArtifactError("이전 artifact 내용 증거가 다릅니다.")
    return PreparedExportChunk(payload, digest, count, now)


def migrate_legacy_export_artifacts(
    repository, artifact_root: Path, *, deadline: float,
    dry_run: bool = True, max_artifacts: int = 100000, now: datetime | None = None,
    on_batch=None, digest_prefix: str = "", modified_since: datetime | None = None,
):
    """파일 목록을 유한 묶음으로 조회한다. 전역 receipt/ref UNION·정렬을 하지 않는다.

    서비스 GC와 같은 live 판정식에 실존 후보 최대256개만 전달한다. 이 명시적 운영
    이관의 SQL 예산은10초다. 서비스 GC의 후보256개·SQL3초 정책은 바꾸지 않는다.
    파일·기존 DB 행 삭제0. 최종 전환 전에 publisher drain 뒤 재검증한다.
    """
    if not 1 <= max_artifacts <= 100000:
        raise ValueError("artifact 이관 개수 상한이 올바르지 않습니다.")
    if re.fullmatch(r"[0-9a-f]{0,2}", digest_prefix) is None:
        raise ValueError("artifact 이관 prefix는0~2개 소문자16진수입니다.")
    if modified_since is not None and (
        modified_since.tzinfo is None or modified_since.utcoffset() is None
    ):
        raise ValueError("증분 이관 시각에는 timezone이 필요합니다.")
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("artifact 이관 시각에는 timezone이 필요합니다.")
    if modified_since is not None and modified_since > now:
        raise ValueError("증분 이관 시각은 미래일 수 없습니다.")
    report = {"dry_run": dry_run, "scanned": 0, "protected": 0, "already_present": 0,
              "imported": 0, "imported_bytes": 0, "missing": 0, "errors": 0,
              "complete": False, "files_deleted": 0, "existing_rows_deleted": 0,
              "would_import": 0, "outcome": "budget_exhausted"}
    root = Path(artifact_root)
    if not root.is_absolute():
        raise ValueError("이전 artifact 경로는 절대 경로여야 합니다.")
    if time.monotonic() >= deadline:
        return report
    target = DatabaseExportArtifactStore(repository)
    def process(batch):
        if not batch:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            return False
        statement = live_artifact_statement(
            repository, now=now, retention=DEFAULT_RETENTION, digests=batch,
        )
        with repository.engine.begin() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            timeout = max(1, int(min(10, remaining) * 1000))
            connection.execute(text(f"SET LOCAL statement_timeout = '{timeout}ms'"))
            connection.execute(text("SET LOCAL lock_timeout = '1s'"))
            connection.execute(text("SET LOCAL jit = off"))
            live = set(connection.scalars(statement))
            existing = set(connection.scalars(select(target.table.c.digest).where(
                target.table.c.digest.in_(list(live)),
            )))
        report["protected"] += len(live)
        if report["protected"] > max_artifacts:
            raise ExportArtifactError("live artifact가 명시 이관 상한을 넘었습니다.")
        for digest in live:
            if time.monotonic() >= deadline:
                return False
            try:
                # 이관은 파일/참조를 삭제하지 않는다. 불변 내용 주소를 읽고 hash를 검증하므로
                # reader slot을 점유하지 않고 read-only volume에서도 동작한다.
                # 동시 replace/unlink가 있어도 검증한 열린 FD의 bytes만 DB에 추가한다.
                chunk = _legacy_chunk(root, digest, now)
                if digest in existing:
                    target.verify(digest, byte_count=len(chunk.payload),
                                  point_count=chunk.point_count)
                    report["already_present"] += 1
                elif dry_run:
                    report["would_import"] += 1
                else:
                    target.publish(chunk)
                    target.verify(digest, byte_count=len(chunk.payload),
                                  point_count=chunk.point_count)
                    report["imported"] += 1
                    report["imported_bytes"] += len(chunk.payload)
            except (FileNotFoundError, ExportArtifactError):
                report["missing"] += 1
                report["outcome"] = "missing_or_invalid_live_artifact"
                return False
        if on_batch is not None:
            on_batch(dict(report))
        return True
    if not root.is_dir():
        report.update(complete=True, outcome="complete")
        return report
    with os.scandir(root) as entries:
        batch = []
        for entry in entries:
            if time.monotonic() >= deadline:
                return report
            match = _FILE.fullmatch(entry.name)
            if match is None or not match.group(1).startswith(digest_prefix):
                continue
            if modified_since is not None:
                try:
                    if entry.stat(follow_symlinks=False).st_mtime < modified_since.timestamp():
                        continue
                except FileNotFoundError:
                    continue
            report["scanned"] += 1
            batch.append(match.group(1))
            if len(batch) == 256:
                if not process(batch):
                    return report
                batch.clear()
        if not process(batch):
            return report
    report.update(complete=True, outcome="complete")
    return report

def main():
    """SDK가 schema를 적용한 뒤 명시적으로 호출하는 이관 entrypoint."""
    import argparse
    import json

    from .repository import repository_from_settings
    from .settings import get_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-seconds", type=int, default=900)
    parser.add_argument("--prefix", default="")
    parser.add_argument("--modified-since", type=datetime.fromisoformat)
    args = parser.parse_args()
    if not 5 <= args.max_seconds <= 900:
        parser.error("--max-seconds는5~900입니다.")
    settings = get_settings()
    if settings.service_export_artifact_root is None:
        parser.error("기존 artifact root가 필요합니다.")
    repository = repository_from_settings(settings)
    try:
        result = migrate_legacy_export_artifacts(
            repository, settings.service_export_artifact_root,
            deadline=time.monotonic() + args.max_seconds, dry_run=not args.execute,
            digest_prefix=args.prefix, modified_since=args.modified_since,
        )
        print(json.dumps({"source": settings.git_commit, **result}, sort_keys=True))
        return 0 if result["complete"] else 2
    except Exception as exc:
        # driver가 SQL·접속 문자열을 stderr로 내보내지 않게 분류만 기록한다.
        print(json.dumps({"source": settings.git_commit, "outcome": "stopped",
                          "errorClass": type(exc).__name__}, sort_keys=True))
        return 3
    finally:
        repository.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
