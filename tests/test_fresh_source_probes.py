"""새 source의 불가능한 기존 fact 조회만 생략하는 PostgreSQL 회귀."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue, kst_now
from kortravelweather.repository import WeatherRepository


@pytest.fixture
def repository(weather_test_database_url):
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    repo.upsert_location(
        WeatherLocation(location_id="fresh", name="시험", latitude=37, longitude=127)
    )
    yield repo
    repo.engine.dispose()


def response(key, *, payload=None, fetched=None):
    return {
        "source_record_key": key,
        "provider": "test",
        "dataset_key": "fresh",
        "source_entity_type": "weather_response",
        "source_entity_id": "fresh",
        "payload": payload if payload is not None else {"revision": key},
        "fetched_at": fetched or kst_now(),
    }


def fact(key, *, metric="TEMP", number="1", target=None):
    return WeatherValue(
        location_id="fresh",
        provider="test",
        dataset_key="fresh",
        weather_domain="weather",
        forecast_style=ForecastStyle.SHORT,
        metric_key=metric,
        target_at=target or kst_now(),
        value_number=Decimal(number),
        source_record_key=key,
        payload={"metric": metric},
    )


@contextmanager
def fact_probes(repo):
    queries = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select ") and "from weather_values" in normalized:
            queries.append(statement)

    event.listen(repo.engine, "before_cursor_execute", capture)
    try:
        yield queries
    finally:
        event.remove(repo.engine, "before_cursor_execute", capture)


def counts(repo):
    with repo.engine.connect() as connection:
        return tuple(
            connection.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in (
                "weather_values",
                "weather_source_records",
                "weather_current_values",
            )
        )


def test_new_source_1001_facts_skip_queries_but_replay_is_still_checked(repository):
    target = kst_now()
    values = [fact("new", target=target + timedelta(minutes=i)) for i in range(1001)]
    record = response("new")
    with fact_probes(repository) as fresh:
        assert repository.ingest_batch(source_records=[record], values=values) == 1001
    assert fresh == []
    with fact_probes(repository) as replay:
        assert repository.ingest_batch(source_records=[record], values=values) == 0
    assert len(replay) == 2
    assert counts(repository) == (1001, 1, 1001)


def test_mixed_sources_only_probe_old_identity(repository):
    target = kst_now()
    old = fact("old", target=target)
    assert repository.ingest_batch(source_records=[response("old")], values=[old]) == 1
    new = fact("new", target=target)
    with fact_probes(repository) as probes:
        assert repository.ingest_batch(source_records=[response("new")], values=[old, new]) == 1
    assert len(probes) == 1
    assert counts(repository) == (2, 2, 1)


def test_new_aliases_in_same_batch_share_source_and_fact(repository):
    payload = {"revision": "shared"}
    target = kst_now()
    with fact_probes(repository) as probes:
        assert (
            repository.ingest_batch(
                source_records=[
                    response("first", payload=payload),
                    response("alias", payload=payload),
                ],
                values=[fact("first", target=target), fact("alias", target=target)],
            )
            == 1
        )
    assert probes == []
    assert counts(repository) == (1, 1, 1)


def test_stored_alias_replays_existing_canonical_fact(repository):
    payload = {"revision": "shared"}
    value = fact("first")
    assert (
        repository.ingest_batch(source_records=[response("first", payload=payload)], values=[value])
        == 1
    )
    with fact_probes(repository) as probes:
        assert (
            repository.ingest_batch(
                source_records=[response("alias", payload=payload)],
                values=[value.model_copy(update={"source_record_key": "alias"})],
            )
            == 0
        )
    assert len(probes) == 1
    assert counts(repository) == (1, 1, 1)


def test_new_fallback_source_and_duplicate_are_atomic(repository):
    value = fact(None)
    with fact_probes(repository) as probes:
        assert repository.upsert_values([value, value]) == 1
    assert probes == []
    with fact_probes(repository) as replay:
        assert repository.upsert_values([value]) == 0
    assert len(replay) == 1
    assert counts(repository) == (1, 1, 1)


def test_new_source_same_batch_typed_conflict_rolls_back(repository):
    value = fact("conflict")
    with pytest.raises(ValueError, match="immutable weather fact"):
        repository.ingest_batch(
            source_records=[response("conflict")],
            values=[value, value.model_copy(update={"value_number": Decimal("2")})],
        )
    assert counts(repository) == (0, 0, 0)


def test_new_source_digest_collision_cannot_overwrite_old_fact(repository, monkeypatch):
    old = fact("old")
    assert repository.ingest_batch(source_records=[response("old")], values=[old]) == 1
    collision = old.identity_key()
    monkeypatch.setattr(WeatherValue, "identity_key", lambda *args, **kwargs: collision)
    with pytest.raises(IntegrityError):
        repository.ingest_batch(
            source_records=[
                response("new", fetched=repository.get_source_record("old")["fetched_at"])
            ],
            values=[old.model_copy(update={"source_record_key": "new"})],
        )
    assert counts(repository) == (1, 1, 1)
