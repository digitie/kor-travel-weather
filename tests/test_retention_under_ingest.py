"""Retention keeps working while ingestion never pauses.

Production (2026-10-02, 10-03, 10-05): ``weather_retention_job`` failed with
``LockNotAvailable`` on ``LOCK TABLE weather_values IN ACCESS EXCLUSIVE MODE``.
The collectors publish in overlapping short transactions around the clock, so
there is rarely a moment when nobody holds the fact table; twenty attempts
inside two minutes all lost, the whole run failed, and with it the 16-day
retention -- silently, since nothing watched how old the oldest day was.

These tests run against real PostgreSQL with writers that hold the same locks
an ingest holds (ROW EXCLUSIVE on ``weather_values`` and on
``weather_current_values``), in overlapping transactions.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import timedelta

import pytest
from alembic.config import Config
from sqlalchemy import text

from alembic import command
from kortravelweather import repository as repository_module
from kortravelweather.models import kst_now
from kortravelweather.partitions import (
    ensure_partitions,
    existing_partitions,
    partition_name,
)
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import get_settings

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()
    repo = WeatherRepository(TEST_DATABASE_URL)
    with repo.engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA public CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA public")
    command.upgrade(Config("alembic.ini"), "head")
    with repo.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO weather_locations (location_id, name, latitude, longitude, "
                "created_at, updated_at) VALUES ('ingest', 'ingest', 37, 127, now(), now())"
            )
        )
    yield repo
    repo.engine.dispose()
    # Leave the shared test database at head for the rest of the suite.
    command.upgrade(Config("alembic.ini"), "head")


class Ingest:
    """Writers holding what a collector's publish holds, in overlapping transactions.

    Each transaction inserts one fact into today's partition and takes ROW
    EXCLUSIVE on the projection (the upsert's lock), then keeps both for
    ``hold`` seconds.  With two writers offset by half a hold and no gap,
    some transaction holds the tables at every instant -- the production
    shape the old retention could never get past.
    """

    def __init__(
        self, repository: WeatherRepository, *, writers: int = 2, hold: float, gap: float = 0.0
    ) -> None:
        self._engine = repository.engine
        self._hold = hold
        self._gap = gap
        self._stop = threading.Event()
        self._started = threading.Barrier(writers + 1)
        self.errors: list[BaseException] = []
        self.commits = 0
        #: Longest time a writer spent beyond its own hold: waiting on a lock.
        self.longest_wait = 0.0
        self._threads = [
            threading.Thread(target=self._write, args=(index * hold / writers,), daemon=True)
            for index in range(writers)
        ]

    def __enter__(self) -> Ingest:
        for thread in self._threads:
            thread.start()
        self._started.wait(30)
        # Let the staggered writers all be inside a transaction.
        time.sleep(self._hold)
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(60)

    def _write(self, offset: float) -> None:
        self._started.wait(30)
        time.sleep(offset)
        while not self._stop.is_set():
            key = f"ingest-{uuid.uuid4().hex}"
            began = time.monotonic()
            try:
                with self._engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO weather_source_records (source_record_key, provider, "
                            "dataset_key, source_entity_type, source_entity_id, "
                            "raw_payload_hash, payload, fetched_at, imported_at) VALUES "
                            "(:key, 'p', 'd', 'weather_response', 'ingest', :key, '{}', "
                            "now(), now())"
                        ),
                        {"key": key},
                    )
                    connection.execute(
                        text(
                            "INSERT INTO weather_values (value_id, location_id, provider, "
                            "dataset_key, weather_domain, forecast_style, metric_key, "
                            "target_at, known_at, normalization_version, payload, "
                            "collected_at, source_record_key, value_number) VALUES "
                            "(:key, 'ingest', 'p', 'd', 'weather', 'short', 'TMP', now(), "
                            "now(), 'test', '{}', now(), :key, 1)"
                        ),
                        {"key": key},
                    )
                    connection.execute(
                        text("LOCK TABLE weather_current_values IN ROW EXCLUSIVE MODE")
                    )
                    waited = time.monotonic() - began
                    self.longest_wait = max(self.longest_wait, waited)
                    self._stop.wait(self._hold)
                self.commits += 1
            except BaseException as exc:  # noqa: BLE001 - reported by the test
                self.errors.append(exc)
                return
            if self._gap:
                self._stop.wait(self._gap)


def _names(repository: WeatherRepository) -> set[str]:
    with repository.engine.connect() as connection:
        return {name for name, _ in existing_partitions(connection)}


def _stage_expired_partition(repository: WeatherRepository, days_ago: int) -> str:
    day = kst_now().date() - timedelta(days=days_ago)
    with repository.engine.begin() as connection:
        ensure_partitions(connection, start=day, end=day)
    return partition_name(day)


def _foreign_keys_valid(repository: WeatherRepository) -> bool:
    with repository.engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT convalidated FROM pg_constraint WHERE contype = 'f' "
                "AND conrelid = 'weather_current_values'::regclass "
                "AND confrelid = 'weather_values'::regclass"
            )
        ).scalars().all()
    return bool(rows) and all(rows)


def test_retention_defers_instead_of_failing_while_ingest_never_pauses(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run finishes, says what it could not do, and blocks no writer for long.

    Before: ``purge_expired_history`` raised ``LockNotAvailable`` and the whole
    run failed -- the same as production.  Now each partition step gets a few
    spaced attempts; one that still cannot get its locks is deferred to the
    next run and reported, and the rest of the run carries on.
    """
    monkeypatch.setattr(repository_module, "PARTITION_DDL_ATTEMPTS", 2)
    monkeypatch.setattr(repository_module, "PARTITION_DDL_RETRY_SECONDS", 0.3)
    expired = _stage_expired_partition(repository, days_ago=30)
    today = kst_now().date()
    beyond = [partition_name(today + timedelta(days=n)) for n in (8, 9)]

    # Each transaction outlasts the DDL lock timeout (three deadlock_timeouts,
    # 3 s here), as production publishes of up to a minute do; shorter ones
    # are simply waited out by a queued request and prove nothing.
    with Ingest(repository, hold=8.0) as ingest:
        report = repository.purge_expired_history(retention_days=2, ahead_days=9)
        commits_during = ingest.commits

    assert ingest.errors == []
    assert commits_during > 0
    # A writer waits for at most one attempt's lock timeout, never the run.
    assert ingest.longest_wait < 5.0, ingest.longest_wait
    assert report.partitions_dropped == ()
    assert report.partitions_deferred == (expired,)
    # 30 days old against a 2-day window: it was due long before tonight.
    assert report.partitions_overdue == (expired,)
    assert report.partitions_created == ()
    assert report.partitions_not_created == tuple(beyond)
    assert expired in _names(repository)
    assert _foreign_keys_valid(repository)

    # The next run with a quiet table does what this one deferred.
    report = repository.purge_expired_history(retention_days=2, ahead_days=9)
    assert report.partitions_dropped == (expired,)
    assert report.partitions_deferred == ()
    assert report.partitions_overdue == ()
    assert report.partitions_created == tuple(beyond)
    assert report.partitions_not_created == ()
    names = _names(repository)
    assert expired not in names
    assert set(beyond) <= names
    assert _foreign_keys_valid(repository)


def test_a_spaced_retry_lands_in_the_gap_between_ingest_transactions(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Between attempts the job watches for the moment nobody holds the tables."""
    monkeypatch.setattr(repository_module, "PARTITION_DDL_ATTEMPTS", 4)
    monkeypatch.setattr(repository_module, "PARTITION_DDL_RETRY_SECONDS", 8.0)
    expired = _stage_expired_partition(repository, days_ago=5)
    tomorrow_week = partition_name(kst_now().date() + timedelta(days=8))

    with Ingest(repository, writers=1, hold=4.0, gap=0.8) as ingest:
        report = repository.purge_expired_history(retention_days=2, ahead_days=8)

    assert ingest.errors == []
    assert report.partitions_dropped == (expired,)
    assert report.partitions_deferred == ()
    assert report.partitions_created == (tomorrow_week,)
    assert report.partitions_not_created == ()
    assert _foreign_keys_valid(repository)


def test_one_partition_at_a_time_so_a_backlog_drains_partially(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each expired day is its own short transaction, oldest first.

    A backlog of several days is dropped one by one, so a lost race costs one
    day -- not the whole run -- and each lock is held for one detach only.
    """
    expired = [_stage_expired_partition(repository, days_ago=n) for n in (12, 11, 10)]
    statements: list[str] = []

    original = repository_module.detach_partition

    def recording(connection, name):
        statements.append(name)
        return original(connection, name)

    monkeypatch.setattr(repository_module, "detach_partition", recording)
    report = repository.purge_expired_history(retention_days=2, ahead_days=7)
    assert report.partitions_dropped == tuple(expired)
    assert statements == expired
    assert _foreign_keys_valid(repository)


def test_the_oldest_partition_age_is_observable(repository: WeatherRepository) -> None:
    """The staleness signal: how many days old the oldest dated partition is."""
    from kortravelweather.metrics import oldest_partition_age_exposition

    # head's window starts yesterday.
    assert repository.oldest_partition_age_days() == 1
    _stage_expired_partition(repository, days_ago=20)
    assert repository.oldest_partition_age_days() == 20
    exposition = oldest_partition_age_exposition(repository.oldest_partition_age_days())
    assert b"ktw_oldest_partition_age_days 20.0" in exposition
    repository.purge_expired_history(retention_days=16, ahead_days=7)
    assert repository.oldest_partition_age_days() == 1
