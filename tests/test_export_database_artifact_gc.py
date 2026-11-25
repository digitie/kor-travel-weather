"""DB GC의 참조 재확인, rollback, keyset·tail budget을 실제 삭제 결과로 확인한다."""

import hashlib
import os
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from kortravelweather import export_database_gc as gc
from kortravelweather.export_artifacts import ExportGuardTimeout
from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore
from kortravelweather.export_gc import ArtifactGCScan

NOW = datetime(2026, 11, 18, tzinfo=UTC)


@pytest.fixture
def repo():
    engine = create_engine("sqlite://")
    repository = SimpleNamespace(engine=engine)
    store = DatabaseExportArtifactStore(repository)
    store.table.create(engine)
    yield repository
    engine.dispose()


def put(repo, number, *, age=2):
    payload = ('{"n":' + str(number) + '}\n').encode()
    digest = hashlib.sha256(payload).hexdigest()
    table = DatabaseExportArtifactStore(repo).table
    with repo.engine.begin() as connection:
        connection.execute(table.insert().values(
            digest=digest, payload=payload, byte_count=len(payload), point_count=1,
            created_at=NOW - timedelta(hours=age),
        ))
    return digest, len(payload)


def present(repo):
    table = DatabaseExportArtifactStore(repo).table
    with repo.engine.connect() as connection:
        return set(connection.scalars(select(table.c.digest)))


def collect(repo, **kwargs):
    return gc.collect_database_export_artifacts(
        repo, deadline=time.monotonic() + 2, now=NOW, **kwargs,
    )


def test_gc_preserves_live_and_recent_and_dry_run_never_deletes(repo, monkeypatch):
    live, _ = put(repo, 1)
    stale, size = put(repo, 2)
    recent, _ = put(repo, 3, age=.5)
    monkeypatch.setattr(gc, "live_artifact_digests", lambda *a, **kw: {live})
    report = collect(repo)
    assert report == dict(
        mode="dry_run", visited=2, scanned=2, live=1, eligible=1, eligible_bytes=size,
        deleted=0, deleted_bytes=0, errors=0, complete=True, pending=0, statement_timeouts=0,
    )
    assert present(repo) == {live, stale, recent}
    report = collect(repo, mode="delete")
    assert report["deleted"] == 1 and report["deleted_bytes"] == size
    assert present(repo) == {live, recent}


def test_failed_reference_keeps_same_pending_batch_for_fresh_retry(repo, monkeypatch):
    digest, _ = put(repo, 1)
    scan = ArtifactGCScan()
    def failed(*a, **kw):
        raise RuntimeError("reference query cancelled")
    monkeypatch.setattr(gc, "live_artifact_digests", failed)
    with pytest.raises(RuntimeError, match="cancelled"):
        collect(repo, mode="delete", scan=scan)
    assert present(repo) == {digest}
    assert len(scan._database_artifact_scan.pending) == 1
    # retry 전에 새 참조가 생겼으면 같은 pending을 live로 재판정한다.
    monkeypatch.setattr(gc, "live_artifact_digests", lambda *a, **kw: {digest})
    result = collect(repo, mode="delete", scan=scan)
    assert result["live"] == 1 and result["deleted"] == 0 and result["complete"]
    assert present(repo) == {digest}


def test_delete_rollback_keeps_pending_and_counts_only_committed_deletes(repo, monkeypatch):
    digest, size = put(repo, 1)
    scan = gc.DatabaseArtifactGCScan()
    monkeypatch.setattr(gc, "live_artifact_digests", lambda *a, **kw: set())
    original = DatabaseExportArtifactStore.delete_many
    def failed(self, digests, *, created_before):
        original(self, digests, created_before=created_before)
        raise RuntimeError("failed before guard commit")
    monkeypatch.setattr(DatabaseExportArtifactStore, "delete_many", failed)
    with pytest.raises(RuntimeError, match="commit"):
        collect(repo, mode="delete", scan=scan)
    assert present(repo) == {digest} and len(scan.pending) == 1
    monkeypatch.setattr(DatabaseExportArtifactStore, "delete_many", original)
    result = collect(repo, mode="delete", scan=scan)
    assert result["deleted"] == 1 and result["deleted_bytes"] == size
    assert not present(repo)


def _timeout(sqlstate="57014"):
    return OperationalError("SELECT gc_candidates", {}, SimpleNamespace(sqlstate=sqlstate))


