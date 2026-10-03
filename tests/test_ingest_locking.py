"""An ingest transaction is short, set-based and gives up on a stuck lock.

On 2026-10-02 a ``kma_short_forecast_job`` run sat in one transaction for
4h03m, updating ``weather_current_values`` one ORM row at a time while it held
1,103 location advisory locks; three other KMA jobs waited 2h+ behind it.
These tests pin the three things that went wrong at the repository layer:
per-fact round trips, row-by-row projection updates, and unbounded lock waits.
"""

from __future__ import annotations

import os
import threading
import time
from collections import Counter
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError

from kortravelweather import repository as repository_module
from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue, kst_now
from kortravelweather.repository import WeatherRepository

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)


def _repository(*location_ids: str) -> WeatherRepository:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    for location_id in location_ids:
        repo.upsert_location(
            WeatherLocation(
                location_id=location_id,
                name=location_id,
                latitude=37,
                longitude=127,
                nx=1,
                ny=1,
            )
        )
    return repo


def _record(repo: WeatherRepository, key: str, *, hours_ago: float = 0) -> None:
    repo.record_source(
        source_record_key=key,
        provider="p",
        dataset_key="d",
        # Not a KMA entity type: lineage checks are not under test here.
        source_entity_type="test_response",
        source_entity_id="loc",
        payload={"rows": [key]},
        fetched_at=kst_now() - timedelta(hours=hours_ago),
    )


def _fact(source_key: str, *, hour: int, location_id: str = "loc", number: int = 1) -> WeatherValue:
    target = kst_now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=hour)
    return WeatherValue(
        location_id=location_id,
        provider="p",
        dataset_key="d",
        weather_domain="forecast",
        forecast_style=ForecastStyle.SHORT,
        metric_key="TMP",
        target_at=target,
        valid_at=target,
        value_number=Decimal(number),
        payload={"hour": hour, "source": source_key},
        source_record_key=source_key,
    )


def _pointer(repo: WeatherRepository, *, hour: int = 0) -> str:
    target = kst_now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=hour)
    with repo.engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT cv.source_record_key FROM weather_current_values cv "
                "WHERE cv.location_id = 'loc' AND cv.target_at = :target"
            ),
            {"target": target},
        ).scalar_one()


def test_ingest_statement_count_does_not_grow_with_the_facts() -> None:
    # The old ingest took the location lock (advisory + SELECT FOR UPDATE),
    # the source lock and a primary-key lookup once per fact, then re-read
    # every candidate and updated each pointer with its own UPDATE: thousands
    # of statements for 300 facts.  Now it is a constant handful per batch.
    repo = _repository("loc")
    _record(repo, "sr-count")
    facts = [_fact("sr-count", hour=hour) for hour in range(300)]
    statements: list[str] = []

    def count(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    event.listen(repo.engine, "before_cursor_execute", count)
    try:
        assert repo.ingest_batch(values=facts) == 300
    finally:
        event.remove(repo.engine, "before_cursor_execute", count)
    assert len(statements) < 40, Counter(s[:90] for s in statements).most_common(5)
    projection_writes = [s for s in statements if "weather_current_values" in s]
    assert len(projection_writes) == 1
    assert projection_writes[0].lstrip().startswith("INSERT INTO weather_current_values")


def test_projection_upsert_keeps_the_newest_revision_whatever_the_arrival_order() -> None:
    repo = _repository("loc")
    _record(repo, "sr-old", hours_ago=2)
    _record(repo, "sr-new", hours_ago=1)
    repo.ingest_batch(values=[_fact("sr-new", hour=0, number=2)])
    assert _pointer(repo) == "sr-new"
    # An older revision arriving late must not replace the newer pointer.
    repo.ingest_batch(values=[_fact("sr-old", hour=0, number=1)])
    assert _pointer(repo) == "sr-new"
    # A newer one does, and two revisions in one batch publish the newer.
    _record(repo, "sr-newest", hours_ago=0)
    repo.ingest_batch(
        values=[_fact("sr-newest", hour=1, number=3), _fact("sr-new", hour=1, number=2)]
    )
    assert _pointer(repo, hour=1) == "sr-newest"
    repo.ingest_batch(values=[_fact("sr-newest", hour=0, number=3)])
    assert _pointer(repo) == "sr-newest"


def _record_same_time(repo: WeatherRepository, *keys: str) -> None:
    fetched = kst_now() - timedelta(minutes=5)
    for key in keys:
        repo.record_source(
            source_record_key=key,
            provider="p",
            dataset_key="d",
            source_entity_type="test_response",
            source_entity_id="loc",
            payload={"rows": [key]},
            fetched_at=fetched,
        )


def test_projection_tie_on_known_at_breaks_bytewise_like_python() -> None:
    # Same ``known_at``: the source key decides, compared by code point as the
    # Python ordering always did ("a" > "B"), not by the database collation.
    repo = _repository("loc")
    _record_same_time(repo, "B-source", "a-source")
    repo.ingest_batch(values=[_fact("a-source", hour=0)])
    repo.ingest_batch(values=[_fact("B-source", hour=0)])
    assert _pointer(repo) == "a-source"


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [("B-source", "a-source", "a-source"), ("a-source", "B-source", None)],
)
def test_legacy_pointer_without_sort_key_compares_against_its_fact(
    first: str, second: str, expected: str | None
) -> None:
    # A pointer written during a deploy carries no ``source_record_key``; the
    # tie-break then reads it from the fact the pointer names.
    repo = _repository("loc")
    _record_same_time(repo, "B-source", "a-source")
    repo.ingest_batch(values=[_fact(first, hour=0)])
    with repo.engine.begin() as connection:
        connection.execute(text("UPDATE weather_current_values SET source_record_key = NULL"))
    repo.ingest_batch(values=[_fact(second, hour=0)])
    assert _pointer(repo) == expected


