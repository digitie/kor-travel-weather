"""DB snapshot 저장의 원자성·파일 비사용·동시 GC 보호 계약."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
import test_export_heads as head_fixtures
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from kortravelweather.export_artifacts import (
    ExportArtifactError,
    ExportArtifactStore,
    ExportGuardTimeout,
    prepare_export_chunk,
)
from kortravelweather.export_database_artifacts import (
    DatabaseExportArtifactStore,
    export_artifact_store,
)
from kortravelweather.repository import WeatherRepository


@pytest.fixture(scope="module")
def repository():
    repo = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    repo.create_schema()
    yield repo
    repo.engine.dispose()


@pytest.fixture
def store(repository):
    return DatabaseExportArtifactStore(repository)


def _chunk():
    return prepare_export_chunk([
        {"value_id": "sample", "value_number": "1.5", "nested": {"proof": [1, 2]}},
    ], source_collected_at=datetime.now(UTC))


def _read(store, chunk):
    return list(store.read(chunk.digest, byte_count=len(chunk.payload),
                           point_count=chunk.point_count))


def test_database_store_never_creates_files_and_publish_is_idempotent(repository, tmp_path):
    store = export_artifact_store(repository, tmp_path / "does-not-exist")
    chunk = _chunk()
    store.publish(chunk)
    with repository.engine.connect() as connection:
        created = connection.scalar(select(store.table.c.created_at))
    store.publish(chunk)
    with repository.engine.connect() as connection:
        assert connection.execute(select(store.table.c.digest)).all() == [(chunk.digest,)]
        assert connection.scalar(select(store.table.c.created_at)) == created
    assert not (tmp_path / "does-not-exist").exists()
    assert _read(store, chunk)[0]["value_number"] == "1.5"


def test_database_read_copy_cannot_mutate_later_response(store):
    chunk = _chunk()
    store.publish(chunk)
    first = _read(store, chunk)
    first[0]["nested"]["proof"].append(3)
    assert _read(store, chunk)[0]["nested"]["proof"] == [1, 2]


def test_transaction_rollback_does_not_leave_export_snapshot(repository, store):
    chunk = _chunk()
    with pytest.raises(RuntimeError), Session(repository.engine) as session, session.begin():
        store.record_in_transaction(session, chunk)
        assert session.scalar(select(store.table.c.digest)) == chunk.digest
        raise RuntimeError("capture transaction rollback")
    with pytest.raises(ExportArtifactError):
        _read(store, chunk)


def test_file_copy_is_never_used_as_implicit_database_fallback(repository, tmp_path):
    chunk = _chunk()
    ExportArtifactStore(tmp_path).publish(chunk)
    store = export_artifact_store(repository, tmp_path)
    with pytest.raises(ExportArtifactError):
        _read(store, chunk)


def test_database_delete_is_not_hidden_by_decode_cache(store):
    chunk = _chunk()
    store.publish(chunk)
    _read(store, chunk)
    with store.guard(holder="test-delete"):
        count, _ = store.delete_many(
            [chunk.digest], created_before=datetime.max.replace(tzinfo=UTC),
        )
        assert count == 1
    with pytest.raises(ExportArtifactError):
        _read(store, chunk)


def test_shared_guard_blocks_writer_on_another_engine(repository, store):
    engine = create_engine(repository.engine.url)
    other = DatabaseExportArtifactStore(type("Repo", (), {"engine": engine})())
    def attempt():
        with pytest.raises(ExportGuardTimeout), other.guard(timeout_seconds=.1):
            pass
    try:
        with store.guard(shared=True), ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(attempt).result(timeout=3)
        with other.guard(timeout_seconds=.2):
            pass
    finally:
        engine.dispose()


def test_capture_receipt_and_database_snapshot_commit_together(
    repository, store, tmp_path, monkeypatch,
):
    monkeypatch.setattr(head_fixtures, "ExportArtifactStore", lambda root: store)
    descriptor = head_fixtures._descriptor(repository)
    heads, identity, capture, *_ = head_fixtures._candidate(repository, tmp_path, descriptor)
    with repository.engine.connect() as connection:
        receipt = connection.execute(select(heads.lifecycle.chunks).where(
            heads.lifecycle.chunks.c.attempt_id == identity.attempt_id,
        )).mappings().one()
        assert connection.scalar(select(store.table.c.digest)) == receipt["artifact_digest"]
    assert list(tmp_path.iterdir()) == []
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    assert list(tmp_path.iterdir()) == []


def test_failed_snapshot_write_rolls_back_receipt_but_keeps_original_fact(
    repository, store, tmp_path, monkeypatch,
):
    monkeypatch.setattr(head_fixtures, "ExportArtifactStore", lambda root: store)
    def fail(session, chunk):
        raise ExportArtifactError("injected DB snapshot write failure")
    monkeypatch.setattr(store, "record_in_transaction", fail)
    descriptor = head_fixtures._descriptor(repository)
    with pytest.raises(ExportArtifactError, match="source 계획"):
        head_fixtures._candidate(repository, tmp_path, descriptor)
    from kortravelweather.export_heads import ExportHeads

    heads = ExportHeads(repository)
    with repository.engine.connect() as connection:
        assert not connection.execute(select(heads.lifecycle.chunks)).all()
        assert connection.scalar(select(heads.lifecycle.attempts.c.status)) == "failed"
        assert not connection.execute(select(store.table.c.digest)).all()
        assert connection.scalar(text("SELECT count(*) FROM weather_values")) == 1


def test_explicit_migration_imports_only_live_snapshots(repository, store, tmp_path):
    import time

    from kortravelweather.export_artifact_migration import migrate_legacy_export_artifacts

    descriptor = head_fixtures._descriptor(repository)
    heads, identity, capture, *_ = head_fixtures._candidate(repository, tmp_path, descriptor)
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    orphan = _chunk()
    ExportArtifactStore(tmp_path).publish(orphan)
    before = {x.name for x in tmp_path.glob("*.jsonl")}
    report = migrate_legacy_export_artifacts(
        repository, tmp_path, deadline=time.monotonic() + 10, dry_run=False,
    )
    assert report["complete"] and report["imported"] == 1
    assert report["protected"] == 1 and report["files_deleted"] == 0
    with repository.engine.connect() as connection:
        assert connection.scalar(select(store.table.c.digest).where(
            store.table.c.digest == orphan.digest,
        )) is None
        receipt = connection.execute(select(heads.refs)).mappings().one()
    assert {x.name for x in tmp_path.glob("*.jsonl")} == before
    # DB에서 읽는 값은 이전 파일의 검증된 canonical 값과 동일하다.
    assert list(store.read(receipt["artifact_digest"], byte_count=receipt["byte_count"],
                           point_count=receipt["point_count"])) == list(capture.artifacts.read(
                               receipt["artifact_digest"], byte_count=receipt["byte_count"],
                               point_count=receipt["point_count"],
                           ))


def test_explicit_migration_dry_run_does_not_create_database_payload(repository, store, tmp_path):
    import time

    from kortravelweather.export_artifact_migration import migrate_legacy_export_artifacts

    descriptor = head_fixtures._descriptor(repository)
    heads, identity, capture, *_ = head_fixtures._candidate(repository, tmp_path, descriptor)
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    report = migrate_legacy_export_artifacts(
        repository, tmp_path, deadline=time.monotonic() + 10,
    )
    assert report["complete"] and report["would_import"] == 1 and report["imported"] == 0
    with repository.engine.connect() as connection:
        assert not connection.execute(select(store.table.c.digest)).all()


def test_migration_candidate_query_keeps_exactly_the_existing_live_reference_set(
    repository, tmp_path, monkeypatch,
):
    from datetime import timedelta

    from test_export_artifact_gc import _scenario

    from kortravelweather.export_gc import live_artifact_statement

    keep, drop = _scenario(repository, tmp_path, monkeypatch)
    now = datetime.now(UTC) + timedelta(hours=6)
    query = live_artifact_statement(
        repository, now=now, retention=timedelta(hours=1), digests=list(keep | drop),
    )
    with repository.engine.connect() as connection:
        actual = set(connection.scalars(query))
    assert actual == keep


def test_create_schema_blocks_snapshot_updates(repository, store):
    from sqlalchemy.exc import IntegrityError

    chunk = _chunk()
    store.publish(chunk)
    with pytest.raises(IntegrityError, match="immutable"), repository.engine.begin() as connection:
        connection.execute(store.table.update().values(created_at=datetime.now(UTC)))
    assert _read(store, chunk)


def test_migration_prefix_allows_bounded_independent_shards(repository, store, tmp_path):
    import time

    from kortravelweather.export_artifact_migration import migrate_legacy_export_artifacts

    descriptor = head_fixtures._descriptor(repository)
    heads, identity, capture, *_ = head_fixtures._candidate(repository, tmp_path, descriptor)
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    with repository.engine.connect() as connection:
        digest = connection.scalar(select(heads.refs.c.artifact_digest))
    wrong = "0" if digest[0] != "0" else "1"
    skipped = migrate_legacy_export_artifacts(
        repository, tmp_path, deadline=time.monotonic() + 10, dry_run=False, digest_prefix=wrong,
    )
    assert skipped["complete"] and skipped["imported"] == 0
    copied = migrate_legacy_export_artifacts(
        repository, tmp_path, deadline=time.monotonic() + 10, dry_run=False,
        digest_prefix=digest[:2],
    )
    assert copied["complete"] and copied["imported"] == 1


@pytest.mark.parametrize("prefix", ["G", "xyz", "a/b"])
def test_migration_rejects_non_digest_shard(repository, tmp_path, prefix):
    import time

    from kortravelweather.export_artifact_migration import migrate_legacy_export_artifacts

    with pytest.raises(ValueError, match="prefix"):
        migrate_legacy_export_artifacts(
            repository, tmp_path, deadline=time.monotonic() + 10, digest_prefix=prefix,
        )