def test_statement_timeout_halves_the_batch_without_consuming_it(repo, monkeypatch):
    digests = [put(repo, i)[0] for i in range(40)]
    scan = gc.DatabaseArtifactGCScan(reference_batch=32)
    sizes = []

    def live(*a, **kw):
        sizes.append(len(kw["digests"]))
        if len(sizes) == 1:
            raise _timeout()
        return set()

    monkeypatch.setattr(gc, "live_artifact_digests", live)
    result = collect(repo, mode="delete", scan=scan)
    # 취소된 32개 batch를 소비하지 않고 16개씩 다시 판정한다(공유·배타 판정 각각).
    assert sizes == [32, 16, 16, 16, 16, 8, 8]
    assert scan.reference_batch == 16 and result["statement_timeouts"] == 1
    assert result["deleted"] == 40 and result["complete"] and not present(repo) & set(digests)


@pytest.mark.parametrize("error", [_timeout("55P03"), RuntimeError("other")])
def test_only_statement_timeout_shrinks_the_batch(repo, monkeypatch, error):
    put(repo, 1)
    scan = gc.DatabaseArtifactGCScan()

    def live(*a, **kw):
        raise error

    monkeypatch.setattr(gc, "live_artifact_digests", live)
    with pytest.raises(type(error)):
        collect(repo, mode="delete", scan=scan)
    assert scan.reference_batch == gc.UNLINK_BATCH and len(scan.pending) == 1


def test_smallest_batch_timeout_is_raised_with_pending_kept(repo, monkeypatch):
    put(repo, 1)
    scan = gc.DatabaseArtifactGCScan(reference_batch=gc.MIN_REFERENCE_BATCH)

    def live(*a, **kw):
        raise _timeout()

    monkeypatch.setattr(gc, "live_artifact_digests", live)
    with pytest.raises(OperationalError) as raised:
        collect(repo, mode="delete", scan=scan)
    assert raised.value.orig.sqlstate == "57014" and len(scan.pending) == 1


@pytest.mark.parametrize("size", [0, gc.UNLINK_BATCH + 1])
def test_reference_batch_is_validated(size):
    with pytest.raises(ValueError):
        gc.DatabaseArtifactGCScan(reference_batch=size)


def test_busy_guard_preserves_pending(repo, monkeypatch):
    digest, _ = put(repo, 1)
    scan = gc.DatabaseArtifactGCScan()
    @contextmanager
    def busy(*a, **kw):
        raise ExportGuardTimeout("busy")
        yield
    monkeypatch.setattr(DatabaseExportArtifactStore, "guard", busy)
    result = collect(repo, mode="delete", scan=scan)
    assert result["pending"] == 1 and not result["complete"]
    assert present(repo) == {digest}


def test_keyset_continues_equal_timestamps_and_resets_after_full_pass(repo, monkeypatch):
    monkeypatch.setattr(gc, "MAX_SCAN_CANDIDATES", 2)
    monkeypatch.setattr(gc, "live_artifact_digests", lambda *a, **kw: set())
    expected = {put(repo, i)[0] for i in range(5)}
    scan = gc.DatabaseArtifactGCScan()
    reports = [collect(repo, mode="delete", scan=scan) for _ in range(3)]
    assert [r["deleted"] for r in reports] == [2, 2, 1]
    assert [r["complete"] for r in reports] == [False, False, True]
    assert sum(r["scanned"] for r in reports) == len(expected)
    assert not present(repo)
    digest, _ = put(repo, 6)
    assert collect(repo, mode="delete", scan=scan)["deleted"] == 1
    assert digest not in present(repo)


def test_reference_is_inside_guard_and_no_tail_batch_starts(repo, monkeypatch):
    for i in range(257):
        put(repo, i)
    scan = gc.DatabaseArtifactGCScan()
    clock = [0.0]
    monkeypatch.setattr(gc.time, "monotonic", lambda: clock[0])
    held = []
    original = DatabaseExportArtifactStore.guard
    @contextmanager
    def guarded(self, **kwargs):
        with original(self, **kwargs):
            held.append(kwargs.get("shared", False))
            try:
                yield
            finally:
                held.pop()
    calls = []
    def live(*a, **kw):
        assert len(held) == 1 and kw["timeout_seconds"] == 3
        calls.append((held[0], kw["digests"]))
        clock[0] += 1
        return set()
    monkeypatch.setattr(DatabaseExportArtifactStore, "guard", guarded)
    monkeypatch.setattr(gc, "live_artifact_digests", live)
    result = collect(repo, mode="delete", scan=scan)
    # 공유 guard에서 batch 전체를 판정하고, 지울 대상만 배타 guard 안에서 다시 판정한다.
    assert [(shared, len(digests)) for shared, digests in calls] == [(True, 256), (False, 256)]
    assert result["deleted"] == 256 and result["pending"] == 1
    assert not result["complete"] and len(present(repo)) == 1


