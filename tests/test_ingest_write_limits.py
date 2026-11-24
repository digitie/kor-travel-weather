"""수집 writer transaction의 문장·유휴 상한(2026-11-24 Map 호출 검토 L5).

수집 transaction에는 lock 대기 상한만 있었다. 이제 원천·fact를 게시하는 transaction은 문장 하나
300초, 문장 사이 유휴 60초 상한을 이 transaction에만 건다. export 읽기처럼
``_set_ingest_lock_timeout``만 쓰는 transaction에는 걸지 않는다.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

from kortravelweather import repository as module
from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherRepository

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)

_LIMITS = (
    "SELECT current_setting('lock_timeout'), current_setting('statement_timeout'), "
    "current_setting('idle_in_transaction_session_timeout')"
)


@pytest.fixture
def repository():
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    # 같은 연결의 session 기본값을 보도록 pool을 한 연결로 고정한다.
    original = repo.engine
    repo.engine = create_engine(original.url, pool_size=1, max_overflow=0)
    repo._session_factory.configure(bind=repo.engine)
    original.dispose()
    yield repo
    repo.engine.dispose()


def _batch(suffix: str) -> tuple[list[dict], list[WeatherValue]]:
    location = "write-limits"
    key = f"write-limits-{suffix}"
    base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    record = {
        "source_record_key": key, "provider": "p", "dataset_key": "d",
        "source_entity_type": "weather_response", "source_entity_id": location,
        "payload": {"rows": [{"h": 1}], "blob": "x" * 2048},
    }
    value = WeatherValue(
        location_id=location, provider="p", dataset_key="d", weather_domain="d",
        forecast_style=ForecastStyle.SHORT, metric_key="TMP",
        target_at=base + timedelta(hours=1), issued_at=base, value_number=Decimal(1),
        source_record_key=key,
    )
    return [record], [value]


def _with_location(repository: WeatherRepository) -> None:
    repository.upsert_location(WeatherLocation(
        location_id="write-limits", name="상한", latitude=37.5, longitude=127, nx=60, ny=127,
    ))


def _observe_inside(repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch) -> list:
    seen: list[tuple[str, str, str]] = []
    original = repository._ingest_batch_session

    def observed(session, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        seen.append(tuple(session.execute(text(_LIMITS)).one()))
        return original(session, *args, **kwargs)

    monkeypatch.setattr(repository, "_ingest_batch_session", observed)
    return seen


def test_ingest_transactions_carry_statement_and_idle_limits(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_location(repository)
    seen = _observe_inside(repository, monkeypatch)
    records, values = _batch(uuid.uuid4().hex[:8])

    assert repository.ingest_batch(source_records=records, values=values) == 1
    run = repository.start_sync_run(provider="p", dataset_key="d")
    repository.ingest_skip_locked(source_records=[], values=values)
    repository.publish_and_finish(
        run_id=run.run_id, source_records=[], values=[], grids_fetched=0,
    )

    # ingest_batch·ingest_skip_locked·publish_and_finish 모두 같은 상한이다.
    assert len(seen) == 3
    for lock, statement, idle in seen:
        assert lock != "0" and statement == "5min" and idle == "1min"
    # SET LOCAL과 같은 범위: 끝나면 연결의 기본값으로 돌아간다.
    with repository.engine.connect() as connection:
        assert connection.execute(text(_LIMITS)).one()[1:] == ("0", "0")


def test_a_stricter_connection_setting_is_kept(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_location(repository)
    seen = _observe_inside(repository, monkeypatch)
    with repository.engine.begin() as connection:
        connection.execute(text("SET statement_timeout = '45s'"))
        connection.execute(text("SET idle_in_transaction_session_timeout = '20s'"))
    try:
        records, values = _batch(uuid.uuid4().hex[:8])
        repository.ingest_batch(source_records=records, values=values)
    finally:
        with repository.engine.begin() as connection:
            connection.execute(text("RESET statement_timeout"))
            connection.execute(text("RESET idle_in_transaction_session_timeout"))
    assert seen[0][1:] == ("45s", "20s")


def test_export_read_transactions_do_not_get_the_writer_limits(
    repository: WeatherRepository,
) -> None:
    """``_set_ingest_lock_timeout``만 쓰는 transaction(export heads·lifecycle)은 그대로다."""
    with repository._session_factory.begin() as session:
        repository._set_ingest_lock_timeout(session)
        lock, statement, idle = session.execute(text(_LIMITS)).one()
    assert lock != "0" and (statement, idle) == ("0", "0")


def test_a_writer_idle_inside_its_transaction_is_cut_off(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """lock을 쥔 채 멈춘 writer는 서버가 끊고, 그 chunk는 commit되지 않는다."""
    _with_location(repository)
    monkeypatch.setattr(module, "INGEST_IDLE_IN_TRANSACTION_TIMEOUT_MS", 200)
    original = repository._ingest_batch_session

    def stalls(session, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        loaded = original(session, *args, **kwargs)
        time.sleep(0.6)  # commit 전에 transaction 안에서 멈춘다.
        return loaded

    monkeypatch.setattr(repository, "_ingest_batch_session", stalls)
    records, values = _batch(uuid.uuid4().hex[:8])
    with pytest.raises(DBAPIError) as raised:
        repository.ingest_batch(source_records=records, values=values)
    # 25P03(idle_in_transaction_session_timeout): lock 경합이 아니므로 재시도하지 않는다.
    assert raised.value.orig.sqlstate == "25P03"
    monkeypatch.setattr(repository, "_ingest_batch_session", original)
    with repository.engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM weather_source_records WHERE source_record_key = :k"),
            {"k": records[0]["source_record_key"]},
        ).scalar_one() == 0


def test_a_statement_past_its_limit_fails_the_chunk_without_retrying(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_location(repository)
    monkeypatch.setattr(module, "INGEST_STATEMENT_TIMEOUT_MS", 200)
    calls: list[int] = []

    def slow(session, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        calls.append(1)
        session.execute(text("SELECT pg_sleep(0.6)"))
        return 0

    monkeypatch.setattr(repository, "_ingest_batch_session", slow)
    records, values = _batch(uuid.uuid4().hex[:8])
    with pytest.raises(OperationalError) as raised:
        repository.ingest_batch(source_records=records, values=values)
    assert raised.value.orig.sqlstate == "57014"
    # lock 경합만 재시도한다. 느린 문장은 다시 해도 느리다.
    assert calls == [1]

