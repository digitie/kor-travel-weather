"""COPY도 동일한 fact·트랜잭션·SQLAlchemy 오류/계측 경계를 유지한다."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from sqlalchemy import event, func, insert, select
from sqlalchemy.exc import IntegrityError, OperationalError, StatementError
from sqlalchemy.orm import Session

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherRepository, WeatherValueRow


@pytest.fixture
def repository(weather_test_database_url):
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    repo.upsert_location(WeatherLocation(
        location_id="copy-test", name="COPY 시험", latitude=37.5, longitude=127, nx=60, ny=127,
    ))
    repo.record_source(source_record_key="copy-source", provider="kma", dataset_key="short",
                       source_entity_type="grid", source_entity_id="60:127", payload={"raw": True})
    yield repo
    repo.engine.dispose()


def rows(count=2):
    now = datetime.now(UTC).replace(microsecond=123456)
    return [dict(
        value_id=f"{index:064x}", location_id="copy-test", provider="kma", dataset_key="short",
        weather_domain="weather", forecast_style="short", timeline_bucket="short", metric_key="TMP",
        metric_name="기온", source_metric_key="TMP", source_metric_name=None,
        value_number=Decimal("12.5000") if index % 2 else None,
        value_text=None if index % 2 else "한글\t줄바꿈\n\\", unit="C", severity=None,
        issued_at=now, valid_at=now + timedelta(hours=index + 1), valid_from=None, valid_until=None,
        observed_at=None, target_at=now + timedelta(hours=index + 1), known_at=now,
        normalization_version="v1", payload={"raw": [0, True, None, "한글"], "ordinal": index},
        collected_at=now, source_record_key="copy-source",
    ) for index in range(count)]


@pytest.fixture
def copy_calls(monkeypatch):
    calls = []
    original = psycopg.Cursor.copy

    def copy(cursor, statement, *args, **kwargs):
        calls.append(str(statement))
        return original(cursor, statement, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "copy", copy)
    return calls


@pytest.mark.parametrize("count,expected_copies", [(0, 0), (1, 0), (2, 1), (1001, 1)])
def test_bounded_copy_roundtrip_and_caller_rollback(repository, copy_calls, count, expected_copies):
    values = rows(count)
    with Session(repository.engine) as session:
        repository._insert_pending_facts_session(session, values)
        got = session.execute(select(WeatherValueRow.__table__).order_by(
            WeatherValueRow.value_id,
        )).mappings().all()
        assert [dict(row) for row in got] == values
        session.rollback()
        assert session.scalar(select(func.count()).select_from(WeatherValueRow)) == 0
    assert len(copy_calls) == expected_copies


@pytest.mark.parametrize("change,exception,sqlstate", [
    ("location", IntegrityError, "23503"), ("source", IntegrityError, "23503"),
    ("constraint", IntegrityError, "23514"), ("duplicate", IntegrityError, "23505"),
    ("naive", StatementError, None),
])
def test_failed_copy_keeps_error_type_and_rolls_back_whole_batch(
    repository, change, exception, sqlstate,
):
    values = rows()
    if change == "location":
        values[1]["location_id"] = "missing"
    elif change == "source":
        values[1]["source_record_key"] = "missing"
    elif change == "constraint":
        values[1].update(value_number=None, value_text=None)
    elif change == "duplicate":
        values[1] = dict(values[0])
    else:
        values[1]["known_at"] = values[1]["known_at"].replace(tzinfo=None)
    with Session(repository.engine) as session:
        with pytest.raises(exception) as error:
            repository._insert_pending_facts_session(session, values)
        assert getattr(error.value.orig, "sqlstate", None) == sqlstate
        session.rollback()
        assert session.scalar(select(func.count()).select_from(WeatherValueRow)) == 0


def test_copy_uses_normal_sqlalchemy_error_and_statement_hooks(repository):
    errors, timings = [], []
    event.listen(repository.engine, "handle_error", lambda context: errors.append(context))
    event.listen(repository.engine, "after_cursor_execute",
                 lambda conn, cursor, statement, params, context, many:
                 timings.append(cursor.rowcount) if context.isinsert else None)
    with Session(repository.engine) as session:
        repository._insert_pending_facts_session(session, rows())
        assert 2 in timings
        session.rollback()
        values = rows()
        values[1]["location_id"] = "missing"
        with pytest.raises(IntegrityError):
            repository._insert_pending_facts_session(session, values)
        session.rollback()
    assert isinstance(errors[-1].sqlalchemy_exception, IntegrityError)
    assert errors[-1].original_exception.sqlstate == "23503"
    assert errors[-1].is_disconnect is False


def test_copy_disconnect_invalidates_pool_and_next_transaction_recovers(repository):
    closed = False

    def close_before_copy(conn, cursor, statement, params, context, many):
        nonlocal closed
        if not closed and context.execution_options.get("weather_fact_copy"):
            closed = True
            cursor.connection.close()

    event.listen(repository.engine, "before_cursor_execute", close_before_copy)
    with Session(repository.engine) as session:
        with pytest.raises(OperationalError) as error:
            repository._insert_pending_facts_session(session, rows())
        assert error.value.connection_invalidated is True
        session.rollback()
        repository._insert_pending_facts_session(session, rows())
        session.commit()
    assert closed
    with Session(repository.engine) as session:
        assert session.scalar(select(func.count()).select_from(WeatherValueRow)) == 2


def test_unflagged_and_returning_inserts_use_existing_dialect(repository, copy_calls):
    for returning in (False, True):
        with Session(repository.engine) as session:
            statement = insert(WeatherValueRow.__table__)
            if returning:
                statement = statement.execution_options(weather_fact_copy=True).returning(
                    WeatherValueRow.value_id,
                )
            result = session.execute(statement, rows())
            if returning:
                assert len(result.scalars().all()) == 2
            session.rollback()
    assert not copy_calls


def test_public_ingest_keeps_source_lineage_idempotency_and_current_projection(
    repository, copy_calls,
):
    target = datetime.now(UTC)
    values = [WeatherValue(
        location_id="copy-test", provider="kma", dataset_key="short", weather_domain="weather",
        forecast_style=ForecastStyle.SHORT, metric_key=metric, target_at=target,
        value_number=Decimal(number), source_record_key="copy-source", payload={"metric": metric},
    ) for metric, number in [("TMP", "12.5"), ("REH", "60")]]
    assert repository.ingest_batch(source_records=[], values=values) == 2
    assert len(copy_calls) == 1
    assert repository.ingest_batch(source_records=[], values=values) == 0
    assert len(copy_calls) == 1
    got = repository.latest_values("copy-test")
    assert {(value.metric_key, value.value_number) for value in got} == {
        ("TMP", Decimal("12.5")), ("REH", Decimal("60")),
    }
    assert all(value.source_record_key == "copy-source" for value in got)


def test_copy_lock_timeout_uses_existing_fresh_transaction_retry(
    repository, copy_calls, monkeypatch,
):
    held = repository.engine.connect()
    held.begin()
    held.exec_driver_sql("SELECT location_id FROM weather_locations "
                         "WHERE location_id='copy-test' FOR UPDATE")
    attempts = []

    def step():
        attempts.append(1)
        if len(attempts) == 2:
            held.rollback()
        with Session(repository.engine) as session:
            session.connection().exec_driver_sql("SET LOCAL lock_timeout='75ms'")
            repository._insert_pending_facts_session(session, rows())
            session.commit()
        return "committed"

    monkeypatch.setattr("kortravelweather.repository.time.sleep", lambda seconds: None)
    try:
        assert repository._retry_lock_race(step) == "committed"
        assert len(attempts) == 2
        assert len(copy_calls) == 2
        with Session(repository.engine) as session:
            assert session.scalar(select(func.count()).select_from(WeatherValueRow)) == 2
    finally:
        held.rollback()
        held.close()


@pytest.mark.parametrize("schema", [None, ""])
def test_explicit_default_schema_translation_uses_existing_dialect(
    repository, copy_calls, schema,
):
    with repository.engine.connect().execution_options(
        schema_translate_map={None: schema},
    ) as connection, Session(connection) as session:
        repository._insert_pending_facts_session(session, rows())
        assert session.scalar(select(func.count()).select_from(WeatherValueRow)) == 2
        session.rollback()
    assert not copy_calls