def test_protected_batch_never_takes_the_exclusive_guard(repo, monkeypatch):
    digests = [put(repo, i)[0] for i in range(3)]
    kinds = []
    original = DatabaseExportArtifactStore.guard
    @contextmanager
    def guarded(self, **kwargs):
        kinds.append(kwargs.get("shared", False))
        with original(self, **kwargs):
            yield
    monkeypatch.setattr(DatabaseExportArtifactStore, "guard", guarded)
    monkeypatch.setattr(gc, "live_artifact_digests", lambda *a, **kw: set(digests))
    result = collect(repo, mode="delete")
    assert kinds == [True] and result["live"] == 3 and result["deleted"] == 0
    assert present(repo) == set(digests)


@pytest.mark.parametrize("kwargs", [
    {"mode": "unknown"}, {"retention": timedelta(minutes=59)},
    {"grace": timedelta(minutes=4)}, {"delete_budget_seconds": 0},
    {"delete_budget_seconds": 31},
])
def test_gc_rejects_unsafe_policy(repo, kwargs):
    with pytest.raises(ValueError):
        collect(repo, **kwargs)


def test_actual_postgresql_live_reference_and_guard_transaction():
    from kortravelweather.export_daily_store import daily_summary_tables
    from kortravelweather.export_heads import ExportHeads
    from kortravelweather.export_inventory import export_inventory_table
    from kortravelweather.export_snapshots import export_snapshot_tables
    from kortravelweather.repository import Base

    url = os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"]
    admin = create_engine(url)
    schema = "db_gc_" + uuid.uuid4().hex
    with admin.begin() as connection:
        connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    repository = SimpleNamespace(engine=engine, _session_factory=sessionmaker(engine))
    try:
        heads = ExportHeads(repository)
        daily_summary_tables(Base.metadata)
        export_inventory_table(Base.metadata)
        export_snapshot_tables(Base.metadata)
        Base.metadata.create_all(engine)
        pinned, _ = put(repository, 1)
        stale, size = put(repository, 2)
        with engine.begin() as connection:
            connection.execute(heads.pins.insert().values(
                attempt_id=str(uuid.uuid4()), chunk_key="c" * 64,
                artifact_digest=pinned, created_at=NOW,
            ))
        result = collect(repository, mode="delete")
        assert result["live"] == 1 and result["deleted"] == 1
        assert result["deleted_bytes"] == size and result["complete"]
        assert present(repository) == {pinned} and stale not in present(repository)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
        admin.dispose()

