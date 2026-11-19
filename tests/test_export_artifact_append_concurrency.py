"""불변 DB artifact 추가는 읽기·다른 추가와 공존하고 삭제 GC와는 배타적이어야 한다."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import test_export_heads as head_fixtures
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from kortravelweather import export_database_artifacts as artifact_module
from kortravelweather.export_artifacts import ExportGuardTimeout, prepare_export_chunk
from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore
from kortravelweather.repository import WeatherRepository


@pytest.fixture(scope="module")
def repository():
    repo = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    repo.create_schema()
    yield repo
    repo.engine.dispose()


@pytest.fixture
def stores(repository):
    engine = create_engine(repository.engine.url)
    try:
        yield (
            DatabaseExportArtifactStore(repository),
            DatabaseExportArtifactStore(SimpleNamespace(engine=engine)),
        )
    finally:
        engine.dispose()


def _chunk(key):
    return prepare_export_chunk(
        [{"value_id": key, "value_number": "1.5"}],
        source_collected_at=datetime.now(UTC),
    )


def _append(store, chunk):
    with Session(store.engine) as session, session.begin():
        store.record_in_transaction(session, chunk)


def test_append_commits_while_snapshot_reader_holds_shared_guard(stores):
    reader, writer = stores
    chunk = _chunk("concurrent-reader-append")
    with reader.guard(shared=True), ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(_append, writer, chunk).result(timeout=3)
        reader.present(chunk.digest, byte_count=len(chunk.payload))


def test_independent_append_transactions_do_not_serialize(stores):
    first, second = stores
    one, two = _chunk("first-append"), _chunk("second-append")
    with Session(first.engine) as session, session.begin():
        first.record_in_transaction(session, one)
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(_append, second, two).result(timeout=3)
    with first.engine.connect() as connection:
        assert set(connection.scalars(select(first.table.c.digest))) == {one.digest, two.digest}


def test_gc_exclusive_guard_still_blocks_append(stores):
    collector, writer = stores
    chunk = _chunk("gc-must-exclude-append")

    def blocked():
        with pytest.raises(ExportGuardTimeout):
            _append(writer, chunk)

    with collector.guard(holder="gc"), ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(blocked).result(timeout=3)
    with collector.engine.connect() as connection:
        assert connection.scalar(select(collector.table.c.digest)) is None
    _append(writer, chunk)
    collector.present(chunk.digest, byte_count=len(chunk.payload))


def test_database_publication_can_finish_while_snapshot_reader_holds_guard(
    repository, stores, tmp_path, monkeypatch,
):
    reader, _ = stores
    monkeypatch.setattr(head_fixtures, "ExportArtifactStore", lambda root: reader)
    candidates = [head_fixtures._candidate(
        repository, tmp_path, head_fixtures._descriptor(repository),
    ) for _ in range(2)]
    with reader.guard(shared=True), ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(heads.promote, identity, capture.artifacts)
                   for heads, identity, capture, *_ in candidates]
        assert [future.result(timeout=8) for future in futures] == ["PUBLISHED", "PUBLISHED"]
    for heads, identity, capture, *_ in candidates:
        assert heads.promote(identity, capture.artifacts) == "ALREADY_COMMITTED"
    with repository.engine.connect() as connection:
        clocks = list(connection.scalars(select(heads.generations.c.created_at)))
        assert len(set(clocks)) == 2
        assert connection.scalar(select(
            heads.metadata.tables["weather_export_publication_clock"].c.published_at,
        )) == max(clocks)


def test_concurrent_publication_of_same_attempt_remains_idempotent(
    repository, stores, tmp_path, monkeypatch,
):
    store, _ = stores
    monkeypatch.setattr(head_fixtures, "ExportArtifactStore", lambda root: store)
    descriptor = head_fixtures._descriptor(repository)
    heads, identity, capture, *_ = head_fixtures._candidate(repository, tmp_path, descriptor)
    locked = heads.lifecycle._locked
    rendezvous = threading.Barrier(2)

    def lock_after_both_read_initial_generation(session, candidate):
        held = getattr(artifact_module._context, "guards", {}).get(repository.engine)
        if held is not None and held[1]:
            # 공유 공개는 시도 행 잠금 전에 두 최초 조회를 함께 허용한다.
            # 기존 배타 공개는 이 지점 전에 두 호출을 직렬화한다.
            rendezvous.wait(timeout=3)
        return locked(session, candidate)

    monkeypatch.setattr(heads.lifecycle, "_locked", lock_after_both_read_initial_generation)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(heads.promote, identity, capture.artifacts) for _ in range(2)]
        outcomes = sorted(future.result(timeout=8) for future in futures)
    assert outcomes == ["ALREADY_COMMITTED", "PUBLISHED"]
    with repository.engine.connect() as connection:
        assert len(connection.execute(select(heads.generations.c.generation_id)).all()) == 1
        assert connection.scalar(select(heads.heads.c.version)) == 1
