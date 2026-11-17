"""불변 DB artifact migration을 SQLite/실제 PostgreSQL 양쪽에서 확인한다."""

import hashlib
import importlib.util
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, create_engine, inspect, select
from sqlalchemy.exc import IntegrityError

from kortravelweather.export_database_artifacts import export_artifact_table


@pytest.fixture(params=["sqlite", "postgresql"])
def migrated(request):
    spec = importlib.util.spec_from_file_location(
        "db_artifact_migration",
        Path(__file__).parents[1] / "alembic/versions/0042_export_database_artifacts.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    url = "sqlite://" if request.param == "sqlite" else os.environ[
        "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"
    ]
    engine = create_engine(url)
    schema = "db_artifact_migration_" + uuid.uuid4().hex
    with engine.connect() as connection:
        if request.param == "postgresql":
            connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
            connection.exec_driver_sql(f"SET search_path={schema}")
            connection.commit()
        with connection.begin(), Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        try:
            yield migration, connection
        finally:
            connection.rollback()
            if request.param == "postgresql":
                connection.exec_driver_sql("SET search_path=public")
                connection.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
                connection.commit()
    engine.dispose()


def _row():
    payload = b'{"value_number":"1.25"}\n'
    return dict(
        digest=hashlib.sha256(payload).hexdigest(), payload=payload,
        byte_count=len(payload), point_count=1, created_at=datetime.now(UTC),
    )


def test_migration_schema_matches_runtime_model(migrated):
    migration, connection = migrated
    model = export_artifact_table(MetaData())
    inspector = inspect(connection)
    columns = inspector.get_columns(migration.TABLE)
    assert [c["name"] for c in columns] == list(model.c.keys())
    assert all(not c["nullable"] for c in columns)
    assert inspector.get_pk_constraint(migration.TABLE)["constrained_columns"] == ["digest"]
    assert not inspector.get_foreign_keys(migration.TABLE)
    assert next(i for i in inspector.get_indexes(migration.TABLE)
                if i["name"] == migration.INDEX)["column_names"] == ["created_at", "digest"]
    checks = {c["name"] for c in inspector.get_check_constraints(migration.TABLE)}
    wanted = {"ck_weather_artifact_bytes", "ck_weather_artifact_points"}
    if connection.dialect.name == "postgresql":
        wanted |= {"ck_weather_artifact_payload_bytes", "ck_weather_artifact_payload_digest"}
    assert checks == wanted


@pytest.mark.parametrize("column", ["payload", "digest", "byte_count", "point_count", "created_at"])
def test_migration_rejects_every_update_but_allows_delete(migrated, column):
    _, connection = migrated
    table = export_artifact_table(MetaData())
    row = _row()
    connection.execute(table.insert().values(**row))
    connection.commit()
    # 값이 같은 UPDATE도 immutable 위반이다.
    with pytest.raises(IntegrityError, match="immutable"), connection.begin():
        connection.execute(table.update().values({column: row[column]}))
    assert connection.execute(select(table.c.payload)).scalar_one() == row["payload"]
    connection.execute(table.delete())
    connection.commit()
    assert connection.execute(select(table.c.digest)).all() == []


@pytest.mark.parametrize("bad", ["size", "digest", "point_count"])
def test_insert_constraints_match_supported_dialect(migrated, bad):
    _, connection = migrated
    table = export_artifact_table(MetaData())
    row = _row()
    if bad == "size":
        row["byte_count"] += 1
    elif bad == "digest":
        row["digest"] = "0" * 64
    else:
        row["point_count"] = 5001
    if connection.dialect.name == "sqlite" and bad in {"size", "digest"}:
        # SQLite에는 의도적으로 PostgreSQL payload 함수를 생성하지 않는다.
        connection.execute(table.insert().values(**row))
        connection.commit()
        assert connection.execute(select(table.c.digest)).scalar_one() == row["digest"]
    else:
        with pytest.raises(IntegrityError), connection.begin():
            connection.execute(table.insert().values(**row))
        assert connection.execute(select(table.c.digest)).all() == []


def test_migration_can_downgrade_and_upgrade_without_orphan_function(migrated):
    migration, connection = migrated
    with connection.begin(), Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
    assert migration.TABLE not in inspect(connection).get_table_names()
    connection.commit()
    with connection.begin(), Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
    assert migration.TABLE in inspect(connection).get_table_names()
