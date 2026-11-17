"""운영의 큰 flat 디렉터리·참조 timeout·취소에서도 GC가 안전하게 전진한다."""

import os
import stat
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import test_export_publisher_recovery as fixtures
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kortravelweather import export_gc
from kortravelweather.export_artifacts import ExportArtifactStore
from kortravelweather.export_gc import ArtifactGCScan, collect_export_artifacts
from kortravelweather.export_heads import ExportHeads

publisher_repository = fixtures.publisher_repository
clear_exports = fixtures.clear_exports


def _old(root, digest):
    path = root / f"{digest}.jsonl"
    path.write_bytes(b"obsolete artifact\n")
    past = (datetime.now(UTC) - timedelta(hours=48)).timestamp()
    os.utime(path, (past, past))
    return path


def _pin(repo, digest):
    heads = ExportHeads(repo)
    with repo.engine.begin() as connection:
        connection.execute(heads.pins.insert().values(
            attempt_id=str(uuid.uuid4()), chunk_key="p" * 64,
            artifact_digest=digest, created_at=datetime.now(UTC),
        ))


def test_gc_reaches_garbage_after_740000_recent_files_without_restarting_scan(
    publisher_repository, tmp_path, monkeypatch,
):
    # 실제 운영 디렉터리 개수만큼 최근 파일을 앞에 둔다. 파일 metadata만 합성하고,
    # 끝의 보호/비참조 파일과 PostgreSQL 참조 판정·unlink는 실제로 실행한다.
    live, garbage = _old(tmp_path, "d" * 64), _old(tmp_path, "e" * 64)
    _pin(publisher_repository, "d" * 64)
    real_scandir, clock, opened = os.scandir, [100.0], []
    fresh = SimpleNamespace(st_mode=stat.S_IFREG | 0o600,
                            st_mtime=datetime.now(UTC).timestamp(), st_size=10)

    def entries():
        for ordinal in range(740000):
            clock[0] += 0.0001
            yield SimpleNamespace(name=f"{ordinal:064x}.jsonl", stat=lambda **_: fresh)
        for path in (live, garbage):
            yield SimpleNamespace(name=path.name, stat=lambda path=path, **_: path.stat())

    def scandir(root):
        if root == tmp_path:
            opened.append(root)
            return entries()
        return real_scandir(root)

    monkeypatch.setattr(export_gc.os, "scandir", scandir)
    monkeypatch.setattr(export_gc.time, "monotonic", lambda: clock[0])
    scan, reports = ArtifactGCScan(), []
    try:
        for _ in range(160):
            report = collect_export_artifacts(
                publisher_repository, tmp_path, mode="delete", deadline=clock[0] + 0.5,
                scan=scan,
            )
            reports.append(report)
            if report["complete"]:
                break
        assert reports[-1]["complete"]
        assert sum(r["visited"] for r in reports) == 740002
        assert max(r["visited"] for r in reports) <= 5003
        assert len(opened) == 1  # tick마다 앞 5천 개로 돌아가면 끝의 파일에 닿지 못한다.
        assert live.exists() and not garbage.exists()
        assert sum(r["deleted_bytes"] for r in reports) == len(b"obsolete artifact\n")
    finally:
        scan.close()


def test_reference_timeout_keeps_files_and_retries_the_same_pending_batch(
    publisher_repository, tmp_path, monkeypatch,
):
    target = _old(tmp_path, "a" * 64)
    scan = ArtifactGCScan()
    monkeypatch.setattr(export_gc, "GC_REFERENCE_SECONDS", 0.05)
    try:
        # 운영 timeout을 실제 PostgreSQL 테이블 잠금으로 만든다. 참조를 읽지 못한 파일은
        # 비참조로 간주하면 안 되며 guard도 반드시 풀어야 한다.
        with publisher_repository.engine.begin() as blocker:
            blocker.execute(text("LOCK TABLE weather_export_pending_refs IN ACCESS EXCLUSIVE MODE"))
            with pytest.raises(DBAPIError):
                collect_export_artifacts(
                    publisher_repository, tmp_path, mode="delete", deadline=float("inf"), scan=scan,
                )
            assert target.exists() and scan.pending
            with ExportArtifactStore(tmp_path).guard(timeout_seconds=0.1):
                pass
        report = collect_export_artifacts(
            publisher_repository, tmp_path, mode="delete", deadline=float("inf"), scan=scan,
        )
        assert report["complete"] and report["deleted"] == 1
        assert not target.exists()
        assert publisher_repository.engine.pool.checkedout() == 0
    finally:
        scan.close()


