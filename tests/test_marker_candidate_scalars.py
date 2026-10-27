"""동일 후보의 불필요한 fact 재조회 제거와 기존 선택 의미를 검증한다."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import String, and_, bindparam, desc, func, literal, nullslast, select, true
from sqlalchemy.dialects.postgresql import ARRAY

from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherRepository, WeatherValueRow

FIELDS = ("location_id", "metric_key", "known_at", "source_record_key", "value_id")
METRICS = ("TEMP", "T1H", "TMP", "WEATHER_CODE", "SKY", "PTY")


def _capture(ids, now):
    statements = []

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

        scalars = execute

    repo = WeatherRepository.__new__(WeatherRepository)
    repo._session_factory = Session
    repo.marker_values_many(ids, now=now)
    return statements[:2]


@pytest.mark.parametrize("index", [0, 1], ids=["observed", "fallback"])
def test_marker_candidate_scalars_do_not_rejoin_the_same_fact(index):
    from sqlalchemy.dialects import postgresql

    statement = _capture(["candidate"], datetime.now(UTC))[index]
    assert tuple(statement.selected_columns.keys()) == FIELDS
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "marker_fact_keys" not in sql
    assert "payload" not in sql
    assert "JOIN LATERAL" in sql
    assert "known_at DESC NULLS LAST" in sql
    assert "source_record_key DESC NULLS LAST" in sql
    assert "value_id DESC" in sql


def _legacy_statement(ids, *, observed):
    """a9f54의 top4+복합 키 재JOIN을 독립적인 쿼리로 보존한다."""
    pairs = select(func.unnest(bindparam("legacy_locations", list(ids), type_=ARRAY(String)))
                   .label("location_id")).subquery("legacy_locations")
    metrics = select(func.unnest(bindparam("legacy_metrics", list(METRICS),
                                          type_=ARRAY(String))).label("metric_key"))\
        .subquery("legacy_metrics")
    literals = tuple(literal(key, literal_execute=True) for key in METRICS)
    candidate = select(WeatherValueRow.value_id, WeatherValueRow.known_at).where(
        WeatherValueRow.location_id == pairs.c.location_id,
        WeatherValueRow.metric_key == metrics.c.metric_key,
        WeatherValueRow.metric_key.in_(literals),
    )
    if observed:
        candidate = candidate.where(WeatherValueRow.forecast_style.in_(
            (literal("observed", literal_execute=True), literal("nowcast", literal_execute=True)),
        ))
    candidate = candidate.order_by(nullslast(desc(WeatherValueRow.known_at)),
                                   nullslast(desc(WeatherValueRow.source_record_key)),
                                   desc(WeatherValueRow.value_id)).limit(4).lateral("legacy_top4")
    keys = select(candidate.c.value_id, candidate.c.known_at).select_from(
        pairs.join(metrics, true()).join(candidate, true()),
    ).subquery("legacy_keys")
    return select(*(getattr(WeatherValueRow, name) for name in FIELDS)).join(keys, and_(
        WeatherValueRow.value_id == keys.c.value_id,
        WeatherValueRow.known_at == keys.c.known_at,
    ))


def test_marker_candidate_scalars_match_legacy_top4_and_partition_pairs(weather_test_database_url):
    from sqlalchemy import insert

    now = datetime.now(UTC).replace(microsecond=0)
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    ids = ["scalar-one", "scalar-fallback", "scalar-empty"]
    for key in [*ids, "scalar-shadow"]:
        repo.upsert_location(WeatherLocation(location_id=key, name=key, latitude=37, longitude=127))
    for key, provider, dataset in [
        ("Z", "fixture", "fixture"), ("a", "fixture", "fixture"),
        ("forecast", "forecast", "forecast"), ("fallback", "fixture", "forecast"),
        ("alert", "fixture", "alert"),
    ]:
        repo.record_source(source_record_key=key, provider=provider, dataset_key=dataset,
                           source_entity_type="response", source_entity_id=key, payload={},
                           fetched_at=now)
    values = []
    for index in range(7):
        values.append(WeatherValue(
            location_id="scalar-one", provider="fixture", dataset_key="fixture",
            weather_domain="weather", forecast_style="observed", metric_key="TEMP",
            target_at=now-timedelta(hours=index), known_at=now-timedelta(days=index % 2),
            value_number=index, source_record_key="Z" if index % 2 else "a",
            payload={"raw": {"same": index}},
        ))
    values.extend([
        WeatherValue(location_id="scalar-one", provider="forecast", dataset_key="forecast",
                     weather_domain="weather", forecast_style="short", metric_key="TEMP",
                     target_at=now+timedelta(days=1), known_at=now+timedelta(seconds=1),
                     value_number=99, source_record_key="forecast"),
        WeatherValue(location_id="scalar-fallback", provider="fixture", dataset_key="forecast",
                     weather_domain="weather", forecast_style="short", metric_key="SKY",
                     target_at=now+timedelta(hours=1), known_at=now, value_number=3,
                     source_record_key="fallback"),
        WeatherValue(location_id="scalar-one", provider="fixture", dataset_key="alert",
                     weather_domain="weather_alert", forecast_style="observed", metric_key="ALERT",
                     target_at=now, known_at=now, value_text="호우주의보 발표",
                     valid_until=now+timedelta(days=1), source_record_key="alert",
                     payload={"stn_id": "fixture", "seq": "1"}),
    ])
    repo.upsert_values(values)
    with repo._session_factory.begin() as session:
        original = session.scalars(select(WeatherValueRow).where(
            WeatherValueRow.location_id == "scalar-one", WeatherValueRow.metric_key == "TEMP",
            WeatherValueRow.forecast_style == "observed",
        )).first()
        shadow = {
            col.name: getattr(original, col.name) for col in WeatherValueRow.__table__.columns
        }
        shadow.update(location_id="scalar-shadow", known_at=now-timedelta(days=3))
        session.execute(insert(WeatherValueRow), shadow)
    observed, fallback = _capture(ids, now)
    with repo.engine.connect() as conn:
        observed_rows = conn.execute(observed).all()
        assert sorted(observed_rows) == sorted(conn.execute(_legacy_statement(ids, observed=True)))
        fallback_rows = conn.execute(fallback).all()
        assert sorted(fallback_rows) == sorted(conn.execute(_legacy_statement(ids, observed=False)))
    assert len([row for row in observed_rows if row.metric_key == "TEMP"]) == 4
    assert all(row.location_id != "scalar-shadow" for row in [*observed_rows, *fallback_rows])
    assert all(row.metric_key != "ALERT" for row in [*observed_rows, *fallback_rows])
    result = repo.marker_values_many(ids, now=now)
    assert result["scalar-empty"] == []
    assert [row.metric_key for row in result["scalar-fallback"]] == ["SKY"]
    assert result["scalar-fallback"][0].payload == values[-2].payload
    assert any(row.metric_key == "ALERT" for row in result["scalar-one"])
    assert next(row for row in result["scalar-one"] if row.metric_key == "TEMP").value_number != 99
    repo.engine.dispose()
