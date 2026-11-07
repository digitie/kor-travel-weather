"""선택 projection의 실제 PostgreSQL backfill·재실행·잠금 상한을 확인한다."""

import importlib.util
import json
import os
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError


@pytest.fixture
def database():
    spec = importlib.util.spec_from_file_location(
        "snapshot_selection_migration", Path(__file__).parents[1]
        / "alembic/versions/0038_snapshot_selection_projection.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    schema = "snapshot_selection_" + uuid.uuid4().hex
    with engine.connect() as connection:
        connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
        connection.exec_driver_sql(f"SET search_path={schema},public")
        connection.exec_driver_sql(
            "CREATE TABLE weather_export_snapshots (snapshot_id text PRIMARY KEY,"
            "issue_order bigint GENERATED ALWAYS AS IDENTITY,manifest json NOT NULL,"
            "selection_kind varchar GENERATED ALWAYS AS "
            "((manifest -> 'selection') ->> 'kind') STORED)"
        )
        connection.commit()
        try:
            yield migration, connection, engine, schema
        finally:
            connection.rollback()
            connection.exec_driver_sql("SET search_path=public")
            connection.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
            connection.commit()
    engine.dispose()


def test_selection_projection_backfill_retry_and_down_preserve_originals(database):
    migration, c, _, _ = database
    manifests = [
        {}, {"selection": None},
        {"selection": {"kind": "current", "signature": "c" * 64}, "padding": "가" * 30000},
        {"selection": {"kind": "forecasts", "style": "short", "since": "2026-11-07",
                       "provider_signature": "p" * 64}},
        {"selection": {"kind": "daily-summaries"}, "daily_resource": "daily-summaries:abc"},
    ]
    for n, manifest in enumerate(manifests):
        c.execute(text("INSERT INTO weather_export_snapshots(snapshot_id,manifest) "
                       "VALUES (:id,CAST(:manifest AS json))"),
                  {"id": str(n), "manifest": json.dumps(manifest)})
    c.commit()
    for _ in range(2):
        with Operations.context(MigrationContext.configure(c)):
            migration.upgrade()
        table = migration.target(c)
        for name in migration.COLUMNS:
            assert migration.check_column(c, table, name) is not None
        for name in migration.INDEXES:
            assert migration.check_index(c, table, name) is not None
        c.commit()
    rows = c.execute(text("SELECT * FROM weather_export_snapshots ORDER BY issue_order"))
    rows = rows.mappings().all()
    assert [r["manifest"] for r in rows] == manifests
    assert [r["selection_signature"] for r in rows] == [None, None, "c" * 64, None, None]
    assert rows[3]["forecast_style"] == "short"
    assert rows[3]["forecast_since"] == "2026-11-07"
    assert rows[3]["forecast_provider_signature"] == "p" * 64
    assert rows[4]["daily_resource"] == "daily-summaries:abc"
    c.execute(text("UPDATE weather_export_snapshots SET manifest=CAST(:manifest AS json) "
                   "WHERE snapshot_id='0'"),
              {"manifest": json.dumps({"selection": {"signature": "new"}})})
    assert c.scalar(text("SELECT selection_signature FROM weather_export_snapshots "
                         "WHERE snapshot_id='0'")) == "new"
    c.commit()
    with Operations.context(MigrationContext.configure(c)):
        migration.downgrade()
    assert c.scalar(text("SELECT count(*) FROM weather_export_snapshots")) == len(manifests)


def test_selection_projection_respects_existing_short_lock_bound(database):
    migration, c, engine, schema = database
    c.exec_driver_sql("SET lock_timeout='100ms'")
    c.exec_driver_sql("SET statement_timeout='2s'")
    c.commit()
    with engine.connect() as blocker, blocker.begin():
        blocker.exec_driver_sql(
            f"LOCK TABLE {schema}.weather_export_snapshots IN ACCESS SHARE MODE"
        )
        with Operations.context(MigrationContext.configure(c)), pytest.raises(OperationalError):
            migration.upgrade()
    assert dict(c.execute(text("SELECT name,setting::bigint FROM pg_settings "
                               "WHERE name IN ('lock_timeout','statement_timeout')")).all()) == {
        "lock_timeout": 100, "statement_timeout": 2000,
    }
    assert all(migration.check_column(c, migration.target(c), name) is None
               for name in migration.COLUMNS)


def test_selection_projection_refuses_wrong_existing_definition(database):
    migration, c, _, _ = database
    c.exec_driver_sql("ALTER TABLE weather_export_snapshots ADD COLUMN selection_signature text")
    c.commit()
    with Operations.context(MigrationContext.configure(c)), pytest.raises(RuntimeError):
        migration.upgrade()
    assert c.scalar(text("SELECT count(*) FROM weather_export_snapshots")) == 0
