"""실제 alert reader의 세 SQL을 1배/10배 PostgreSQL fixture에서 검증한다."""

import importlib.util
from contextlib import suppress
from datetime import timedelta
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, text

from kortravelweather.models import kst_now
from kortravelweather.repository import SyncRunRow, WeatherRepository

INDEX = "ix_weather_sync_runs_success_finished"


def _nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from _nodes(child)


def _actual_queries(repository):
    statements = []

    class Captured(Exception):
        pass

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((statement, parameters))
            if len(statements) == 3:
                raise Captured()

    event.listen(repository.engine, "before_cursor_execute", capture)
    try:
        with suppress(Captured):
            repository.read_alert_events(bjd_code="11", since=kst_now() - timedelta(hours=1))
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture)
    assert len(statements) == 3
    return statements


def test_alert_actual_queries_keep_index_plans_at_tenfold_volume(weather_test_database_url):
    repository = WeatherRepository(weather_test_database_url)
    repository.create_schema()
    next(index for index in SyncRunRow.__table__.indexes if index.name == INDEX).create(
        repository.engine, checkfirst=True,
    )
    try:
        queries = _actual_queries(repository)
        for rows in (20000, 200000):
            with repository.engine.begin() as connection:
                connection.execute(text("DELETE FROM weather_sync_runs"))
                connection.execute(text("""
                    INSERT INTO weather_sync_runs
                        (run_id,provider,dataset_key,status,started_at,finished_at)
                    SELECT 'alert-plan-' || n,
                           CASE WHEN n % 10=0 THEN 'python-kma-api' ELSE 'other' END,
                           CASE WHEN n % 10=0 THEN 'kma_regional_alert_events' ELSE 'other' END,
                           CASE WHEN n % 30=0 THEN 'failed' ELSE 'success' END,
                           now()-interval '1 second' * n,
                           now()-interval '1 second' * n + interval '1 second'
                    FROM generate_series(1,:rows) n
                """), {"rows": rows})
                connection.exec_driver_sql("ANALYZE weather_sync_runs")
            with repository.engine.connect() as connection:
                for ordinal, (statement, parameters) in enumerate(queries):
                    plan = connection.exec_driver_sql(
                        "EXPLAIN (FORMAT JSON) " + statement, parameters,
                    ).scalar_one()
                    nodes = list(_nodes(plan[0]["Plan"]))
                    assert not any(n["Node Type"] == "Seq Scan" for n in nodes), plan
                    if ordinal == 1:
                        assert not any(n["Node Type"] == "Sort" for n in nodes), plan
                        assert any(n.get("Index Name") == INDEX for n in nodes), plan
    finally:
        repository.engine.dispose()


def test_alert_index_migration_roundtrip_preserves_rows(weather_test_database_url):
    repository = WeatherRepository(weather_test_database_url)
    repository.create_schema()
    next(index for index in SyncRunRow.__table__.indexes if index.name == INDEX).create(
        repository.engine, checkfirst=True,
    )
    path = Path("alembic/versions/0043_alert_success_index.py")
    spec = importlib.util.spec_from_file_location("alert_index_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert len(module.revision) <= 32
    assert module.down_revision == "0042_export_database_artifacts"
    try:
        with repository.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO weather_sync_runs(run_id,provider,dataset_key,status,started_at)
                VALUES ('alert-migration','python-kma-api','kma_regional_alert_events',
                        'success',now())
            """))
        with repository.engine.connect() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()  # create_all로 이미 만든 동일 정의도 검산한다.
                module.downgrade()
                assert connection.scalar(text("SELECT count(*) FROM weather_sync_runs")) == 1
                connection.commit()
                module.upgrade()
            assert connection.execute(text("""
                SELECT indisvalid,indisready FROM pg_index
                WHERE indexrelid='ix_weather_sync_runs_success_finished'::regclass
            """)).one() == (True, True)
            assert connection.scalar(text("SELECT count(*) FROM weather_sync_runs")) == 1
    finally:
        repository.engine.dispose()
