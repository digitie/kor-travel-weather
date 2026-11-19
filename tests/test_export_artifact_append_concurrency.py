"""불변 DB artifact 추가는 읽기·다른 추가와 공존하고 삭제 GC와는 배타적이어야 한다."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

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
