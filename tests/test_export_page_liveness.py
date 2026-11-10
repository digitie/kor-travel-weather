from __future__ import annotations

import importlib.util
import time
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, MetaData, Table, Text, create_engine, delete, select, text
from sqlalchemy.exc import DBAPIError

from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_page_liveness import (
    FUNCTION_NAME,
    TABLE_NAME,
    TARGETS,
    TRIGGER_NAME,
    capture_liveness,
    install_page_liveness,
    register_page_liveness,
)


@pytest.fixture
def owned(weather_test_database_url):
    engine = create_engine(weather_test_database_url)
    schema = "liveness_" + uuid.uuid4().hex
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(f'SET search_path TO "{schema}"')
            connection.commit()
            yield engine, connection, schema
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()


def minimal(connection, *, install=True):
    metadata = MetaData()
    targets = [Table(name, metadata, Column("id", Text, primary_key=True),
                     Column("value", Text)) for name in TARGETS]
    if install:
        register_page_liveness(metadata, targets)
    metadata.create_all(connection)
    return metadata, targets


def test_metadata_installs_exact_three_epochs_and_idempotently_verifies(owned):
    _, connection, schema = owned
    metadata, targets = minimal(connection)
    assert capture_liveness(connection, metadata) == (0, 0, 0)
    assert register_page_liveness(metadata, targets) is metadata.tables[TABLE_NAME]
    install_page_liveness(connection)
    assert connection.scalar(text(
        "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE t.tgname=:name AND n.nspname=:schema"
    ), {"name": TRIGGER_NAME, "schema": schema}) == 3
    assert not metadata.tables[TABLE_NAME].foreign_keys
    assert all(column.nullable is False for column in metadata.tables[TABLE_NAME].columns)
    metadata.drop_all(connection)
    assert connection.scalar(text(
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE p.proname=:name AND n.nspname=:schema"
    ), {"name": FUNCTION_NAME, "schema": schema}) == 0


def test_actual_head_metadata_create_all_order(owned):
    from kortravelweather.export_heads import export_head_tables

    _, connection, _ = owned
    metadata = MetaData()
    first = export_head_tables(metadata)
    assert export_head_tables(metadata) == first
    metadata.create_all(connection)
    assert capture_liveness(connection, metadata) == (0, 0, 0)
    metadata.drop_all(connection)


@pytest.mark.parametrize("index", range(3))
def test_publication_unchanged_statement_delete_and_rollback(owned, index):
    _, connection, _ = owned
    metadata, targets = minimal(connection)
    target = targets[index]
    connection.execute(target.insert(), [{"id": str(i), "value": "before"} for i in range(3)])
    connection.execute(target.update().values(value="after"))
    assert capture_liveness(connection, metadata) == (0, 0, 0)
    connection.commit()
    transaction = connection.begin()
    connection.execute(delete(target))
    expected = [0, 0, 0]
    expected[index] = 1
    assert capture_liveness(connection, metadata) == tuple(expected)
    transaction.rollback()
    assert capture_liveness(connection, metadata) == (0, 0, 0)
    assert len(connection.execute(select(target)).all()) == 3
    # 0행 DELETE도 statement 단위로 보수적 무효화한다.
    connection.execute(delete(target).where(target.c.id == "absent"))
    assert capture_liveness(connection, metadata) == tuple(expected)


def test_truncate_three_targets_is_transactional(owned):
    _, connection, _ = owned
    metadata, targets = minimal(connection)
    for target in targets:
        connection.execute(target.insert().values(id="kept"))
    connection.commit()
    transaction = connection.begin()
    connection.exec_driver_sql("TRUNCATE " + ",".join(TARGETS))
    assert capture_liveness(connection, metadata) == (1, 1, 1)
    transaction.rollback()
    assert capture_liveness(connection, metadata) == (0, 0, 0)
    assert all(connection.scalar(select(target.c.id)) == "kept" for target in targets)
    connection.exec_driver_sql("TRUNCATE " + ",".join(TARGETS))
    connection.commit()
    assert capture_liveness(connection, metadata) == (1, 1, 1)
    assert all(connection.scalar(select(target.c.id)) is None for target in targets)


