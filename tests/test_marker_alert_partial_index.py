"""명시한 6종류의 특보 고정 조건만 literal로 남기고 실제 generic 계획을 검산한다."""
import json
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from psycopg import sql
from sqlalchemy import event, text
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherRepository

NOW = datetime(2026, 10, 28, tzinfo=UTC)


def _alert_statement(location="literal-quote-'--", providers=None):
    statements = []

    class Session:
        def execute(self, statement):
            statements.append(statement)
            return SimpleNamespace(partitions=lambda _: iter(()))

    WeatherRepository.__new__(WeatherRepository)._current_value_models_many(
        Session(), [location], limit_per_location=26, prefer_current=False,
        kinds=["alert"], providers=providers, alert_active_at=NOW,
    )
    return statements[0]


@pytest.mark.parametrize("providers", [None, ["provider-quote-'--"], ["a", "b"]])
def test_explicit_kind_alert_fixed_predicate_survives_generic_bind(providers):
    compiled = _alert_statement(providers=providers).compile(
        dialect=PGDialect_psycopg(), compile_kwargs={"render_postcompile": True},
    )
    query = str(compiled).replace("%%", "%")
    assert "metric_key = 'ALERT'" in query
    for column in ("weather_domain", "dataset_key"):
        for pattern in ("alert", "warning"):
            assert f"{column} ILIKE '%{pattern}%'" in query
    assert "literal-quote-'--" not in query and "provider-quote-'--" not in query
    assert ["literal-quote-'--"] in compiled.params.values()
    assert NOW in compiled.params.values()
    assert NOW - timedelta(days=3) in compiled.params.values()
    assert all(value not in ("ALERT", "%alert%", "%warning%")
               for value in compiled.params.values() if isinstance(value, str))
    if providers:
        assert all(provider in compiled.params.values() for provider in providers)


def test_explicit_six_kind_alert_generic_uses_partial_index(
    weather_test_database_url, tmp_path,
):
    """일반 행이 많은 실제 PG에서 custom/generic 모두 특보 prefix와 복합 fact 키를 쓴다."""
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    ids = [f"literal-{i}" for i in range(60)]
    for key in ids:
        repo.upsert_location(WeatherLocation(location_id=key, name=key, latitude=37, longitude=127))
    for dataset in ("weather", "weather_alert"):
        repo.record_source(
            source_record_key=f"literal-{dataset}", provider="selected", dataset_key=dataset,
            source_entity_type="response", source_entity_id="literal", payload={}, fetched_at=NOW,
        )
    values = []
    for key in ids:
        # 특보가 드문 일반 이력에서 통계가 있는 planner의 인덱스 선택을 검산한다.
        for slot in range(80):
            values.append(WeatherValue(
                location_id=key, provider="selected", dataset_key="weather",
                weather_domain="weather",
                forecast_style="short", metric_key="TEMP", value_number=slot,
                target_at=NOW + timedelta(hours=slot), known_at=NOW,
                source_record_key="literal-weather", payload={"normal": "kept"},
            ))
        values.append(WeatherValue(
            location_id=key, provider="selected", dataset_key="weather_alert",
            weather_domain="weather_alert",
            forecast_style="observed", metric_key="ALERT", value_text="호우주의보 발표",
            target_at=NOW - timedelta(days=5), known_at=NOW - timedelta(days=1),
            valid_until=NOW + timedelta(hours=1), source_record_key="literal-weather_alert",
            payload={"stn_id": key, "action": "announce", "title": "호우주의보 발표"},
        ))
    repo.upsert_values(values)
    with repo.engine.begin() as connection:
        connection.execute(text("ANALYZE weather_current_values"))
        connection.execute(text("ANALYZE weather_values"))
    statements = []

    def record(_connection, _cursor, statement, parameters, _context, _executemany):
        if "EXISTS" in statement and "weather_current_values" in statement:
            statements.append((statement, parameters))

    event.listen(repo.engine, "before_cursor_execute", record)
    try:
        actual = repo.marker_values_many(
            ids, kinds=["alert", "observed", "nowcast", "ultra_short", "short", "mid"],
            providers=None, limit_per_location=80, now=NOW,
        )
    finally:
        event.remove(repo.engine, "before_cursor_execute", record)
    for key in ids:
        alerts = [row for row in actual[key] if row.metric_key == "ALERT"]
        assert len(alerts) == 1 and alerts[0].payload == {
            "stn_id": key, "action": "announce", "title": "호우주의보 발표",
        }
        assert alerts[0].valid_until == NOW + timedelta(hours=1)
        assert any(row.metric_key == "TEMP" for row in actual[key])
    statement, parameters = statements[-1]
    names = []

    def positional(match):
        name = match.group(1)
        if name not in names:
            names.append(name)
        return "$" + str(names.index(name) + 1)

    prepared = re.sub(r"%\(([A-Za-z0-9_]+)\)s", positional, statement)
    assert set(names) == set(parameters)

    def nodes(node):
        yield node
        for child in node.get("Plans", []):
            yield from nodes(child)

    try:
        with repo.engine.connect() as connection:
            cursor = connection.connection.driver_connection.cursor()
            try:
                # 실제 DBAPI와 같이 %% literal escape도 처리한 뒤 PREPARE한다.
                cursor.execute("PREPARE literal_alert AS " + prepared, ())
                execute = sql.SQL("EXECUTE literal_alert ({})").format(
                    sql.SQL(",").join(sql.Literal(parameters[name]) for name in names),
                )
                for mode in ("force_custom_plan", "force_generic_plan"):
                    cursor.execute("SET LOCAL plan_cache_mode=" + mode)
                    cursor.execute(sql.SQL("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) ") + execute)
                    plan = cursor.fetchone()[0]
                    (tmp_path / f"partial-alert-{mode}.json").write_text(
                        json.dumps(plan, default=str),
                    )
                    scans = list(nodes(plan[0]["Plan"]))
                    assert any(n.get("Index Name") == "ix_weather_current_values_alert_lookup"
                               for n in scans)
                    facts = [n for n in scans
                             if n.get("Relation Name", "").startswith("weather_values_")]
                    assert sum(n["Actual Loops"] for n in facts) == 60
                    assert all("value_id" in n.get("Index Cond", "")
                               and "known_at" in n.get("Index Cond", "")
                               for n in facts if n["Actual Loops"] > 0)
                    assert any(n["Actual Loops"] == 0 for n in facts)
                cursor.execute("DEALLOCATE literal_alert")
            finally:
                cursor.close()
    finally:
        repo.engine.dispose()