def test_reference_added_after_listing_is_kept_under_the_existing_guard(
    publisher_repository, tmp_path, monkeypatch,
):
    target = _old(tmp_path, "b" * 64)
    original, inserted = ExportArtifactStore.guard, []

    @contextmanager
    def writer_then_gc(self, **kwargs):
        if not inserted:
            with original(self, **kwargs):
                _pin(publisher_repository, "b" * 64)
                inserted.append(True)
        with original(self, **kwargs):
            yield

    monkeypatch.setattr(ExportArtifactStore, "guard", writer_then_gc)
    report = collect_export_artifacts(
        publisher_repository, tmp_path, mode="delete", deadline=float("inf"),
    )
    assert target.exists() and report["live"] == 1 and report["deleted"] == 0


def test_abort_after_unlink_releases_guard_and_continues_without_losing_candidates(
    publisher_repository, tmp_path, monkeypatch,
):
    targets = [_old(tmp_path, c * 64) for c in "abc"]
    real_unlink, aborted, scan = os.unlink, [], ArtifactGCScan()

    def interrupt(path, *args, **kwargs):
        result = real_unlink(path, *args, **kwargs)
        if not aborted:
            aborted.append(True)
            raise KeyboardInterrupt("synthetic cancellation")
        return result

    monkeypatch.setattr(export_gc.os, "unlink", interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):
            collect_export_artifacts(
                publisher_repository, tmp_path, mode="delete", deadline=float("inf"), scan=scan,
            )
        assert sum(path.exists() for path in targets) == 2
        with ExportArtifactStore(tmp_path).guard(timeout_seconds=0.1):
            pass
        report = collect_export_artifacts(
            publisher_repository, tmp_path, mode="delete", deadline=float("inf"), scan=scan,
        )
        assert report["complete"] and report["deleted"] == 2
        assert all(not path.exists() for path in targets)
    finally:
        scan.close()


