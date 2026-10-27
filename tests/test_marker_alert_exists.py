"""특보의 기존 후보 의미와 반복 fact 검산 제거를 실제 PostgreSQL로 검증한다."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import event, text

from kortravelweather.alerts import ALERT_MAX_AGE, active_alert_values
from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherRepository


def test_six_kind_alert_candidates_keep_order_without_batch_rechecks(
    weather_test_database_url, tmp_path,
):
    """60지점·80특보·6종류·80예산과 제공처 필터에서 같은 후보를 반환한다."""
    import json

    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    now = datetime(2026, 10, 28, tzinfo=UTC)
    ids = [f"exists-{i}" for i in range(60)]
    for key in ids:
        repo.upsert_location(WeatherLocation(location_id=key, name=key,
                                            latitude=37, longitude=127))
    repo.upsert_location(WeatherLocation(location_id="unrequested", name="범위 밖",
                                        latitude=37, longitude=127))
    for provider in ("other", "selected"):
        for suffix in ("a", "z"):
            key = f"alert-{provider}-{suffix}"
            repo.record_source(source_record_key=key, provider=provider,
                               dataset_key="weather_alert", source_entity_type="response",
                               source_entity_id=key, payload={},
                               fetched_at=now-timedelta(days=suffix == "a"))
    repo.record_source(source_record_key="ordinary", provider="selected", dataset_key="weather",
                       source_entity_type="response", source_entity_id="ordinary",
                       payload={}, fetched_at=now)
    values = []
    for location in ids:
        for slot in range(80):
            recent = slot < 40
            provider = "other" if recent else "selected"
            values.append(WeatherValue(
                location_id=location, provider=provider,
                dataset_key="weather_alert", weather_domain="weather_alert",
                forecast_style="observed", metric_key="ALERT",
                value_text=("호우주의보 해제" if slot == 0 else
                            "호우주의보 발표" if slot == 2 else f"특보{slot} 발표"),
                target_at=now-timedelta(hours=slot//2 if recent else 100+slot),
                known_at=now-timedelta(days=slot % 2),
                valid_until=(now+timedelta(days=1) if slot % 3 == 0
                             else now-timedelta(hours=1) if slot % 3 == 1 else None),
                source_record_key=f"alert-{provider}-{'a' if slot % 2 else 'z'}",
                payload={"stn_id": location},
            ))
        # 제공처가 다른 같은 target/known_at은 source key/value_id로 순위가 결정된다.
        values.append(WeatherValue(
            location_id=location, provider="selected", dataset_key="weather_alert",
            weather_domain="weather_alert", forecast_style="observed", metric_key="ALERT",
            value_text="동률 특보 발표", target_at=now, known_at=now,
            valid_until=now+timedelta(days=1), source_record_key="alert-selected-z",
        ))
        for style in ("observed", "short"):
            values.append(WeatherValue(
                location_id=location, provider="selected", dataset_key="weather",
                weather_domain="weather", forecast_style=style, metric_key="TEMP",
                value_number=12, target_at=now, known_at=now, source_record_key="ordinary",
            ))
    repo.upsert_values(values + [WeatherValue(
        location_id="unrequested", provider="selected", dataset_key="weather_alert",
        weather_domain="weather_alert", forecast_style="observed", metric_key="ALERT",
        value_text="요청 범위 밖", target_at=now-timedelta(days=20, minutes=slot),
        known_at=now, valid_until=now+timedelta(days=1),
        source_record_key="alert-selected-z",
    ) for slot in range(80)])
    with repo.engine.connect() as connection:
        projected = set(connection.scalars(text("SELECT value_id FROM weather_current_values")))
    # 이전 512행 루프와 같은 판정·정렬을 순수 reference로 남긴다. 최근 명시 만료도
    # 먼저 26개 후보에 포함되고 최종 reducer가 제거하는 기존 순서를 유지한다.
    def expected(location, providers):
        rows = [v for v in values if v.identity_key() in projected
                and v.location_id == location and v.metric_key == "ALERT"
                and (providers is None or v.provider in providers)
                and (v.target_at >= now-ALERT_MAX_AGE
                     or (v.valid_until is not None and v.valid_until > now))]
        return sorted(rows, key=lambda v: (
            v.target_at, v.known_at, v.source_record_key is not None,
            v.source_record_key or "", v.identity_key(),
        ), reverse=True)[:26]

    statements = []
    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((statement, parameters))

    statistics = []
    event.listen(repo.engine, "before_cursor_execute", record)
    try:
        for providers in (None, ["selected"], ["other"], ["missing"]):
            statements.clear()
            actual = repo.marker_values_many(
                ids, kinds=["alert", "observed", "nowcast", "ultra_short", "short", "mid"],
                providers=providers, limit_per_location=80, now=now,
            )
            counts = len(statements)
            rechecks = sum(isinstance(p, dict) and "alert_value_ids" in p
                           for _, p in statements)
            for key, rows in actual.items():
                alerts = [r for r in rows if r.metric_key == "ALERT"]
                reference = expected(key, providers)
                assert [(r.identity_key(), r.known_at) for r in alerts] == [
                    (r.identity_key(), r.known_at) for r in reference]
                assert [r.identity_key() for r in active_alert_values(alerts, now=now)] == [
                    r.identity_key() for r in active_alert_values(reference, now=now)]
                assert all(r.value_text != "호우주의보 해제" for r in
                           active_alert_values(alerts, now=now))
            statistics.append({"providers": providers, "queries": counts,
                               "old_batch_rechecks": rechecks})
            print("특보 후보 검산:", statistics[-1])
            assert rechecks == 0
            assert counts == (3 if providers == ["missing"] else
                              4 if providers == ["other"] else 6)
            # 소유한 합성 DB의 실행 계획만 저장한다. 운영 조회·통계 갱신은 없다.
            pointer_sql, params = next((s, p) for s, p in reversed(statements)
                                      if "weather_current_values" in s)
            with repo.engine.connect() as connection:
                plan = connection.exec_driver_sql(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + pointer_sql, params,
                ).scalar_one()
            (tmp_path/f"plan-{providers}.json").write_text(json.dumps(plan, default=str))
            def fact_scans(node):
                if node.get("Relation Name", "").startswith("weather_values_"):
                    yield node
                for child in node.get("Plans", []):
                    yield from fact_scans(child)

            scans = list(fact_scans(plan[0]["Plan"]))
            if providers is None or providers == ["selected"]:
                # 2,400개 오래된 pointer마다 정확한 날짜 파티션 하나만 조회한다.
                assert sum(s["Actual Loops"] for s in scans) == 60 * 40
                assert all("value_id" in s["Index Cond"] and "known_at" in s["Index Cond"]
                           for s in scans)
                assert any(s["Actual Loops"] == 0 for s in scans)
            else:
                assert sum(s["Actual Loops"] for s in scans) == 0
    finally:
        event.remove(repo.engine, "before_cursor_execute", record)
        repo.engine.dispose()


def test_alert_candidate_cutoff_does_not_filter_recent_expired_releases(
    weather_test_database_url,
):
    """경계 시각·만료/NULL·해제의 의미는 원래 후보 단계와 같다."""
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    now = datetime(2026, 10, 28, tzinfo=UTC)
    repo.upsert_location(WeatherLocation(location_id="boundary-alert", name="경계",
                                        latitude=37, longitude=127))
    repo.record_source(source_record_key="boundary-alert", provider="selected",
                       dataset_key="weather_alert", source_entity_type="response",
                       source_entity_id="boundary-alert", payload={}, fetched_at=now)
    cases = [
        ("호우주의보 해제", now, now-timedelta(hours=1)),
        ("호우주의보 발표", now-timedelta(hours=1), now+timedelta(days=1)),
        ("강풍주의보 발표", now-ALERT_MAX_AGE, None),
        ("대설주의보 발표", now-ALERT_MAX_AGE-timedelta(microseconds=1), None),
        ("한파주의보 발표", now-timedelta(days=8), now+timedelta(microseconds=1)),
        ("폭염주의보 발표", now-timedelta(days=9), now),
    ]
    repo.upsert_values([WeatherValue(
        location_id="boundary-alert", provider="selected", dataset_key="weather_alert",
        weather_domain="weather_alert", forecast_style="observed", metric_key="ALERT",
        value_text=title, target_at=target, valid_until=end, known_at=now,
        source_record_key="boundary-alert",
    ) for title, target, end in cases])
    try:
        rows = repo.marker_values_many(["boundary-alert"], kinds=["alert"], now=now)[
            "boundary-alert"]
        assert [v.value_text for v in rows] == [
            "호우주의보 해제", "호우주의보 발표", "강풍주의보 발표", "한파주의보 발표"]
        assert {v.value_text for v in active_alert_values(rows, now=now)} == {
            "강풍주의보 발표", "한파주의보 발표"}
        limited = repo.marker_values_many(["boundary-alert"], kinds=["alert"],
                                          limit_per_location=1, now=now)["boundary-alert"]
        assert [v.value_text for v in limited] == ["호우주의보 해제"]
        assert active_alert_values(limited, now=now) == []
    finally:
        repo.engine.dispose()


def test_alert_validity_uses_exact_partition_key(weather_test_database_url):
    """다른 known_at의 미래 유효기간이 현재 pointer를 살리면 안 된다."""
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    now = datetime(2026, 10, 28, tzinfo=UTC)
    repo.upsert_location(WeatherLocation(location_id="partition-alert", name="특보",
                                        latitude=37, longitude=127))
    repo.record_source(source_record_key="partition-alert", provider="selected",
                       dataset_key="weather_alert", source_entity_type="response",
                       source_entity_id="partition-alert", payload={}, fetched_at=now)
    value = WeatherValue(
        location_id="partition-alert", provider="selected", dataset_key="weather_alert",
        weather_domain="weather_alert", forecast_style="observed", metric_key="ALERT",
        value_text="장기 특보", target_at=now-timedelta(days=8), known_at=now,
        valid_until=now-timedelta(hours=1), source_record_key="partition-alert",
    )
    repo.upsert_values([value])
    with repo.engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO weather_values
            SELECT (jsonb_populate_record(NULL::weather_values,
                to_jsonb(v)||jsonb_build_object('known_at',CAST(:older AS timestamptz),
                                              'valid_until',CAST(:future AS timestamptz)))).*
            FROM weather_values v WHERE value_id=:id AND known_at=:now
        """), {"older": now-timedelta(days=10), "future": now+timedelta(days=1),
                "id": value.identity_key(), "now": now})
    try:
        result = repo.marker_values_many(["partition-alert"], kinds=["alert"], now=now)
        assert result == {"partition-alert": []}
    finally:
        repo.engine.dispose()