def test_rr_deletion_commit_and_epoch_are_same_snapshot(owned):
    engine, connection, schema = owned
    metadata, targets = minimal(connection)
    connection.execute(targets[0].insert().values(id="kept"))
    connection.commit()
    connection.execution_options(isolation_level="REPEATABLE READ")
    assert capture_liveness(connection, metadata) == (0, 0, 0)
    with engine.begin() as writer:
        writer.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        writer.execute(delete(targets[0]))
    assert capture_liveness(connection, metadata) == (0, 0, 0)
    assert connection.scalar(select(targets[0].c.id)) == "kept"
    connection.commit()
    assert capture_liveness(connection, metadata) == (1, 0, 0)
    assert connection.scalar(select(targets[0].c.id)) is None


def test_publication_does_not_lock_epoch_and_search_path_cannot_shadow(owned):
    engine, connection, schema = owned
    metadata, targets = minimal(connection)
    connection.commit()
    connection.exec_driver_sql(f'SELECT * FROM "{schema}".{TABLE_NAME} FOR UPDATE')
    with engine.begin() as writer:
        writer.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        writer.exec_driver_sql("SET LOCAL lock_timeout='100ms'")
        writer.execute(targets[0].insert().values(id="published"))
        writer.execute(targets[0].update().values(value="updated"))
    connection.commit()
    connection.exec_driver_sql(f'CREATE TEMP TABLE {TABLE_NAME} (target_name text,epoch bigint)')
    connection.exec_driver_sql("SET LOCAL search_path TO pg_temp,public")
    assert capture_liveness(connection, metadata) == (0, 0, 0)
    connection.exec_driver_sql(f'DELETE FROM "{schema}".{TARGETS[0]}')
    assert capture_liveness(connection, metadata) == (1, 0, 0)


def test_sqlite_none_and_pg_missing_or_incomplete_refuses(owned):
    with create_engine("sqlite://").connect() as sqlite:
        assert capture_liveness(sqlite, MetaData()) is None
    _, connection, _ = owned
    with pytest.raises(ExportArtifactError):
        capture_liveness(connection, MetaData())
    metadata, _ = minimal(connection)
    with connection.begin_nested() as savepoint:
        connection.execute(delete(metadata.tables[TABLE_NAME]))
        with pytest.raises(ExportArtifactError):
            capture_liveness(connection, metadata)
        savepoint.rollback()
    assert capture_liveness(connection, metadata) == (0, 0, 0)


@pytest.mark.parametrize("tamper", [
    f'ALTER TABLE {TARGETS[0]} DISABLE TRIGGER {TRIGGER_NAME}',
    f'ALTER FUNCTION {FUNCTION_NAME}() SET search_path=public',
    f'GRANT EXECUTE ON FUNCTION {FUNCTION_NAME}() TO PUBLIC',
    f'ALTER TABLE {TABLE_NAME} ALTER COLUMN epoch DROP NOT NULL',
    f'ALTER TABLE {TABLE_NAME} DROP CONSTRAINT {TABLE_NAME}_pkey',
    f'ALTER TABLE {TABLE_NAME} DROP CONSTRAINT ck_weather_page_liveness_epoch',
    f'ALTER TABLE {TABLE_NAME} ADD FOREIGN KEY (target_name) REFERENCES {TABLE_NAME}(target_name)',
    f'ALTER TABLE {TABLE_NAME} ADD COLUMN unexpected text',
    f'ALTER TABLE {TABLE_NAME} ADD CHECK (epoch <= 1)',
    f'ALTER TABLE {TABLE_NAME} ADD UNIQUE (target_name)',
])
def test_existing_changed_objects_refused_without_repair(owned, tamper):
    _, connection, _ = owned
    metadata, _ = minimal(connection)
    with connection.begin_nested() as savepoint:
        connection.exec_driver_sql(tamper)
        with pytest.raises(RuntimeError):
            install_page_liveness(connection)
        savepoint.rollback()
    assert capture_liveness(connection, metadata) == (0, 0, 0)
    install_page_liveness(connection)


