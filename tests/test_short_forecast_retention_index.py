"""소유 PostgreSQL의 TEMP 파티션으로 정리 계획·상속·조회 예산을 검증한다."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from kortravelweather.repository import WeatherRepository, WeatherValueRow
from kortravelweather.retention import SHORT_FORECAST_CANDIDATES_SQL, SHORT_FORECAST_PREDICATE


@pytest.mark.parametrize("original", ["0", "60s", "2s", "50ms"])
def test_partition_guard_restores_prior_timeout(weather_test_database_url, original):
    engine = create_engine(weather_test_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TEMP TABLE weather_values_20400101 (provider text,dataset_key text,"
                "weather_domain text,forecast_style text,valid_at timestamptz) ON COMMIT DROP"
            ))
            connection.execute(text(
                "SELECT set_config('statement_timeout', :original, true)"
            ), {"original": original})
            before = connection.scalar(text("SHOW statement_timeout"))
            WeatherRepository._preserve_short_forecast_partition(
                connection, "weather_values_20400101",
            )
            assert connection.scalar(text("SHOW statement_timeout")) == before
    finally:
        engine.dispose()


def _nodes(node):
    return [node, *(item for child in node.get("Plans", []) for item in _nodes(child))]


def test_retention_index_plan_migration_and_future_leaf(weather_test_database_url):
    path = Path("alembic/versions/0020_short_forecast_retention_index.py")
    spec = importlib.util.spec_from_file_location("retention_index_migration", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    index = next(index for index in WeatherValueRow.__table__.indexes
                 if index.name == revision.INDEX)
    assert str(index.dialect_options["postgresql"]["where"]) == SHORT_FORECAST_PREDICATE
    assert revision.PREDICATE == SHORT_FORECAST_PREDICATE
    compiled = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "(valid_at, known_at, value_id)" in compiled
    engine = create_engine(weather_test_database_url)
    try:
        with engine.begin() as connection:
            # 같은 이름의 public 원표를 가릴 뿐 변경하지 않는다.
            connection.execute(text(
                "CREATE TEMP TABLE weather_values (value_id text,known_at timestamptz,"
                "valid_at timestamptz,provider text,dataset_key text,weather_domain text,"
                "forecast_style text) PARTITION BY RANGE(known_at)"
            ))
            connection.execute(text(
                "CREATE TEMP TABLE retention_leaf PARTITION OF weather_values "
                "FOR VALUES FROM ('2040-01-01') TO ('2040-01-02')"
            ))
            connection.execute(text(
                "INSERT INTO weather_values SELECT n::text,'2040-01-01'::timestamptz,"
                "'2040-01-01'::timestamptz+n*interval '1 second',"
                "CASE WHEN n%10=0 THEN 'python-kma-api' ELSE 'other' END,"
                "'kma_short_forecast','kma_short_forecast','short' "
                "FROM generate_series(1,10000) n"
            ))
            connection.execute(text("ANALYZE weather_values"))
            query = text(SHORT_FORECAST_CANDIDATES_SQL)
            params = {"cutoff": "2040-01-03", "batch_size": 25}
            before = connection.execute(query, params).all()
            old = connection.scalar(text("EXPLAIN (FORMAT JSON) " + str(query)), params)
            assert any(n["Node Type"] == "Sort" for n in _nodes(old[0]["Plan"]))
            with Operations.context(MigrationContext.configure(connection)):
                revision.upgrade()
            after = connection.execute(query, params).all()
            assert after == before and len(after) == 25
            plan = connection.scalar(text("EXPLAIN (FORMAT JSON) " + str(query)), params)
            nodes = _nodes(plan[0]["Plan"])
            assert not any(n["Node Type"] == "Sort" for n in nodes)
            assert any(n["Node Type"] in {"Index Scan", "Index Only Scan"} for n in nodes)
            valid = connection.scalar(text(
                "SELECT indisvalid FROM pg_index WHERE indexrelid=CAST(:name AS regclass)"
            ), {"name": revision.INDEX})
            assert valid
            connection.execute(text(
                "CREATE TEMP TABLE retention_future_leaf PARTITION OF weather_values "
                "FOR VALUES FROM ('2040-01-02') TO ('2040-01-03')"
            ))
            assert connection.scalar(text(
                "SELECT count(*) FROM pg_inherits i JOIN pg_index x ON x.indexrelid=i.inhrelid "
                "WHERE i.inhparent=CAST(:name AS regclass) AND x.indisvalid"
            ), {"name": revision.INDEX}) == 2
            with Operations.context(MigrationContext.configure(connection)):
                revision.downgrade()
            assert not connection.scalar(text(
                "SELECT EXISTS (SELECT 1 FROM pg_class WHERE relnamespace=pg_my_temp_schema() "
                "AND relname=:name)"
            ), {"name": revision.INDEX})
            assert connection.execute(query, params).all() == before
            with Operations.context(MigrationContext.configure(connection)):
                revision.upgrade()
            assert connection.execute(query, params).all() == before
            connection.execute(text("DROP TABLE weather_values CASCADE"))
    finally:
        engine.dispose()
