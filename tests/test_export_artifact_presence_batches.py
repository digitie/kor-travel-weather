"""운영 크기의 존재 검산은 payload 없이 적은 왕복으로 수행하고 유실은 숨기지 않는다."""

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, event

from kortravelweather.export_artifacts import ExportArtifactError, prepare_export_chunk
from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore
from kortravelweather.repository import WeatherRepository


@pytest.fixture(scope="module")
def repository():
    repo = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    repo.create_schema()
    yield repo
    repo.engine.dispose()


@pytest.fixture
def populated(repository):
    store = DatabaseExportArtifactStore(repository)
    now = datetime.now(UTC)
    chunks = [prepare_export_chunk(
        [{"value_id": f"presence-{index}", "value_number": "1.5"}],
        source_collected_at=now,
    ) for index in range(2911)]
    with repository.engine.begin() as connection:
        connection.execute(store.table.insert(), [{
            "digest": chunk.digest, "payload": chunk.payload,
            "byte_count": len(chunk.payload), "point_count": chunk.point_count,
            "created_at": now,
        } for chunk in chunks])
    return store, [{"artifact_digest": chunk.digest, "byte_count": len(chunk.payload)}
                   for chunk in chunks]


def test_production_sized_presence_uses_few_metadata_queries(repository, populated):
    store, refs = populated
    queries = []

    def observe(connection, cursor, statement, params, context, many):
        if (statement.lstrip().upper().startswith("SELECT")
                and "weather_export_artifacts" in statement):
            queries.append(statement)

    event.listen(repository.engine, "before_cursor_execute", observe)
    try:
        with store.guard(shared=True):
            store.present_many(refs)
    finally:
        event.remove(repository.engine, "before_cursor_execute", observe)
    assert 0 < len(queries) <= 4
    assert all("weather_export_artifacts.payload" not in query for query in queries)


def test_missing_artifact_after_first_batch_still_fails(repository, populated):
    store, refs = populated
    with repository.engine.begin() as connection:
        connection.execute(delete(store.table).where(
            store.table.c.digest == refs[2300]["artifact_digest"],
        ))
    with pytest.raises(ExportArtifactError, match="없거나 손상"):
        store.present_many(refs)
    assert not store.all_present(refs)


def test_conflicting_duplicate_across_batches_still_fails(populated):
    store, refs = populated
    refs.append({**refs[0], "byte_count": refs[0]["byte_count"] + 1})
    with pytest.raises(ExportArtifactError, match="없거나 손상"):
        store.present_many(refs)


def test_invalid_late_reference_is_rejected(populated):
    store, refs = populated
    refs.append({"artifact_digest": "not-a-digest", "byte_count": 1})
    with pytest.raises(ExportArtifactError, match="digest"):
        store.present_many(refs)