@pytest.fixture
def postgres_gc_repo(weather_test_database_url):
    """기존 global guard와 실제 live predicate를 쓰는 전용 PostgreSQL schema."""
    from kortravelweather.export_daily_store import daily_summary_tables
    from kortravelweather.export_heads import ExportHeads
    from kortravelweather.export_inventory import export_inventory_table
    from kortravelweather.export_snapshots import export_snapshot_tables
    from kortravelweather.repository import Base

    admin = create_engine(weather_test_database_url)
    schema = "gc_capture_" + uuid.uuid4().hex
    with admin.begin() as connection:
        connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
    engine = create_engine(
        weather_test_database_url, pool_size=3, max_overflow=0,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    repository = SimpleNamespace(engine=engine, _session_factory=sessionmaker(engine))
    try:
        heads = ExportHeads(repository)
        daily_summary_tables(Base.metadata)
        export_inventory_table(Base.metadata)
        export_snapshot_tables(Base.metadata)
        Base.metadata.create_all(engine)
        digest, _ = put(repository, 71)
        with engine.begin() as connection:
            connection.execute(heads.pins.insert().values(
                attempt_id=str(uuid.uuid4()), chunk_key="d" * 64,
                artifact_digest=digest, created_at=NOW,
            ))
        yield repository, digest, heads.pins
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
        admin.dispose()


@contextmanager
def _slow_real_gc(repository, monkeypatch, mode, *, delay=1.35, exclusive_only=False):
    """기존3초 reference 창 안의 정상 GC를 다른 실제 DB 연결에서 실행한다.

    ``exclusive_only``면 공유 guard의 첫 판정은 그대로 두고, 배타 guard 안의 재판정만
    늦추고 그때 진입을 알린다(delete가 실제로 지우는 구간).
    """
    from threading import Event, Thread

    from kortravelweather import export_database_artifacts

    entered, finished = Event(), Event()
    report, errors = [], []
    original = gc.live_artifact_digests

    def slow_reference(*args, **kwargs):
        assert kwargs["timeout_seconds"] == 3
        if exclusive_only and export_database_artifacts._context.guards[repository.engine][1]:
            return original(*args, **kwargs)
        entered.set()
        assert 0 < delay < kwargs["timeout_seconds"]
        time.sleep(delay)
        return original(*args, **kwargs)

    monkeypatch.setattr(gc, "live_artifact_digests", slow_reference)

    def run():
        try:
            report.append(collect(repository, mode=mode))
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()

    worker = Thread(target=run)
    worker.start()
    try:
        assert entered.wait(5), "GC가 실제 reference 구간에 도달해야 한다"
        yield report, finished
    finally:
        worker.join(8)
        assert not worker.is_alive()
        assert not errors, [type(error).__name__ for error in errors]


@pytest.mark.parametrize("mode", ["dry_run", "delete"])
def test_postgresql_gc_capture_mode_preserves_live_pin_and_transaction(
    postgres_gc_repo, monkeypatch, mode,
):
    from kortravelweather.export_artifacts import PreparedExportChunk

    repository, pinned, pins = postgres_gc_repo
    store = DatabaseExportArtifactStore(repository)
    payload = b'{"cold_capture":true}\n'
    chunk = PreparedExportChunk(payload, hashlib.sha256(payload).hexdigest(), 1, NOW)
    with _slow_real_gc(repository, monkeypatch, mode) as (reports, finished):
        with repository._session_factory.begin() as session:
            store.record_in_transaction(session, chunk)
        # 참조 판정은 delete에서도 공유 guard다. 보호 행뿐인 batch는 capture를 막지 않는다.
        assert not finished.is_set(), "공유 관측은 capture를 막지 않는다"
    assert reports[0]["live"] == 1 and reports[0]["deleted"] == 0
    assert chunk.digest in present(repository)
    assert pinned in present(repository)
    with repository.engine.connect() as connection:
        assert connection.scalar(select(pins.c.artifact_digest)) == pinned


def test_postgresql_delete_rechecks_victims_inside_the_exclusive_guard(
    postgres_gc_repo, monkeypatch,
):
    from kortravelweather import export_database_artifacts as artifacts

    repository, pinned, pins = postgres_gc_repo
    orphan, _ = put(repository, 72)
    original = gc.live_artifact_digests
    seen = []

    def racing(*args, **kwargs):
        shared = artifacts._context.guards[repository.engine][1]
        result = original(*args, **kwargs)
        seen.append((shared, sorted(kwargs["digests"]), sorted(result)))
        if len(seen) == 1:
            # 공유 판정 뒤·배타 guard 전에 다른 writer가 orphan을 참조하고 commit했다.
            with repository.engine.begin() as connection:
                connection.execute(pins.insert().values(
                    attempt_id=str(uuid.uuid4()), chunk_key="e" * 64,
                    artifact_digest=orphan, created_at=NOW,
                ))
        return result

    monkeypatch.setattr(gc, "live_artifact_digests", racing)
    result = collect(repository, mode="delete")
    assert seen == [
        (True, sorted([pinned, orphan]), [pinned]),
        # 배타 guard 안에서 지울 대상만 다시 보고, 새 참조를 찾아 지우지 않는다.
        (False, [orphan], [orphan]),
    ]
    assert result["live"] == 2 and result["eligible"] == 0 and result["deleted"] == 0
    assert present(repository) == {pinned, orphan}


def test_postgresql_dry_run_shared_guard_still_excludes_deletion(
    postgres_gc_repo, monkeypatch,
):
    repository, pinned, _pins = postgres_gc_repo
    store = DatabaseExportArtifactStore(repository)
    with (
        _slow_real_gc(repository, monkeypatch, "dry_run"),
        pytest.raises(ExportGuardTimeout),
        store.guard(timeout_seconds=.05, holder="competing-delete"),
    ):
        pytest.fail("관측 중인 artifact를 삭제할 배타 guard가 열리면 안 된다")
    assert pinned in present(repository)
