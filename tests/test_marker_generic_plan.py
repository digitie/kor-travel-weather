"""요청 입력은 bind하고 부분 인덱스의 고정 조건만 SQL에 남긴다."""

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from psycopg import sql
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherRepository

NOW = datetime(2026, 10, 16, 3, 50, 34, tzinfo=UTC)
METRIC_SQL = "metric_key IN ('TEMP', 'T1H', 'TMP', 'WEATHER_CODE', 'SKY', 'PTY')"


def _statements(location_id="marker-fixture"):
    statements = []

    class Result:
        def all(self):
            return []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, stmt):
            statements.append(stmt)
            return SimpleNamespace(all=lambda: [])

        def scalars(self, stmt):
            statements.append(stmt)
            return Result()

    repo = WeatherRepository.__new__(WeatherRepository)
    repo._session_factory = lambda: Session()
    repo.marker_values_many([location_id], now=NOW)
    return statements


def _compile(stmt):
    return stmt.compile(
        dialect=PGDialect_psycopg(), compile_kwargs={"render_postcompile": True}
    )


@pytest.mark.parametrize("index", [0, 1], ids=["observed", "fallback"])
def test_marker_metric_partial_index_guard_survives_generic_bind(index):
    compiled = _compile(_statements()[index])
    assert METRIC_SQL in str(compiled)
    assert "__[POSTCOMPILE" not in str(compiled)


def test_marker_observed_partial_index_guard_survives_generic_bind():
    compiled = _compile(_statements()[0])
    assert "forecast_style IN ('observed', 'nowcast')" in str(compiled)


def test_marker_alert_partial_index_guard_survives_generic_bind():
    compiled = _compile(_statements()[2])
    assert "weather_domain = 'weather_alert'" in str(compiled)
    assert "metric_key = 'ALERT'" in str(compiled)


@pytest.mark.parametrize("index", [0, 1, 2], ids=["observed", "fallback", "alerts"])
def test_marker_request_location_remains_bound(index):
    location = "request-quote-'-- %(__literal__)s"
    compiled = _compile(_statements(location)[index])
    assert location not in str(compiled)
    assert any(
        value == location or isinstance(value, list) and location in value
        for value in compiled.params.values()
    )
    # 후보/경보의 한도도 내부 predicate 고정과 별개의 기존 bind 계약이다.
    assert any(isinstance(value, int) for value in compiled.params.values())


def test_marker_alert_time_and_json_fields_remain_bound():
    compiled = _compile(_statements()[2])
    assert NOW in compiled.params.values()
    assert "'stn_id'" not in str(compiled)
    assert "stn_id" in compiled.params.values()


def test_marker_generic_prepared_queries_preserve_exact_rows(weather_test_database_url):
    """실제 psycopg PREPARE의 generic/custom 결과와 요청 bind를 비교한다."""
    repo = WeatherRepository(weather_test_database_url)
    try:
        repo.create_schema()
        repo.upsert_location(
            WeatherLocation(
                location_id="marker-fixture", name="fixture", latitude=37, longitude=127
            )
        )
        repo.record_source(
            source_record_key="marker-generic-source",
            provider="fixture",
            dataset_key="marker-generic",
            source_entity_type="weather_response",
            source_entity_id="marker-fixture",
            payload={},
            fetched_at=NOW,
        )
        repo.upsert_values([
            WeatherValue(
                location_id="marker-fixture",
                provider="fixture",
                dataset_key="marker-generic",
                metric_key="ALERT" if alert else "TEMP",
                weather_domain="weather_alert" if alert else "weather",
                forecast_style="observed",
                known_at=NOW,
                target_at=NOW,
                value_number=None if alert else 20,
                value_text="호우주의보 발표" if alert else None,
                valid_until=NOW + timedelta(hours=1) if alert else None,
                payload={"stn_id": "fixture", "seq": "1"} if alert else {},
                source_record_key="marker-generic-source",
            )
            for alert in (False, True)
        ])
        with repo.engine.connect() as connection:
            cursor = connection.connection.driver_connection.cursor()
            cursor.execute("SET LOCAL statement_timeout = '5s'")
            for index, stmt in enumerate(_statements()):
                compiled = _compile(stmt)
                names, kinds = [], {}
                token = re.compile(
                    r"%\(([A-Za-z0-9_]+)\)s::"
                    r"(TIMESTAMP WITH TIME ZONE|VARCHAR\[\]|VARCHAR|INTEGER|TEXT)"
                )

                def positional(match, names=names, kinds=kinds):
                    name, kind = match.groups()
                    if name not in kinds:
                        names.append(name)
                        kinds[name] = kind
                    assert kinds[name] == kind
                    return "$" + str(names.index(name) + 1) + "::" + kind

                prepared = token.sub(positional, str(compiled))
                assert set(names) == set(compiled.params) and "%(" not in prepared
                name = "marker_generic_test_" + str(index)
                cursor.execute(
                    "PREPARE " + name + " (" + ", ".join(kinds[key] for key in names)
                    + ") AS " + prepared
                )
                execute = sql.SQL("EXECUTE " + name + " ({})").format(
                    sql.SQL(", ").join(sql.Literal(compiled.params[key]) for key in names)
                )
                cursor.execute("SET LOCAL plan_cache_mode = force_custom_plan")
                cursor.execute(execute)
                custom = cursor.fetchall()
                cursor.execute("SET LOCAL plan_cache_mode = force_generic_plan")
                cursor.execute(execute)
                generic = cursor.fetchall()
                assert generic == custom and generic
                cursor.execute(
                    "SELECT generic_plans, custom_plans FROM pg_prepared_statements WHERE name=%s",
                    (name,),
                )
                generic_count, custom_count = cursor.fetchone()
                assert generic_count == custom_count == 1
    finally:
        repo.engine.dispose()
