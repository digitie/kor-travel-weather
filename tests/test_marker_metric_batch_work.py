"""종류별 지도 조회가 대표값 대신 전체 metric 이력을 읽지 않는지 검증한다."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherRepository


def test_marker_metric_batch_reads_only_indexed_representatives(weather_test_database_url):
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    now = datetime.now(UTC)
    ids = [f"metric-work-{i}" for i in range(6)]
    values = []
    for location in ids:
        repo.upsert_location(WeatherLocation(location_id=location, name=location,
                                            latitude=37, longitude=127))
        repo.record_source(source_record_key=location, provider="fixture", dataset_key="weather",
                           source_entity_type="response", source_entity_id=location, payload={},
                           fetched_at=now)
        for minute in range(24):
            for metric in ["TEMP", "T1H", "SKY"]:
                values.append(WeatherValue(
                    location_id=location, provider="fixture", dataset_key="weather",
                    weather_domain="weather", forecast_style="observed", metric_key=metric,
                    value_number=minute, target_at=now-timedelta(minutes=minute), known_at=now,
                    source_record_key=location,
                ))
    repo.upsert_values(values)
    statements = []

    class Capture:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, statement):
            statements.append(statement)
            return SimpleNamespace(all=lambda: [])

    fake = WeatherRepository.__new__(WeatherRepository)
    fake._session_factory = Capture
    fake.marker_values_many(ids, kinds=["observed"], now=now)
    assert len(statements) == 1
    sql = str(statements[0].compile(dialect=postgresql.dialect(),
                                    compile_kwargs={"literal_binds": True}))
    with repo.engine.connect() as connection:
        plan = connection.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) " + sql)).scalar_one()

    def scans(value):
        if isinstance(value, dict):
            if value.get("Relation Name") == "weather_current_values":
                yield value
            for child in value.values():
                yield from scans(child)
        elif isinstance(value, list):
            for child in value:
                yield from scans(child)

    observed = list(scans(plan))
    examined = sum((item["Actual Rows"] + item.get("Rows Removed by Filter", 0))
                   * item["Actual Loops"] for item in observed)
    assert observed and examined <= len(ids) * 6, (
        f"종류 마커가 대표 대신 이력을 읽었습니다: examined={examined}, locations={len(ids)}"
    )
    assert all(item.get("Index Name") == "ix_weather_current_values_marker_current"
               for item in observed)
    result = repo.marker_values_many(ids, kinds=["observed"], now=now)
    assert set(result) == set(ids)
    assert all([r.metric_key for r in rows] == ["TEMP", "T1H", "SKY"]
               and all(r.value_number == 0 for r in rows) for rows in result.values())
    repo.engine.dispose()


def test_sixty_location_kind_batches_preserve_provider_and_alert_results(
    weather_test_database_url,
):
    """60지점·선택 제공처 없음/부재/오래된 값·장기 특보를 실제 PG로 조회한다."""
    import re
    from time import perf_counter

    from psycopg import sql as pgsql
    from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    now = datetime(2026, 10, 27, 0, tzinfo=UTC)
    ids = [f"batch-work-{i}" for i in range(60)]
    for location in ids:
        repo.upsert_location(WeatherLocation(location_id=location, name=location,
                                            latitude=37, longitude=127))
    repo.record_source(source_record_key="batch-work", provider="fixture", dataset_key="weather",
                       source_entity_type="response", source_entity_id="fixture", payload={},
                       fetched_at=now)
    with repo.engine.begin() as connection:
        # 대량 fixture 적재의 FK 내부 계획에 생기는 JIT 비용은 조회 측정과 분리한다.
        connection.exec_driver_sql("SET LOCAL jit=off")
        connection.exec_driver_sql("SET LOCAL statement_timeout=30000")
        connection.execute(text("""
            INSERT INTO weather_values
            (value_id,location_id,provider,dataset_key,weather_domain,forecast_style,metric_key,
             value_number,target_at,known_at,collected_at,normalization_version,source_record_key)
            SELECT 'batch-'||l||'-'||style||'-'||metric||'-'||slot,
                   'batch-work-'||l,
                   CASE WHEN slot=79 THEN 'selected' ELSE 'other' END,
                   'weather','weather',style,metric,slot,
                   :now + (CASE WHEN style='observed' THEN -slot ELSE slot END)*interval '1 hour',
                   :now,:now,'fixture','batch-work'
            FROM generate_series(0,59) l CROSS JOIN generate_series(0,79) slot
            CROSS JOIN unnest(ARRAY['observed','short']) style
            CROSS JOIN unnest(ARRAY['TEMP','T1H','TMP','WEATHER_CODE','SKY','PTY']) metric
        """), {"now": now})
        # FK를 유지한 같은 57,600행을 적재하되 한 statement를 한 지점(960행)으로 제한한다.
        # CI의 10지점(9,600행) 적재가 FK 검사 중 30초를 넘었으며 조회 예산은 그대로 둔다.
        # known_at 조건은 source fact의 파티션을 한정하며 이후 조회 측정에는 적용하지 않는다.
        inserted = 0
        for location in ids:
            result = connection.execute(text("""
                INSERT INTO weather_current_values
                (value_id,location_id,provider,dataset_key,weather_domain,forecast_style,
                 metric_key,target_at,known_at,source_record_key)
                SELECT value_id,location_id,provider,dataset_key,weather_domain,forecast_style,
                       metric_key,target_at,known_at,source_record_key FROM weather_values
                WHERE known_at=:now AND location_id=ANY(:locations)
            """), {"now": now, "locations": [location]})
            inserted += result.rowcount
        assert inserted == 60 * 80 * 2 * 6
        connection.exec_driver_sql("ANALYZE weather_current_values")
        connection.exec_driver_sql("ANALYZE weather_values")
    statistics = []
    for kind in ("observed", "short"):
        for provider in (None, ["missing"], ["selected"], ["selected", "missing"]):
            statements = []

            class Capture:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

                def execute(self, statement, statements=statements):
                    statements.append(statement)
                    return SimpleNamespace(all=lambda: [])

            fake = WeatherRepository.__new__(WeatherRepository)
            fake._session_factory = Capture
            fake.marker_values_many(ids, kinds=[kind], providers=provider, now=now)
            compiled = statements[0].compile(dialect=PGDialect_psycopg(),
                                             compile_kwargs={"render_postcompile": True})
            names, kinds = [], {}

            def positional(match, names=names, kinds=kinds):
                name, datatype = match.groups()
                if name not in kinds:
                    names.append(name)
                    kinds[name] = datatype
                assert kinds[name] == datatype
                return "$" + str(names.index(name)+1) + "::" + datatype

            prepared = re.sub(
                r"%\(([A-Za-z0-9_]+)\)s::"
                r"(TIMESTAMP WITH TIME ZONE|VARCHAR\[\]|VARCHAR|INTEGER|TEXT)",
                positional, str(compiled),
            )
            assert set(names) == set(compiled.params) and "%(" not in prepared
            with repo.engine.connect() as connection:
                cursor = connection.connection.driver_connection.cursor()
                cursor.execute("SET LOCAL statement_timeout='5s'")
                cursor.execute("PREPARE batch_kind (" + ",".join(kinds[n] for n in names)
                               + ") AS " + prepared)
                execute = pgsql.SQL("EXECUTE batch_kind ({})").format(pgsql.SQL(",").join(
                    pgsql.Literal(compiled.params[n]) for n in names
                ))
                results = []
                for mode in ("force_custom_plan", "force_generic_plan"):
                    cursor.execute("SET LOCAL plan_cache_mode=" + mode)
                    cursor.execute(execute)
                    results.append(cursor.fetchall())
                    cursor.execute(pgsql.SQL("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) ") + execute)
                    plan = cursor.fetchone()[0][0]

                    def projection_scans(node):
                        if node.get("Relation Name") == "weather_current_values":
                            yield node
                        for child in node.get("Plans", []):
                            yield from projection_scans(child)

                    scans = list(projection_scans(plan["Plan"]))
                    examined = sum((node["Actual Rows"] + node.get("Rows Removed by Filter", 0))
                                   * node["Actual Loops"] for node in scans)
                    assert scans and examined <= 360, (kind, provider, mode, examined)
                    statistics.append({"kind": kind, "provider": provider, "mode": mode,
                                       "milliseconds": plan["Execution Time"],
                                       "hit": plan["Plan"].get("Shared Hit Blocks", 0),
                                       "read": plan["Plan"].get("Shared Read Blocks", 0),
                                       "temp": plan["Plan"].get("Temp Written Blocks", 0)})
                assert results[0] == results[1]
                assert len(results[0]) == (0 if provider == ["missing"] else 360)
                cursor.execute("DEALLOCATE batch_kind")
            started = perf_counter()
            rows = repo.marker_values_many(ids, kinds=[kind], providers=provider, now=now)
            assert perf_counter()-started < 5
            for values in rows.values():
                if provider == ["missing"]:
                    assert not values
                else:
                    assert len(values) == 6
                    assert all(value.value_number == (79 if provider else 0) for value in values)
                    assert all(value.provider == ("selected" if provider else "other")
                               for value in values)
    # 3일보다 오래되었어도 명시된 종료시각이 지나지 않은 특보는 유지해야 한다.
    repo.record_source(source_record_key="batch-alert", provider="selected",
                       dataset_key="weather_alert", source_entity_type="response",
                       source_entity_id="fixture", payload={}, fetched_at=now)
    alerts = [WeatherValue(
        location_id=location, provider="selected", dataset_key="weather_alert",
        weather_domain="weather_alert", forecast_style="observed", metric_key="ALERT",
        value_text="장기 특보", target_at=now-timedelta(days=8), known_at=now,
        valid_until=now+timedelta(days=1), source_record_key="batch-alert",
    ) for location in ids]
    repo.upsert_values(alerts)
    with repo.engine.begin() as connection:
        # 대량 fixture 적재의 FK 내부 계획에 생기는 JIT 비용은 조회 측정과 분리한다.
        connection.exec_driver_sql("SET LOCAL jit=off")
        connection.exec_driver_sql("SET LOCAL statement_timeout=30000")
        connection.execute(text("""
            INSERT INTO weather_values
            (value_id,location_id,provider,dataset_key,weather_domain,forecast_style,metric_key,
             value_text,target_at,known_at,collected_at,valid_until,
             normalization_version,source_record_key)
            SELECT 'expired-'||l||'-'||slot, 'batch-work-'||l, 'selected',
                   'weather_alert','weather_alert','observed','ALERT','종료된 특보',
                   :now - (4*24+slot)*interval '1 hour',:now,:now,:now-interval '1 hour',
                   'fixture','batch-alert'
            FROM generate_series(0,59) l CROSS JOIN generate_series(0,52) slot
        """), {"now": now})
        connection.execute(text("""
            INSERT INTO weather_current_values
            (value_id,location_id,provider,dataset_key,weather_domain,forecast_style,
             metric_key,target_at,known_at,source_record_key)
            SELECT value_id,location_id,provider,dataset_key,weather_domain,forecast_style,
                   metric_key,target_at,known_at,source_record_key FROM weather_values
            WHERE value_id LIKE 'expired-%'
        """))
        connection.exec_driver_sql("ANALYZE weather_current_values")
    started = perf_counter()
    rows = repo.marker_values_many(ids, kinds=["alert"], providers=["selected"], now=now)
    alert_seconds = perf_counter()-started
    assert alert_seconds < 5
    print("60지점 장기 특보 조회 초:", alert_seconds)
    assert all(len(values) == 1 and values[0].value_text == "장기 특보"
               for values in rows.values())
    print("60지점 계획 통계:", statistics)
    repo.engine.dispose()


def test_marker_maximum_location_alert_keys_are_chunked_before_global_limit(
    weather_test_database_url,
):
    """API 최대 500지점에서도 키는 512행 배열로 제한하고 전체 최종 winner를 유지한다."""
    from sqlalchemy import event

    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    now = datetime.now(UTC)
    ids = [f"maximum-alert-{i}" for i in range(500)]
    for location in ids:
        repo.upsert_location(WeatherLocation(location_id=location, name=location,
                                            latitude=37, longitude=127))
    repo.record_source(source_record_key="maximum-alert", provider="fixture",
                       dataset_key="weather_alert", source_entity_type="response",
                       source_entity_id="fixture", payload={}, fetched_at=now)
    repo.upsert_values([
        WeatherValue(location_id=location, provider="fixture", dataset_key="weather_alert",
                     weather_domain="weather_alert", forecast_style="observed", metric_key="ALERT",
                     value_text=label, target_at=now-timedelta(days=days), known_at=now,
                     valid_until=now+timedelta(days=1), source_record_key="maximum-alert")
        for location in ids for days, label in [(9, "먼 특보"), (8, "가까운 특보")]
    ])
    batches = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if isinstance(parameters, dict) and "alert_value_ids" in parameters:
            batches.append((len(parameters["alert_value_ids"]), len(parameters)))

    event.listen(repo.engine, "before_cursor_execute", record)
    try:
        result = repo.marker_values_many(ids, kinds=["alert"], limit_per_location=1, now=now)
        assert len(result) == 500
        assert all(len(rows) == 1 and rows[0].value_text == "가까운 특보"
                   for rows in result.values())
        assert len(batches) == 2 and sum(size for size, _ in batches) == 1000
        assert all(size <= 512 and binds < 10 for size, binds in batches)
    finally:
        event.remove(repo.engine, "before_cursor_execute", record)
        repo.engine.dispose()
