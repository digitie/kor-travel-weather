"""적격 특보의 위치별 prefix와 기존 정렬·reducer 의미를 보존한다."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select, text

from kortravelweather.alerts import ALERT_MAX_AGE, active_alert_values
from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherCurrentValueRow, WeatherRepository, WeatherValueRow

NOW = datetime(2026, 10, 28, 1, tzinfo=UTC)


def _seed(url, *, recent=36, old=35):
    repo = WeatherRepository(url)
    repo.create_schema()
    ids = ["prefix-a", "prefix-b"]
    for key in ids:
        repo.upsert_location(WeatherLocation(location_id=key, name=key, latitude=37, longitude=127))
    for key in ("a", "Z"):
        repo.record_source(
            source_record_key=key,
            provider=key,
            dataset_key="weather_alert",
            source_entity_type="response",
            source_entity_id=key,
            payload={},
            fetched_at=NOW,
        )
    values = []
    for location in ids:
        for index in range(recent + old):
            fresh = index < recent
            # 같은 target·known의 다른 source/provider, 명시 만료/해제·옛 미래 유효를 포함한다.
            source = "a" if index % 2 else "Z"
            target = (
                NOW - timedelta(minutes=index // 2)
                if fresh
                else NOW - timedelta(days=5, minutes=index)
            )
            values.append(
                WeatherValue(
                    location_id=location,
                    provider=source,
                    dataset_key="weather_alert",
                    weather_domain="weather_alert",
                    forecast_style="observed" if index % 3 else "short",
                    metric_key="ALERT" if index % 4 else "NOTICE",
                    value_text="호우주의보 해제"
                    if index == 0
                    else "호우주의보 발표"
                    if index == 2
                    else f"특보{index} 발표",
                    target_at=target,
                    known_at=NOW - timedelta(days=index % 2),
                    valid_until=(
                        NOW + timedelta(days=1)
                        if not fresh and index % 3 == 0
                        else NOW - timedelta(hours=1)
                        if index % 3 == 1
                        else None
                    ),
                    source_record_key=source,
                    payload={
                        "stn_id": location,
                        "action": "release" if index == 0 else "active",
                        "title": "fallback 발표",
                        "raw": {"full": "must survive"},
                    },
                )
            )
    repo.upsert_values(values)
    # 구 writer가 남긴 nullable projection tie key를 fact source와 구분한다.
    with repo.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE weather_current_values SET source_record_key=NULL "
                "WHERE provider=:provider AND target_at=:now"
            ),
            {"provider": "a", "now": NOW},
        )
    return repo, values, ids


def _expected(repo, values, ids, *, limit, providers, newest, prefer):
    # source fetched_at 정규화가 적용된 실제 저장 fact를 reference 입력으로 쓴다.
    with repo._session_factory() as session:
        facts = {
            (r.value_id, r.known_at): repo._value_model(r)
            for r in session.scalars(select(WeatherValueRow)).all()
        }
    with repo.engine.connect() as connection:
        pointers = connection.execute(select(WeatherCurrentValueRow)).mappings().all()
    output = {}
    for location in dict.fromkeys(ids):
        choices = [
            p
            for p in pointers
            if p["location_id"] == location
            and (providers is None or p["provider"] in providers)
            and (
                p["target_at"] >= NOW - ALERT_MAX_AGE
                or (
                    facts[p["value_id"], p["known_at"]].valid_until is not None
                    and facts[p["value_id"], p["known_at"]].valid_until > NOW
                )
            )
        ]
        choices.sort(
            key=lambda p: (
                p["known_at"],
                p["source_record_key"] is not None,
                p["source_record_key"] or "",
                p["value_id"],
            ),
            reverse=True,
        )
        if newest:
            choices.sort(key=lambda p: p["target_at"], reverse=True)
        else:
            choices.sort(key=lambda p: (p["target_at"], p["metric_key"]))
        if prefer:
            choices.sort(key=lambda p: p["forecast_style"] not in ("observed", "nowcast"))
        output[location] = [facts[p["value_id"], p["known_at"]] for p in choices[:limit]]
    return output


@pytest.mark.parametrize("recent", [36, 5])
@pytest.mark.parametrize("newest,prefer", [(True, False), (False, False), (True, True)])
@pytest.mark.parametrize("providers,limit", [(None, 26), (None, 3), (["a"], 3), (["missing"], 1)])
def test_alert_prefix_preserves_complete_order_and_reducer(
    weather_test_database_url,
    recent,
    newest,
    prefer,
    providers,
    limit,
):
    repo, values, ids = _seed(weather_test_database_url, recent=recent)
    requested = [ids[1], ids[0], ids[1]]
    expected = _expected(
        repo, values, requested, limit=limit, providers=providers, newest=newest, prefer=prefer
    )
    try:
        with repo._session_factory() as session:
            actual = repo._current_value_models_many(
                session,
                requested,
                limit_per_location=limit,
                prefer_current=prefer,
                newest_first=newest,
                providers=providers,
                kinds=["alert"],
                alert_active_at=NOW,
            )

        def keys(rows):
            return [(r.identity_key(), r.known_at) for r in rows]

        assert list(actual) == list(expected)
        for key in actual:
            assert keys(actual[key]) == keys(expected[key])
            assert [r.payload for r in actual[key]] == [r.payload for r in expected[key]]
            assert keys(active_alert_values(actual[key], now=NOW)) == keys(
                active_alert_values(expected[key], now=NOW)
            )
    finally:
        repo.engine.dispose()


def test_recent_prefix_avoids_losing_old_fact_checks(weather_test_database_url, tmp_path):
    repo, values, ids = _seed(weather_test_database_url)
    # 분포 통계가 있는 own PG18에서 실제 실행 횟수를 확인한다.
    with repo.engine.begin() as connection:
        connection.exec_driver_sql("ANALYZE weather_current_values")
        connection.exec_driver_sql("ANALYZE weather_values")
    statements = []

    def record(_connection, _cursor, statement, parameters, _context, _many):
        if "EXISTS" in statement and "weather_current_values" in statement:
            statements.append((statement, parameters))

    event.listen(repo.engine, "before_cursor_execute", record)
    try:
        actual = repo.marker_values_many(
            ids[:1],
            limit_per_location=80,
            now=NOW,
            kinds=["observed", "nowcast", "ultra_short", "short", "mid", "alert"],
        )
    finally:
        event.remove(repo.engine, "before_cursor_execute", record)
    assert len(actual[ids[0]]) == 26 and len(statements) == 1
    statement, parameters = statements[0]
    with repo.engine.connect() as connection:
        plan = connection.exec_driver_sql(
            "EXPLAIN (ANALYZE,BUFFERS,TIMING FALSE,FORMAT JSON) " + statement, parameters
        ).scalar_one()
    (tmp_path / "alert-prefix-runtime-plan.json").write_text(json.dumps(plan, default=str))

    def nodes(node):
        yield node
        for child in node.get("Plans", []):
            yield from nodes(child)

    subplans = [
        n
        for n in nodes(plan[0]["Plan"])
        if n.get("Relation Name", "").startswith("weather_values_")
    ]
    assert subplans and sum(n.get("Actual Loops", 0) for n in subplans) == 0, plan
    assert "LATERAL" in statement and "LIMIT" in statement
    repo.engine.dispose()


def test_alert_prefix_does_not_replace_fact_disappearing_before_hydration(
    weather_test_database_url,
):
    """READ COMMITTED 다음 읽기에서 사라진 승자를 차순위로 채우지 않는다."""
    repo, values, ids = _seed(weather_test_database_url)
    expected = _expected(
        repo, values, ids[:1], limit=26, providers=None, newest=True, prefer=False
    )[ids[0]]
    gone = expected[0]
    fired = False

    def remove(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal fired
        if not fired and "current_fact_keys" in statement:
            fired = True
            with repo.engine.begin() as writer:
                writer.execute(text("SET LOCAL kortravelweather.purge = 'on'"))
                writer.execute(
                    text(
                        "DELETE FROM weather_current_values WHERE value_id=:id AND known_at=:known"
                    ),
                    {"id": gone.identity_key(), "known": gone.known_at},
                )
                writer.execute(
                    text("DELETE FROM weather_values WHERE value_id=:id AND known_at=:known"),
                    {"id": gone.identity_key(), "known": gone.known_at},
                )

    event.listen(repo.engine, "before_cursor_execute", remove)
    try:
        actual = repo.marker_values_many(
            ids[:1],
            limit_per_location=80,
            now=NOW,
            kinds=["observed", "nowcast", "ultra_short", "short", "mid", "alert"],
        )[ids[0]]
        assert fired
        assert [(r.identity_key(), r.known_at) for r in actual] == [
            (r.identity_key(), r.known_at) for r in expected[1:]
        ]
    finally:
        event.remove(repo.engine, "before_cursor_execute", remove)
        repo.engine.dispose()


def test_alert_prefix_string_ties_match_python_under_database_locale(weather_test_database_url):
    """DB 기본 locale 대신 Python 문자열 순서로 LIMIT 경계를 고른다."""
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    repo.upsert_location(
        WeatherLocation(location_id="unicode-prefix", name="문자 경계", latitude=37, longitude=127)
    )
    values = []
    for key in ("Z", "a", "ä", "가", "null-key"):
        repo.record_source(
            source_record_key=key,
            provider=key,
            dataset_key="weather_alert",
            source_entity_type="response",
            source_entity_id=key,
            payload={},
            fetched_at=NOW,
        )
        values.append(
            WeatherValue(
                location_id="unicode-prefix",
                provider=key,
                dataset_key="weather_alert",
                weather_domain="weather_alert",
                forecast_style="observed",
                metric_key="ALERT",
                value_text=key + " 특보 발표",
                target_at=NOW,
                known_at=NOW,
                source_record_key=key,
                payload={"stn_id": "same", "raw": {"kept": key}},
            )
        )
    repo.upsert_values(values)
    with repo.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE weather_current_values SET source_record_key=NULL WHERE provider=:provider"
            ),
            {"provider": "null-key"},
        )
    try:
        actual = repo.marker_values_many(
            ["unicode-prefix"], kinds=["alert"], limit_per_location=3, now=NOW
        )["unicode-prefix"]
        assert [row.source_record_key for row in actual] == ["가", "ä", "a"]
        assert [row.payload["raw"]["kept"] for row in actual] == ["가", "ä", "a"]
    finally:
        repo.engine.dispose()