def test_scoped_reference_check_unlinks_with_large_unrelated_generation_history(
    publisher_repository, tmp_path,
):
    # 실제 PostgreSQL에 긴 과거 이력을 만든다. 후보와 무관한 전체 live set을 먼저 읽는
    # 구현은 이 문장 예산에 의존하지만, digest 색인에서 찾으면 후보 하나만 확인한다.
    with publisher_repository.engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO weather_export_generations
                (generation_id, attempt_id, owner_id, seal_digest, namespace,
                 data_revision_at, source_collected_at, content_digest, point_count, created_at)
            SELECT md5(i::text), md5('attempt-' || i::text), repeat('o', 36), repeat('0', 64),
                   'history', now() - interval '48 hours', now() - interval '48 hours',
                   repeat('1', 64), 1, now() - interval '48 hours'
            FROM generate_series(1, 50000) AS i
        """))
        connection.execute(text("""
            INSERT INTO weather_export_generation_chunks
                (generation_id, ordinal, artifact_digest, byte_count, point_count)
            SELECT generation_id, 0, repeat('d', 64), 10, 1
            FROM weather_export_generations
        """))
        connection.execute(text("ANALYZE weather_export_generations"))
        connection.execute(text("ANALYZE weather_export_generation_chunks"))
    target = _old(tmp_path, "f" * 64)
    live = _old(tmp_path, "d" * 64)
    report = collect_export_artifacts(
        publisher_repository, tmp_path, mode="delete", deadline=time.monotonic() + 1.5,
    )
    assert report["complete"] and report["deleted"] == 1 and not target.exists()
    assert live.exists() and report["live"] == 1


def test_scoped_gc_with_large_receipt_history_preserves_only_pending_files(
    publisher_repository,
    tmp_path,
):
    # receipt heap에 긴 이력이 있어도 digest에서 시작한다. 실패/공개 완료 receipt는
    # 보호하지 않고 preparing/sealed의 미공개 receipt만 남긴다.
    repo = publisher_repository
    heads = ExportHeads(repo)
    index = next(
        i
        for i in heads.lifecycle.chunks.indexes
        if i.name == "ix_weather_export_chunks_artifact_digest"
    )
    index.create(repo.engine, checkfirst=True)
    with repo.engine.begin() as c:
        c.execute(
            text("""
            INSERT INTO weather_export_attempts
                (attempt_id,run_id,owner_id,scope_key,execution_mode,planned_sources,
                 plan_digest,status,created_at,published_at)
            SELECT md5('receipt-' || i::text),'history',repeat('o',36),'scope','native',
                   '[]'::json,repeat('0',64),
                   CASE WHEN i % 3 = 0 THEN 'failed'
                        WHEN i % 3 = 1 THEN 'preparing' ELSE 'sealed' END,
                   now()-interval '48 hours',CASE WHEN i % 7 = 0 THEN now() END
            FROM generate_series(1,50000) AS i
        """)
        )
        c.execute(
            text("""
            INSERT INTO weather_export_chunks
                (attempt_id,chunk_key,artifact_digest,byte_count,point_count,
                 covered_sources,source_collected_at,recorded_at)
            SELECT md5('receipt-' || i::text),'chunk',lpad(to_hex(i),64,'0'),
                   10,1,'[]'::json,now()-interval '48 hours',now()-interval '48 hours'
            FROM generate_series(1,50000) AS i
        """)
        )
        c.execute(text("ANALYZE weather_export_attempts"))
        c.execute(text("ANALYZE weather_export_chunks"))
    expected = {f"{i:064x}" for i in range(1, 257) if i % 3 != 0 and i % 7 != 0}
    for i in range(1, 257):
        _old(tmp_path, f"{i:064x}")
    report = collect_export_artifacts(repo, tmp_path, mode="delete", deadline=float("inf"))
    assert report["complete"] and report["live"] == len(expected)
    assert report["deleted"] == 256 - len(expected)
    assert {p.stem for p in tmp_path.glob("*.jsonl")} == expected
    with repo.engine.connect() as c:
        assert c.exec_driver_sql("SHOW jit").scalar() == "on"


def test_gc_defers_tail_batch_without_shortening_reference_budget(
    publisher_repository, tmp_path, monkeypatch,
):
    targets = [_old(tmp_path, c * 64) for c in "ab"]
    clock, calls = [100.0], []
    original = export_gc.live_artifact_digests

    def bounded_reference(*args, **kwargs):
        # 정상 참조 조회가 허용된 3초를 사용한 뒤에도 다음 묶음에 2.5초만
        # 주며 시작하면 안 된다. 다음 호출에서는 같은 pending을 다시 판정한다.
        assert kwargs["timeout_seconds"] >= export_gc.GC_REFERENCE_SECONDS
        calls.append(kwargs["digests"])
        result = original(*args, **kwargs)
        clock[0] += export_gc.GC_REFERENCE_SECONDS
        return result

    monkeypatch.setattr(export_gc.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(export_gc, "live_artifact_digests", bounded_reference)
    monkeypatch.setattr(export_gc, "UNLINK_BATCH", 1)
    scan = ArtifactGCScan()
    try:
        first = collect_export_artifacts(
            publisher_repository, tmp_path, mode="delete", deadline=float("inf"), scan=scan,
        )
        assert first["deleted"] == 1 and first["pending"] == 1 and not first["complete"]
        assert len(calls) == 1 and sum(p.exists() for p in targets) == 1
        second = collect_export_artifacts(
            publisher_repository, tmp_path, mode="delete", deadline=float("inf"), scan=scan,
        )
        assert second["deleted"] == 1 and second["complete"] and not second["pending"]
        assert len(calls) == 2 and all(not p.exists() for p in targets)
    finally:
        scan.close()