def test_not_null_logical_contract_across_pg_catalog_versions(owned):
    _, connection, schema = owned
    metadata, _ = minimal(connection)
    oid = connection.scalar(text("SELECT to_regclass(:name)::oid"),
                            {"name": f'"{schema}".{TABLE_NAME}'})
    not_null = connection.execute(text(
        "SELECT attname,attnotnull FROM pg_attribute WHERE attrelid=:oid "
        "AND attnum>0 AND NOT attisdropped ORDER BY attnum"
    ), {"oid": oid}).all()
    assert not_null == [("target_name", True), ("epoch", True)]
    count = connection.scalar(text(
        "SELECT count(*) FROM pg_constraint WHERE conrelid=:oid AND contype='n'"
    ), {"oid": oid})
    version = int(connection.scalar(text("SHOW server_version_num")))
    assert count == (2 if version >= 180000 else 0)
    install_page_liveness(connection)
    assert capture_liveness(connection, metadata) == (0, 0, 0)


def test_ownership_mismatch_is_refused_and_not_changed(owned):
    _, connection, _ = owned
    minimal(connection)
    role = "liveness_owner_" + uuid.uuid4().hex
    with connection.begin_nested() as savepoint:
        connection.exec_driver_sql(f'CREATE ROLE "{role}"')
        connection.exec_driver_sql(f'ALTER TABLE {TARGETS[0]} OWNER TO "{role}"')
        with pytest.raises(RuntimeError, match="소유자"):
            install_page_liveness(connection)
        savepoint.rollback()
    with connection.begin_nested() as savepoint:
        connection.exec_driver_sql(f'CREATE ROLE "{role}"')
        connection.exec_driver_sql(f'ALTER FUNCTION {FUNCTION_NAME}() OWNER TO "{role}"')
        with pytest.raises(RuntimeError, match="소유자"):
            install_page_liveness(connection)
        savepoint.rollback()
    install_page_liveness(connection)


def test_missing_epoch_refuses_delete_without_losing_source_row(owned):
    _, connection, _ = owned
    metadata, targets = minimal(connection)
    connection.execute(targets[0].insert().values(id="preserved"))
    connection.execute(delete(metadata.tables[TABLE_NAME]).where(
        metadata.tables[TABLE_NAME].c.target_name == TARGETS[0]))
    with pytest.raises(DBAPIError) as caught, connection.begin_nested():
        connection.execute(delete(targets[0]))
    assert caught.value.orig.sqlstate == "55000"
    assert connection.scalar(select(targets[0].c.id)) == "preserved"
    with pytest.raises(ExportArtifactError):
        capture_liveness(connection, metadata)


def test_quoted_actual_schema_and_single_epoch_query(owned):
    from sqlalchemy import event

    _, connection, _ = owned
    connection.exec_driver_sql('CREATE SCHEMA "page-liveness\'quoted"')
    connection.exec_driver_sql('SET LOCAL search_path TO "page-liveness\'quoted"')
    metadata, targets = minimal(connection)
    connection.execute(delete(targets[2]))
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(connection, "before_cursor_execute", record)
    try:
        assert capture_liveness(connection, metadata) == (0, 0, 1)
    finally:
        event.remove(connection, "before_cursor_execute", record)
    assert len(statements) == 1
    assert TABLE_NAME in statements[0] and "FROM pg_catalog.pg_trigger" in statements[0]
    assert "pg_catalog.to_regclass" in statements[0]
    assert "pg_catalog.to_regprocedure" in statements[0]
    metadata.drop_all(connection)
    connection.exec_driver_sql('DROP SCHEMA "page-liveness\'quoted"')


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize("change", ["drop_table", "recreate_table", "disable", "drop_trigger"])
def test_target_or_statement_trigger_loss_refuses_and_rollback_recovers(owned, index, change):
    _, connection, _ = owned
    metadata, targets = minimal(connection)
    target = targets[index]
    connection.execute(target.insert().values(id="preserved"))
    with connection.begin_nested() as savepoint:
        if change in {"drop_table", "recreate_table"}:
            target.drop(connection)
            if change == "recreate_table":
                target.create(connection)
        elif change == "disable":
            connection.exec_driver_sql(f"ALTER TABLE {target.name} DISABLE TRIGGER {TRIGGER_NAME}")
        else:
            connection.exec_driver_sql(f"DROP TRIGGER {TRIGGER_NAME} ON {target.name}")
        # DDL과 DISABLE은 삭제 trigger를 실행하지 않아 epoch는 그대로다.
        assert connection.scalar(select(metadata.tables[TABLE_NAME].c.epoch).where(
            metadata.tables[TABLE_NAME].c.target_name == target.name)) == 0
        with pytest.raises(ExportArtifactError):
            capture_liveness(connection, metadata)
        savepoint.rollback()
    assert capture_liveness(connection, metadata) == (0, 0, 0)
    assert connection.scalar(select(target.c.id)) == "preserved"


