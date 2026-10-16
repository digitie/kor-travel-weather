"""재수집 복합 키의 정확성·bounded query·partition pruning 회귀."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import insert, select
from sqlalchemy.dialects import postgresql

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue, kst_now
from kortravelweather.repository import WeatherRepository, WeatherValueRow, _ResolvedFact

KST = timezone(timedelta(hours=9))


def resolved(value_id, clock):
    value = WeatherValue(
        location_id="pair",
        provider="test",
        dataset_key="replay",
        weather_domain="weather",
        forecast_style=ForecastStyle.SHORT,
        metric_key="TEMP",
        target_at=clock or kst_now(),
        value_number=Decimal("1"),
        source_record_key="source",
        payload={"metric": "TEMP"},
    )
    return _ResolvedFact(
        value=value, source_key="source", value_id=value_id,
        target_at=value.target_at, known_at=clock, collected_at=clock, replayed=True,
    )


class CaptureSession:
    def __init__(self):
        self.statements = []

    def scalars(self, statement):
        self.statements.append(statement)
        return self

    def all(self):
        return []


def compiled(session):
    return [s.compile(dialect=postgresql.dialect()) for s in session.statements]


def test_same_clock_probe_shape_uses_one_clock_even_with_equivalent_timezone():
    clock = datetime(2026, 10, 16, tzinfo=UTC)
    session = CaptureSession()
    WeatherRepository._existing_facts_session(
        session, [resolved("a", clock), resolved("b", clock.astimezone(KST))]
    )
    query, = compiled(session)
    sql = str(query)
    assert "weather_values.known_at = " in sql
    assert "weather_values.value_id IN " in sql
    assert "(weather_values.value_id, weather_values.known_at) IN " not in sql
    assert "JOIN (VALUES" not in sql


def test_mixed_clock_probe_shape_joins_exact_pairs_and_bounds_partitions():
    before = datetime(2026, 10, 15, 15, tzinfo=UTC)
    after = before + timedelta(days=1)
    session = CaptureSession()
    WeatherRepository._existing_facts_session(
        session, [resolved("same-id", before), resolved("same-id", after.astimezone(KST))]
    )
    query, = compiled(session)
    sql = str(query)
    assert "JOIN (VALUES" in sql
    assert "weather_values.value_id = requested_fact_keys.value_id" in sql
    assert "weather_values.known_at = requested_fact_keys.known_at" in sql
    assert "weather_values.known_at >= " in sql and "weather_values.known_at <= " in sql
    assert query.params["known_at_1"] == before
    assert query.params["known_at_2"] == after


def test_probe_queries_are_bounded_after_duplicate_and_fresh_filtering():
    clock = datetime(2026, 10, 16, tzinfo=UTC)
    seed = resolved("seed", clock)
    facts = [
        replace(seed, value_id=f"value-{i}", known_at=clock + timedelta(seconds=i % 3))
        for i in range(1001)
    ]
    session = CaptureSession()
    WeatherRepository._existing_facts_session(
        session, facts + facts[:50] + [replace(seed, value_id="fresh", replayed=False)]
    )
    queries = compiled(session)
    assert len(queries) == 2
    assert all(len(q.params) <= 2002 for q in queries)


def test_nonnullable_clock_keys_cannot_match_or_trigger_a_probe():
    session = CaptureSession()
    assert WeatherRepository._existing_facts_session(session, [resolved("a", None)]) == {}
    assert session.statements == []


@pytest.fixture
def repository(weather_test_database_url):
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    repo.upsert_location(
        WeatherLocation(location_id="pair", name="시험", latitude=37, longitude=127)
    )
    yield repo
    repo.engine.dispose()


def stored_fact(repository, key, clock):
    value = resolved("unused", clock).value.model_copy(update={"source_record_key": key})
    repository.ingest_batch(
        source_records=[{
            "source_record_key": key, "provider": "test", "dataset_key": "replay",
            "source_entity_type": "weather_response", "source_entity_id": "pair",
            "payload": {"response": key}, "fetched_at": clock,
        }],
        values=[value],
    )
    return resolved(value.identity_key(key, target_at=clock), clock)


def test_postgresql_same_id_different_clocks_does_not_overfetch(repository):
    # KST 자정 경계 양쪽을 쓰고, 한 id의 과거 clock을 synthetic 보존 행으로 둔다.
    before = kst_now().astimezone(UTC).replace(
        hour=15, minute=0, second=0, microsecond=0
    ) - timedelta(days=1)
    after = before + timedelta(days=1)
    fact = stored_fact(repository, "canonical", before)
    with repository._session_factory.begin() as session:
        row = session.scalars(select(WeatherValueRow)).one()
        shadow = {c.name: getattr(row, c.name) for c in WeatherValueRow.__table__.columns}
        shadow["known_at"] = after
        session.execute(insert(WeatherValueRow), shadow)
    with repository._session_factory() as session:
        found = repository._existing_facts_session(
            session, [fact, replace(fact, value_id="missing", known_at=after)]
        )
        assert set(found) == {(fact.value_id, before)}
        assert found[fact.value_id, before].payload == fact.value.payload
        both = repository._existing_facts_session(
            session, [fact, replace(fact, known_at=after.astimezone(KST))]
        )
        assert set(both) == {(fact.value_id, before), (fact.value_id, after)}


def test_postgresql_none_clock_does_not_overfetch_in_single_clock_path(repository):
    clock = kst_now().astimezone(UTC)
    first = stored_fact(repository, "first", clock)
    second = stored_fact(repository, "second", clock)
    with repository._session_factory() as session:
        found = repository._existing_facts_session(
            session, [first, replace(second, known_at=None)]
        )
    assert set(found) == {(first.value_id, clock)}