def test_ingest_gives_up_on_a_stuck_location_lock_instead_of_queueing(monkeypatch) -> None:
    # 2026-10-02: KMA jobs waited 2h+ on location advisory locks with no
    # lock_timeout.  An ingest now waits at most 3 x deadlock_timeout per
    # attempt and gives up after its retries, holding nothing in between.
    monkeypatch.setattr(repository_module, "INGEST_LOCK_ATTEMPTS", 2)
    monkeypatch.setattr(repository_module, "INGEST_LOCK_RETRY_SECONDS", 0.1)
    repo = _repository("loc")
    _record(repo, "sr-stuck")
    holder_engine = create_engine(TEST_DATABASE_URL, future=True)
    holder = holder_engine.connect()
    holder.begin()
    holder.execute(text("SELECT pg_advisory_xact_lock(hashtext('location:loc'))"))
    outcome: list[BaseException | int] = []

    def ingest() -> None:
        try:
            outcome.append(repo.ingest_batch(values=[_fact("sr-stuck", hour=0)]))
        except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
            outcome.append(exc)

    worker = threading.Thread(target=ingest, daemon=True)
    worker.start()
    try:
        worker.join(timeout=30)
        finished = not worker.is_alive()
    finally:
        holder.rollback()
        holder.close()
        holder_engine.dispose()
        worker.join(timeout=30)
    assert finished, "ingest queued behind a held location lock with no timeout"
    assert len(outcome) == 1 and isinstance(outcome[0], OperationalError)
    assert repository_module.is_lock_conflict(outcome[0])


def test_ingest_retries_a_lock_race_it_can_win(monkeypatch) -> None:
    # A holder that lets go within the retry budget costs a retry, not a run.
    monkeypatch.setattr(repository_module, "INGEST_LOCK_RETRY_SECONDS", 0.5)
    repo = _repository("loc")
    _record(repo, "sr-race")
    holder_engine = create_engine(TEST_DATABASE_URL, future=True)
    holder = holder_engine.connect()
    holder.begin()
    holder.execute(text("SELECT pg_advisory_xact_lock(hashtext('location:loc'))"))
    timer = threading.Timer(4.0, holder.rollback)
    timer.start()
    try:
        assert repo.ingest_batch(values=[_fact("sr-race", hour=0)]) == 1
    finally:
        timer.join()
        holder.close()
        holder_engine.dispose()
    assert _pointer(repo) == "sr-race"


