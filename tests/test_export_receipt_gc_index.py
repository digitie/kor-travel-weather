"""receipt digest 색인 migration의 재실행·충돌·데이터 보존을 실제 DB에서 확인한다."""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, create_engine

from kortravelweather.export_receipts import export_receipt_tables


@pytest.fixture
def database():
    spec = importlib.util.spec_from_file_location(
        "receipt_gc_index",
        Path(__file__).parents[1] / "alembic/versions/0041_export_receipt_gc_index.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    schema = "receipt_gc_" + uuid.uuid4().hex
    with engine.connect() as c:
        c.exec_driver_sql(f"CREATE SCHEMA {schema}")
        c.exec_driver_sql(f"SET search_path={schema},public")
        c.exec_driver_sql(
            "CREATE TABLE weather_export_chunks (attempt_id text,"
            "chunk_key text, artifact_digest text NOT NULL,"
            "PRIMARY KEY(attempt_id,chunk_key))"
        )
        c.exec_driver_sql("INSERT INTO weather_export_chunks VALUES ('a','c','digest')")
        c.commit()
        try:
            yield migration, c
        finally:
            c.rollback()
            c.exec_driver_sql("SET search_path=public")
            c.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
            c.commit()
    engine.dispose()


def test_receipt_model_has_digest_lookup_index():
    _, chunks = export_receipt_tables(MetaData())
    index = next(i for i in chunks.indexes if i.name == "ix_weather_export_chunks_artifact_digest")
    assert [c.name for c in index.columns] == ["artifact_digest"]
    assert not index.unique


def test_concurrent_migration_reuses_verified_index_and_preserves_rows(database):
    migration, c = database
    c.exec_driver_sql("SET statement_timeout='10s'")
    c.exec_driver_sql("SET lock_timeout='500ms'")
    c.commit()
    with Operations.context(MigrationContext.configure(c)):
        migration.upgrade()
    row = migration.existing_index(c)
    assert row["indisvalid"] and row["indisready"]
    oid = c.exec_driver_sql(
        "SELECT to_regclass('ix_weather_export_chunks_artifact_digest')::oid"
    ).scalar()
    c.commit()
    # 운영에서 먼저 만든 정확한 색인은 정상 upgrade 때 재빌드하지 않는다.
    with Operations.context(MigrationContext.configure(c)):
        migration.upgrade()
    assert (
        c.exec_driver_sql(
            "SELECT to_regclass('ix_weather_export_chunks_artifact_digest')::oid"
        ).scalar()
        == oid
    )
    assert c.exec_driver_sql("SHOW statement_timeout").scalar() == "10s"
    assert c.exec_driver_sql("SHOW lock_timeout").scalar() == "500ms"
    assert c.exec_driver_sql("SELECT * FROM weather_export_chunks").all() == [("a", "c", "digest")]
    c.commit()
    with Operations.context(MigrationContext.configure(c)):
        migration.downgrade()
    assert migration.existing_index(c) is None
    assert c.exec_driver_sql("SELECT * FROM weather_export_chunks").all() == [("a", "c", "digest")]


def test_conflicting_index_is_preserved_and_upgrade_fails(database):
    migration, c = database
    c.exec_driver_sql(
        "CREATE INDEX ix_weather_export_chunks_artifact_digest ON weather_export_chunks(chunk_key)"
    )
    oid = c.exec_driver_sql(
        "SELECT to_regclass('ix_weather_export_chunks_artifact_digest')::oid"
    ).scalar()
    c.commit()
    with (
        Operations.context(MigrationContext.configure(c)),
        pytest.raises(RuntimeError, match="충돌"),
    ):
        migration.upgrade()
    assert (
        c.exec_driver_sql(
            "SELECT to_regclass('ix_weather_export_chunks_artifact_digest')::oid"
        ).scalar()
        == oid
    )
    assert c.exec_driver_sql("SELECT * FROM weather_export_chunks").all() == [("a", "c", "digest")]
