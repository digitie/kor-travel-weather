"""종류 projection의 실제 PostgreSQL backfill·재실행·잠금 상한을 확인한다."""

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
        "snapshot_kind_migration", Path(__file__).parents[1]
        / "alembic/versions/0036_snapshot_kind_projection.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    schema = "snapshot_kind_" + uuid.uuid4().hex
    with engine.connect() as connection:
        connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
        connection.exec_driver_sql(f"SET search_path={schema},public")
        connection.exec_driver_sql(
            "CREATE TABLE weather_export_snapshots (snapshot_id text PRIMARY KEY,"
            "issue_order bigint GENERATED ALWAYS AS IDENTITY,manifest json NOT NULL)"
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


def test_kind_projection_backfills_without_modifying_manifest_and_is_retryable(database):
    migration, c, _, _ = database
    manifests = [{}, {"selection": None}, {"selection": {"kind": None}},
                 {"selection": {"kind": "locations"}, "locations": ["한글"]},
                 {"selection": {"kind": "forecasts", "style": "short"}}]
    for n, manifest in enumerate(manifests):
        c.execute(text("INSERT INTO weather_export_snapshots(snapshot_id,manifest) "
                       "VALUES (:id,CAST(:manifest AS json))"),
                  {"id": str(n), "manifest": json.dumps(manifest)})
    c.commit()
    with Operations.context(MigrationContext.configure(c)):
        migration.upgrade()
    rows = c.execute(text("SELECT manifest,selection_kind FROM weather_export_snapshots "
                          "ORDER BY issue_order")).all()
    assert [row[0] for row in rows] == manifests
    assert [row[1] for row in rows] == [None, None, None, "locations", "forecasts"]
    table = migration.target(c)
    index = migration.check_index(c, table)
    c.commit()
    with Operations.context(MigrationContext.configure(c)):
        migration.upgrade()
    assert dict(migration.check_index(c, migration.target(c))) == dict(index)
    c.execute(text("UPDATE weather_export_snapshots SET manifest=:manifest WHERE snapshot_id='0'"),
              {"manifest": json.dumps({"selection": {"kind": "locations"}})})
    assert c.scalar(text("SELECT selection_kind FROM weather_export_snapshots "
                         "WHERE snapshot_id='0'")) == "locations"
    c.commit()
    with Operations.context(MigrationContext.configure(c)):
        migration.downgrade()
    assert c.scalar(text("SELECT count(*) FROM weather_export_snapshots")) == len(manifests)


def test_kind_projection_respects_shorter_lock_timeout_and_restores_settings(database):
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
    assert migration.check_column(c, migration.target(c)) is None