@pytest.mark.parametrize("chunk_locations", [1, 3])
def test_location_locks_held_are_only_the_batchs_own(chunk_locations: int) -> None:
    # A batch's transaction holds exactly its own locations' advisory locks,
    # so publishing a run in location chunks bounds what any writer waits on.
    ids = [f"loc-{index}" for index in range(chunk_locations)]
    repo = _repository(*ids, "other")
    _record(repo, "sr-scope")
    seen: list[int] = []

    def probe(conn, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().startswith("INSERT INTO weather_current_values"):
            cursor.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND pid = pg_backend_pid() AND granted"
            )
            seen.append(cursor.fetchone()[0])

    event.listen(repo.engine, "before_cursor_execute", probe)
    try:
        repo.ingest_batch(values=[_fact("sr-scope", hour=0, location_id=i) for i in ids])
    finally:
        event.remove(repo.engine, "before_cursor_execute", probe)
    # One lock per location plus one for the cited source.
    assert seen == [chunk_locations + 1]


def test_skip_locked_ingest_publishes_the_free_locations_and_names_the_held_ones(
    monkeypatch,
) -> None:
    # 2026-10-03: kma_weather_alerts failed most hourly runs on one
    # ``location:airkorea-station-...`` lock held by another job's chunk for
    # longer than the whole retry budget.  The alerts publish now skips a
    # location whose lock is held -- without waiting on it at all -- and
    # publishes the rest of the chunk.
    monkeypatch.setattr(repository_module, "INGEST_LOCK_ATTEMPTS", 20)
    monkeypatch.setattr(repository_module, "INGEST_LOCK_RETRY_SECONDS", 3.0)
    repo = _repository("skip-a", "skip-b", "skip-c")
    _record(repo, "sr-skip")
    holder_engine = create_engine(TEST_DATABASE_URL, future=True)
    holder = holder_engine.connect()
    holder.begin()
    holder.execute(text("SELECT pg_advisory_xact_lock(hashtext('location:skip-b'))"))
    try:
        started = time.monotonic()
        loaded, skipped = repo.ingest_skip_locked(
            values=[
                _fact("sr-skip", hour=0, location_id=location)
                for location in ("skip-a", "skip-b", "skip-c")
            ]
        )
        elapsed = time.monotonic() - started
    finally:
        holder.rollback()
        holder.close()
        holder_engine.dispose()
    assert (loaded, skipped) == (2, ["skip-b"])
    # Not one lock_timeout: a held location is skipped, not waited on.
    assert elapsed < 2.0
    with repo.engine.connect() as connection:
        published = connection.execute(
            text(
                "SELECT location_id FROM weather_values WHERE source_record_key = 'sr-skip' "
                "ORDER BY location_id"
            )
        ).scalars().all()
    assert published == ["skip-a", "skip-c"]
    # The skipped location publishes on a later attempt once it is free.
    assert repo.ingest_skip_locked(values=[_fact("sr-skip", hour=0, location_id="skip-b")]) == (
        1,
        [],
    )


def test_skip_locked_ingest_still_takes_the_free_locks_in_order() -> None:
    # The lock-ordering guarantee is unchanged: the free locations' locks are
    # held, sorted, for the whole transaction, plus the cited source's.
    ids = ["order-c", "order-a", "order-b"]
    repo = _repository(*ids)
    _record(repo, "sr-order")
    taken: list[str] = []
    seen: list[int] = []

    def probe(conn, cursor, statement, parameters, context, executemany) -> None:
        if "pg_try_advisory_xact_lock" in statement or "pg_advisory_xact_lock" in statement:
            scope = (parameters or {}).get("scope") if isinstance(parameters, dict) else None
            if scope and scope.startswith("location:"):
                taken.append(scope)
        if statement.lstrip().startswith("INSERT INTO weather_current_values"):
            cursor.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND pid = pg_backend_pid() AND granted"
            )
            seen.append(cursor.fetchone()[0])

    event.listen(repo.engine, "before_cursor_execute", probe)
    try:
        repo.ingest_skip_locked(values=[_fact("sr-order", hour=0, location_id=i) for i in ids])
    finally:
        event.remove(repo.engine, "before_cursor_execute", probe)
    assert taken == ["location:order-a", "location:order-b", "location:order-c"]
    assert seen == [len(ids) + 1]