@pytest.mark.parametrize("change", ["wrong_type", "wrong_function"])
def test_replaced_statement_trigger_contract_refuses(owned, change):
    _, connection, _ = owned
    metadata, targets = minimal(connection)
    target = targets[0]
    with connection.begin_nested() as savepoint:
        connection.exec_driver_sql(f"DROP TRIGGER {TRIGGER_NAME} ON {target.name}")
        if change == "wrong_type":
            connection.exec_driver_sql(
                f"CREATE TRIGGER {TRIGGER_NAME} BEFORE DELETE ON {target.name} "
                f"FOR EACH STATEMENT EXECUTE FUNCTION {FUNCTION_NAME}()"
            )
        else:
            connection.exec_driver_sql(
                "CREATE FUNCTION unrelated_liveness() RETURNS trigger LANGUAGE plpgsql "
                "AS $$BEGIN RETURN NULL; END;$$"
            )
            connection.exec_driver_sql(
                f"CREATE TRIGGER {TRIGGER_NAME} AFTER DELETE OR TRUNCATE ON {target.name} "
                "FOR EACH STATEMENT EXECUTE FUNCTION unrelated_liveness()"
            )
        with pytest.raises(ExportArtifactError):
            capture_liveness(connection, metadata)
        savepoint.rollback()
    assert capture_liveness(connection, metadata) == (0, 0, 0)


def test_migration_upgrade_downgrade_preserves_original_rows_and_timeouts(owned):
    _, connection, _ = owned
    _, targets = minimal(connection, install=False)
    for target in targets:
        connection.execute(target.insert().values(id="original"))
    before = tuple(connection.execute(text("SELECT current_setting('lock_timeout'),"
                                           "current_setting('statement_timeout')")).one())
    path = Path(__file__).parents[1] / "alembic/versions/0039_page_liveness.py"
    spec = importlib.util.spec_from_file_location("page_liveness_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
        module.downgrade()
    after = tuple(connection.execute(text("SELECT current_setting('lock_timeout'),"
                                          "current_setting('statement_timeout')")).one())
    assert before == after
    assert all(connection.scalar(select(target.c.id)) == "original" for target in targets)
    assert connection.scalar(text("SELECT to_regclass(:name)"), {"name": TABLE_NAME}) is None


def test_install_lock_timeout_bounded_and_atomic(owned):
    engine, connection, schema = owned
    minimal(connection, install=False)
    connection.commit()
    with engine.connect() as blocker:
        blocker.exec_driver_sql(f'LOCK TABLE "{schema}".{TARGETS[0]} IN ACCESS EXCLUSIVE MODE')
        started = time.monotonic()
        with pytest.raises(DBAPIError) as caught:
            install_page_liveness(connection)
        assert caught.value.orig.sqlstate == "55P03"
        assert time.monotonic() - started < 3
        assert connection.scalar(text("SELECT to_regclass(:name)"), {"name": TABLE_NAME}) is None
        blocker.rollback()
    install_page_liveness(connection)
