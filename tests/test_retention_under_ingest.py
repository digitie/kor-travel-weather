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
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool

from alembic import command
from kortravelweather import partitions as partitions_module
from kortravelweather import repository as repository_module
from kortravelweather.models import kst_now
from kortravelweather.partitions import (
    AUTOVACUUM_BACKEND,
    BRIEF_DDL_LOCK_TIMEOUT_MS,
    VALUES_TABLE,
    LockHolder,
    blocking_holder_kinds,
    conflicting_lock_holders,
    ensure_partitions,
    existing_partitions,
    is_lock_conflict,
    lock_for_partition_ddl,
    partition_name,
)
from kortravelweather.repository import WeatherRepository
from kortravelweather.retention import ShortForecastPartitionRetained
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
    # 30 days past due by the calendar, but tonight is the first attempt: a
    # miss is counted from real attempts (review of db10c64).
    assert report.partitions_overdue == ()
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


def test_a_deferred_drop_leaves_the_sources_it_cites_for_the_next_run(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detach and drop are separate transactions, so a drop can be deferred.

    The detached table still holds its facts and their foreign key to the
    source records (``ON DELETE RESTRICT``), while the source purge looks only
    at ``weather_values``: purging those sources would fail the run.  They wait
    for the run that drops the table.
    """
    from types import SimpleNamespace

    from sqlalchemy.exc import OperationalError

    expired = _stage_expired_partition(repository, days_ago=10)
    known_at = kst_now() - timedelta(days=10)
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO weather_source_records (source_record_key, provider, "
                "dataset_key, source_entity_type, source_entity_id, raw_payload_hash, "
                "payload, fetched_at, imported_at) VALUES ('old', 'p', 'd', "
                "'weather_response', 'ingest', 'old', '{}', :at, :at)"
            ),
            {"at": known_at},
        )
        connection.execute(
            text(
                "INSERT INTO weather_values (value_id, location_id, provider, dataset_key, "
                "weather_domain, forecast_style, metric_key, target_at, known_at, "
                "normalization_version, payload, collected_at, source_record_key, "
                "value_number) VALUES ('old', 'ingest', 'p', 'd', 'weather', 'short', "
                "'TMP', :at, :at, 'test', '{}', :at, 'old', 1)"
            ),
            {"at": known_at},
        )

    def lost_race(connection, **_):
        raise OperationalError("LOCK TABLE", {}, SimpleNamespace(sqlstate="55P03"))

    monkeypatch.setattr(repository_module, "PARTITION_DDL_ATTEMPTS", 1)
    monkeypatch.setattr(repository_module, "lock_for_detached_drop", lost_race)
    report = repository.purge_expired_history(retention_days=2, ahead_days=7)
    assert report.partitions_deferred == (expired,)
    assert report.sources_deleted == 0
    assert expired not in _names(repository)  # detached, not dropped

    monkeypatch.undo()
    report = repository.purge_expired_history(retention_days=2, ahead_days=7)
    assert report.partitions_dropped == (expired,)
    assert report.sources_deleted >= 1
    with repository.engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM weather_source_records WHERE source_record_key = 'old'")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT to_regclass(:t)"), {"t": expired}
        ).scalar_one() is None


class _LocationReader:
    """A session that keeps reading ``weather_locations`` -- the API does.

    It holds ACCESS SHARE on the table, which only the drop of a detached
    partition conflicts with (ACCESS EXCLUSIVE on the referenced tables).
    """

    def __init__(self, repository: WeatherRepository) -> None:
        self._connection = repository.engine.connect()

    def __enter__(self) -> _LocationReader:
        self._transaction = self._connection.begin()
        self._connection.execute(text("SELECT count(*) FROM weather_locations"))
        return self

    def __exit__(self, *exc: object) -> None:
        self._transaction.rollback()
        self._connection.close()


def _stage_fact(repository: WeatherRepository, key: str, *, days_ago: int, fact: bool) -> None:
    at = kst_now() - timedelta(days=days_ago)
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO weather_source_records (source_record_key, provider, "
                "dataset_key, source_entity_type, source_entity_id, raw_payload_hash, "
                "payload, fetched_at, imported_at) VALUES (:key, 'p', 'd', "
                "'weather_response', 'ingest', :key, '{}', :at, :at)"
            ),
            {"key": key, "at": at},
        )
        if fact:
            connection.execute(
                text(
                    "INSERT INTO weather_values (value_id, location_id, provider, "
                    "dataset_key, weather_domain, forecast_style, metric_key, target_at, "
                    "known_at, normalization_version, payload, collected_at, "
                    "source_record_key, value_number) VALUES (:key, 'ingest', 'p', 'd', "
                    "'weather', 'short', 'TMP', :at, :at, 'test', '{}', :at, :key, 1)"
                ),
                {"key": key, "at": at},
            )


def _source_exists(repository: WeatherRepository, key: str) -> bool:
    with repository.engine.connect() as connection:
        return bool(
            connection.execute(
                text("SELECT count(*) FROM weather_source_records WHERE source_record_key = :k"),
                {"k": key},
            ).scalar_one()
        )


def _leftovers(repository: WeatherRepository) -> list[str]:
    from kortravelweather.partitions import detached_leftovers

    with repository.engine.connect() as connection:
        return [name for name, _ in detached_leftovers(connection)]


def test_a_drop_held_by_a_location_reader_does_not_pile_up_detached_days(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review MED (db10c64): detach succeeds, the drop keeps losing.

    Before: every expired day was detached anyway, the detached tables piled
    up out of sight of the staleness gauge, the whole source purge stopped,
    and the run stayed green.  Now no further day is detached behind a
    deferred drop, the gauge counts the leftover, only the sources the
    leftover cites wait, and a second consecutive miss is overdue -- counted
    from real attempts, not from the calendar (both days are weeks past due
    by date, but the job never tried them before).
    """
    monkeypatch.setattr(repository_module, "PARTITION_DDL_ATTEMPTS", 2)
    monkeypatch.setattr(repository_module, "PARTITION_DDL_RETRY_SECONDS", 0.3)
    older = _stage_expired_partition(repository, days_ago=12)
    newer = _stage_expired_partition(repository, days_ago=11)
    _stage_fact(repository, "cited", days_ago=12, fact=True)
    _stage_fact(repository, "loose", days_ago=12, fact=False)

    with _LocationReader(repository):
        first = repository.purge_expired_history(retention_days=2, ahead_days=7)
        assert first.partitions_dropped == ()
        assert first.partitions_deferred == (older, newer)
        assert first.partitions_overdue == ()  # never attempted before tonight
        assert _leftovers(repository) == [older]
        assert newer in _names(repository)  # not detached behind the deferred drop
        assert repository.oldest_partition_age_days() == 12
        assert _source_exists(repository, "cited")
        assert not _source_exists(repository, "loose")

        second = repository.purge_expired_history(retention_days=2, ahead_days=7)
        assert second.partitions_deferred == (older, newer)
        # The leftover's drop lost on the previous run too; the newer day was
        # never attempted, so it is not a miss.
        assert second.partitions_overdue == (older,)

    third = repository.purge_expired_history(retention_days=2, ahead_days=7)
    assert third.partitions_dropped == (older, newer)
    assert third.partitions_deferred == ()
    assert _leftovers(repository) == []
    assert not _source_exists(repository, "cited")
    assert _foreign_keys_valid(repository)


def test_three_deferrals_in_a_row_stop_the_run(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backlog of lost races must not eat the run's 2h cap: after three
    consecutive deferred steps the run stops trying and reports the rest."""
    monkeypatch.setattr(repository_module, "PARTITION_DDL_ATTEMPTS", 2)
    monkeypatch.setattr(repository_module, "PARTITION_DDL_RETRY_SECONDS", 0.2)
    calls: list[int] = []
    original = repository_module.lock_for_partition_ddl

    def counting(connection, **kwargs):
        calls.append(1)
        return original(connection, **kwargs)

    monkeypatch.setattr(repository_module, "lock_for_partition_ddl", counting)
    today = kst_now().date()
    beyond = tuple(partition_name(today + timedelta(days=n)) for n in range(8, 13))
    holder = repository.engine.connect()
    transaction = holder.begin()
    holder.execute(text("LOCK TABLE weather_values IN ROW EXCLUSIVE MODE"))
    try:
        report = repository.purge_expired_history(retention_days=2, ahead_days=12)
    finally:
        transaction.rollback()
        holder.close()
    assert report.partitions_not_created == beyond
    assert report.partition_ddl_stopped is True
    assert len(calls) == 3 * 2  # three steps of two attempts, then nothing


def test_a_validation_that_times_out_leaves_the_key_not_valid(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end-of-run VALIDATE scans the projection; past its statement
    timeout it gives up and the next run validates instead."""
    from types import SimpleNamespace

    from sqlalchemy.exc import OperationalError

    from kortravelweather.partitions import (
        VALIDATE_STATEMENT_TIMEOUT,
        drop_foreign_keys_into_facts,
        readd_foreign_keys_not_valid,
    )

    assert VALIDATE_STATEMENT_TIMEOUT == "10min"
    with repository.engine.begin() as connection:
        readd_foreign_keys_not_valid(connection, drop_foreign_keys_into_facts(connection))

    def timed_out(connection):
        raise OperationalError("VALIDATE", {}, SimpleNamespace(sqlstate="57014"))

    monkeypatch.setattr(repository_module, "validate_foreign_keys_into_facts", timed_out)
    assert repository._validate_fact_foreign_keys() is False
    assert not _foreign_keys_valid(repository)
    monkeypatch.undo()
    assert repository._validate_fact_foreign_keys() is True
    assert _foreign_keys_valid(repository)


def test_only_the_drop_waits_for_location_readers(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review LOW3 (6c88c01): create and detach take SHARE ROW EXCLUSIVE on
    ``weather_locations``, which API readers do not block, so their retries
    must not wait for those readers; the drop (ACCESS EXCLUSIVE) must."""
    import time as clock
    from types import SimpleNamespace

    from sqlalchemy.exc import OperationalError

    monkeypatch.setattr(repository_module, "PARTITION_DDL_ATTEMPTS", 2)
    monkeypatch.setattr(repository_module, "PARTITION_DDL_RETRY_SECONDS", 4.0)

    def lose_once():
        calls: list[int] = []

        def step(connection):
            calls.append(1)
            if len(calls) == 1:
                raise OperationalError("LOCK", {}, SimpleNamespace(sqlstate="55P03"))
            return True

        return step

    with _LocationReader(repository):
        began = clock.monotonic()
        assert repository._try_partition_ddl(lose_once(), breaker="retention") == (True, True)
        default_wait = clock.monotonic() - began
        began = clock.monotonic()
        assert repository._try_partition_ddl(
            lose_once(),
            breaker="retention",
            quiet_on=repository_module._DETACHED_DROP_HOT_TABLES,
        ) == (True, True)
        drop_wait = clock.monotonic() - began
    assert default_wait < 2.0, default_wait
    assert drop_wait >= 3.5, drop_wait


def test_marking_a_day_another_run_dropped_is_skipped(repository: WeatherRepository) -> None:
    """Review LOW2 (6c88c01): the day can vanish between the deferral and the
    mark; that is no reason to fail the run.  Nothing is left to mark, so no
    mark is lost either: ``(marked before, written)`` is ``(False, True)``."""
    assert repository._mark_deferred("weather_values_19990101") == (False, True)


def _lost_race(*_: object) -> None:
    from types import SimpleNamespace

    from sqlalchemy.exc import OperationalError

    raise OperationalError("LOCK TABLE", {}, SimpleNamespace(sqlstate="55P03"))


def test_a_stuck_forward_step_does_not_starve_retention(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review LOW1 (dd99edd): the breaker counter was shared across steps.

    Forward creation runs first, so three forward days lost in a row tripped
    the breaker before any expired day was tried; those days were skipped
    unmarked, never became overdue, and the run stayed ``deferred`` night
    after night while nothing was dropped.  Forward creation and detach/drop
    now keep a breaker each.
    """
    monkeypatch.setattr(repository_module, "PARTITION_DDL_ATTEMPTS", 1)
    monkeypatch.setattr(WeatherRepository, "_create_forward_day", staticmethod(_lost_race))
    expired = _stage_expired_partition(repository, days_ago=10)
    today = kst_now().date()
    beyond = tuple(partition_name(today + timedelta(days=n)) for n in range(8, 13))

    report = repository.purge_expired_history(retention_days=2, ahead_days=12)

    assert report.partitions_not_created == beyond
    assert report.partition_ddl_stopped is True  # the forward breaker
    assert report.partitions_dropped == (expired,)
    assert report.partitions_deferred == ()
    assert expired not in _names(repository)


def test_days_the_breaker_skipped_on_consecutive_runs_are_overdue(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review LOW1 (dd99edd): a day that was due and skipped by the breaker
    is a miss like one that was tried, so two runs in a row make it overdue.

    Only a day held behind a waiting drop is still no miss (the leftover in
    front of it is the one counted, see the pile-up test above).
    """
    monkeypatch.setattr(repository_module, "PARTITION_DDL_ATTEMPTS", 1)
    monkeypatch.setattr(WeatherRepository, "_detach_expired", staticmethod(_lost_race))
    days = tuple(_stage_expired_partition(repository, days_ago=n) for n in (14, 13, 12, 11))

    first = repository.purge_expired_history(retention_days=2, ahead_days=7)
    assert first.partitions_deferred == days
    assert first.partition_ddl_stopped is True  # three tried, the fourth skipped
    assert first.partitions_overdue == ()

    second = repository.purge_expired_history(retention_days=2, ahead_days=7)
    assert second.partitions_deferred == days
    assert second.partitions_overdue == days


class _CommentBlocker:
    """Holds SHARE UPDATE EXCLUSIVE on one table -- what autovacuum holds --
    so a ``COMMENT ON TABLE`` there loses its lock race.

    ``release_after`` lost mark attempts, the hold ends (``None``: never,
    until the block exits).  A lost attempt is seen as a waiter on the table
    in ``pg_locks`` that went away again.
    """

    def __init__(self, repository: WeatherRepository, table: str, *, release_after: int | None):
        self._engine = repository.engine
        self._table = table
        self._release_after = release_after
        self._stop = threading.Event()
        self._held = threading.Event()
        self.lost_attempts = 0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> _CommentBlocker:
        self._thread.start()
        assert self._held.wait(30)
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(60)

    def _run(self) -> None:
        holder = self._engine.connect()
        transaction = holder.begin()
        holder.execute(text(f"LOCK TABLE {self._table} IN SHARE UPDATE EXCLUSIVE MODE"))
        self._held.set()
        waiting = False
        with self._engine.connect() as watcher:
            while True:
                # Read once more after the stop: the run may have returned
                # within one poll of its last lost attempt.
                stopping = self._stop.is_set()
                now_waiting = bool(
                    watcher.execute(
                        text(
                            "SELECT count(*) FROM pg_locks WHERE NOT granted "
                            "AND relation = to_regclass(:t)"
                        ),
                        {"t": self._table},
                    ).scalar_one()
                )
                watcher.rollback()
                if waiting and not now_waiting:
                    self.lost_attempts += 1
                    if self._release_after is not None and (
                        self.lost_attempts >= self._release_after
                    ):
                        break
                waiting = now_waiting
                if stopping:
                    break
                time.sleep(0.02)
        transaction.rollback()
        holder.close()


def test_a_mark_that_loses_its_lock_race_is_retried(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review LOW2 (dd99edd): one lost ``COMMENT ON TABLE`` race lost the mark,
    and the next run undercounted "overdue".  The mark is retried a few times
    with a short lock timeout."""
    monkeypatch.setattr(repository_module, "PARTITION_DDL_ATTEMPTS", 1)
    monkeypatch.setattr(WeatherRepository, "_detach_expired", staticmethod(_lost_race))
    expired = _stage_expired_partition(repository, days_ago=10)

    with _CommentBlocker(repository, expired, release_after=1) as blocker:
        first = repository.purge_expired_history(retention_days=2, ahead_days=0)
    assert blocker.lost_attempts == 1
    assert first.partitions_deferred == (expired,)
    assert first.partitions_unmarked == ()

    second = repository.purge_expired_history(retention_days=2, ahead_days=7)
    assert second.partitions_overdue == (expired,)


def test_a_mark_that_never_lands_is_carried_by_the_run_record(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review LOW2 (dd99edd): when every retry loses, the run reports the day as
    ``partitions_unmarked``; the next run is handed that list (the Dagster
    asset reads it back from the previous run's record) and counts the day as
    marked."""
    monkeypatch.setattr(repository_module, "PARTITION_DDL_ATTEMPTS", 1)
    monkeypatch.setattr(WeatherRepository, "_detach_expired", staticmethod(_lost_race))
    expired = _stage_expired_partition(repository, days_ago=10)

    with _CommentBlocker(repository, expired, release_after=None) as blocker:
        first = repository.purge_expired_history(retention_days=2, ahead_days=0)
    assert blocker.lost_attempts == repository_module.DEFERRAL_MARK_ATTEMPTS
    assert first.partitions_deferred == (expired,)
    assert first.partitions_unmarked == (expired,)

    second = repository.purge_expired_history(
        retention_days=2, ahead_days=7, prior_unmarked=first.partitions_unmarked
    )
    assert second.partitions_overdue == (expired,)
    assert second.partitions_unmarked == ()  # written tonight

    # And from here on the mark itself carries it.
    third = repository.purge_expired_history(retention_days=2, ahead_days=7)
    assert third.partitions_overdue == (expired,)


# 2026-11-24 Map 호출 검토 L1·L2·L3: 야간 파티션 유지보수가 지도 읽기를 세우는 lock 구간을 줄인다.


class _Holder:
    """다른 세션이 ``statement``의 표 lock을 쥐고 있다가 놓는다.

    ``release_after``초 뒤에 놓고, 없으면 블록이 끝날 때 놓는다.
    """

    def __init__(
        self, repository: WeatherRepository, statement: str, *, release_after: float | None = None
    ) -> None:
        self._engine = repository.engine
        self._statement = statement
        self._release_after = release_after
        self._held = threading.Event()
        self._stop = threading.Event()
        self.errors: list[BaseException] = []
        #: lock을 쥔 세션의 pid.
        self.pid: int | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> _Holder:
        self._thread.start()
        assert self._held.wait(30)
        assert self.errors == []
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(60)

    def _run(self) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(text(self._statement))
                self.pid = int(connection.execute(text("SELECT pg_backend_pid()")).scalar_one())
                self._held.set()
                self._stop.wait(self._release_after)
        except BaseException as exc:  # noqa: BLE001 - reported by the test
            self.errors.append(exc)
            self._held.set()


class _ReaderProbe:
    """지도 읽기처럼 ``weather_values``를 짧은 transaction으로 계속 읽는다.

    ``longest``는 가장 오래 걸린 읽기 문장의 시간(초)이다. ``hold``초 동안 읽은 뒤의
    transaction을 열어 둔다(그동안 ACCESS SHARE를 쥔다).
    """

    def __init__(self, repository: WeatherRepository, *, hold: float = 0.0) -> None:
        self._engine = repository.engine
        self._hold = hold
        self._stop = threading.Event()
        self.longest = 0.0
        self.reads = 0
        self.errors: list[BaseException] = []
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> _ReaderProbe:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(60)

    def _run(self) -> None:
        try:
            with self._engine.connect() as connection:
                while not self._stop.is_set():
                    began = time.monotonic()
                    connection.execute(text("SELECT count(*) FROM weather_values"))
                    self.longest = max(self.longest, time.monotonic() - began)
                    if self._hold:
                        self._stop.wait(self._hold)
                    connection.rollback()
                    self.reads += 1
                    self._stop.wait(0.02)
        except BaseException as exc:  # noqa: BLE001 - reported by the test
            self.errors.append(exc)


def _committed_statements(engine, run):
    """``run()``의 결과와, 그동안 commit된 transaction별 SQL 목록(commit 순서)."""
    log: list[tuple[int, str, str]] = []

    def on_begin(connection) -> None:
        log.append((id(connection), "begin", ""))

    def on_commit(connection) -> None:
        log.append((id(connection), "commit", ""))

    def on_execute(connection, _cursor, statement, *_: object) -> None:
        log.append((id(connection), "sql", " ".join(statement.split())))

    event.listen(engine, "begin", on_begin)
    event.listen(engine, "commit", on_commit)
    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        result = run()
    finally:
        event.remove(engine, "begin", on_begin)
        event.remove(engine, "commit", on_commit)
        event.remove(engine, "before_cursor_execute", on_execute)
    open_tx: dict[int, list[str]] = {}
    committed: list[list[str]] = []
    for connection_id, kind, statement in log:
        if kind == "begin":
            open_tx[connection_id] = []
        elif kind == "commit":
            committed.append(open_tx.pop(connection_id, []))
        elif connection_id in open_tx:
            open_tx[connection_id].append(statement)
    return result, committed


def _stage_short_forecast(connection, key: str, *, known_at) -> None:
    """``known_at``(오래된 발표)에 미래 대상시각을 가진 KMA 단기예보 한 건: 보존 대상이다."""
    valid_at = kst_now() + timedelta(days=1)
    connection.execute(
        text(
            "INSERT INTO weather_source_records (source_record_key, provider, dataset_key, "
            "source_entity_type, source_entity_id, raw_payload_hash, payload, fetched_at, "
            "imported_at) VALUES (:key, 'python-kma-api', 'kma_short_forecast', "
            "'weather_response', 'ingest', :key, '{}', :at, :at)"
        ),
        {"key": key, "at": known_at},
    )
    connection.execute(
        text(
            "INSERT INTO weather_values (value_id, location_id, provider, dataset_key, "
            "weather_domain, forecast_style, metric_key, target_at, valid_at, known_at, "
            "normalization_version, payload, collected_at, source_record_key, value_number) "
            "VALUES (:key, 'ingest', 'python-kma-api', 'kma_short_forecast', "
            "'kma_short_forecast', 'short', 'TMP', :valid, :valid, :at, 'test', '{}', :at, "
            ":key, 1)"
        ),
        {"key": key, "at": known_at, "valid": valid_at},
    )


def test_the_short_forecast_guard_runs_before_the_exclusive_locks(
    repository: WeatherRepository,
) -> None:
    """L1·L2: 보존 검사(상한 5초)는 뜨거운 표 lock 전에, lock 뒤에는 짧은 재확인만 한다.

    예전에는 detach가 ``weather_values``·``weather_current_values`` ACCESS EXCLUSIVE를,
    drop이 지도 API가 읽는 ``weather_locations`` ACCESS EXCLUSIVE를 쥔 채 그 검사를 돌렸다.
    """
    expired = _stage_expired_partition(repository, days_ago=10)
    report, committed = _committed_statements(
        repository.engine,
        lambda: repository.purge_expired_history(retention_days=2, ahead_days=7),
    )
    assert report.partitions_dropped == (expired,)

    def only(fragment: str) -> list[str]:
        found = [tx for tx in committed if any(fragment in sql for sql in tx)]
        assert len(found) == 1, committed
        return found[0]

    def where(tx: list[str], prefix: str) -> list[int]:
        return [index for index, sql in enumerate(tx) if sql.startswith(prefix)]

    def budget_before(tx: list[str], position: int) -> str:
        return [sql for sql in tx[:position] if sql.startswith("SET LOCAL statement_timeout")][-1]

    detach = only(f"DETACH PARTITION {expired}")
    locks = where(detach, "LOCK TABLE")
    guards = where(detach, "SELECT EXISTS (SELECT 1 FROM")
    assert [detach[index] for index in locks] == [
        "LOCK TABLE weather_source_records IN SHARE ROW EXCLUSIVE MODE",
        "LOCK TABLE weather_locations IN SHARE ROW EXCLUSIVE MODE",
        "LOCK TABLE weather_values IN ACCESS EXCLUSIVE MODE",
        "LOCK TABLE weather_current_values IN ACCESS EXCLUSIVE MODE",
    ]
    assert len(guards) == 2 and guards[0] < locks[0] and guards[1] > locks[-1], detach
    assert budget_before(detach, guards[0]) == "SET LOCAL statement_timeout = '5000ms'"
    assert budget_before(detach, guards[1]) == "SET LOCAL statement_timeout = '300ms'"
    assert f"SET LOCAL lock_timeout = '{BRIEF_DDL_LOCK_TIMEOUT_MS}ms'" in detach

    drop = only(f"DROP TABLE {expired}")
    locks = where(drop, "LOCK TABLE")
    guards = where(drop, "SELECT EXISTS (SELECT 1 FROM")
    assert [drop[index] for index in locks] == [
        f"LOCK TABLE {expired} IN SHARE MODE",
        "LOCK TABLE weather_source_records IN ACCESS EXCLUSIVE MODE",
        "LOCK TABLE weather_locations IN ACCESS EXCLUSIVE MODE",
        f"LOCK TABLE {expired} IN ACCESS EXCLUSIVE MODE",
    ]
    # 떼어낸 표만 SHARE로 쓰기를 막은 뒤 한 번 검사하고, 참조 표 lock 뒤에는 검사가 없다.
    assert len(guards) == 1 and locks[0] < guards[0] < locks[1], drop


def test_a_short_forecast_landing_between_the_check_and_the_lock_keeps_the_day(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L1 경합: lock 전 검사가 통과한 뒤 그 날짜로 보존할 단기예보가 commit돼도 잃지 않는다.

    늦은 재수집이 오래된 발표의 미래 대상시각을 쓰는 경우다. lock 뒤 재확인이 없으면 이 실행의
    DETACH·DROP이 그 행을 지운다. lock을 쥔 뒤에는 그 날짜에 새 행이 들어올 수 없다.
    """
    expired = _stage_expired_partition(repository, days_ago=10)
    known_at = kst_now() - timedelta(days=10)
    checks: list[tuple[int, str]] = []
    landed: list[str] = []
    original_guard = WeatherRepository._preserve_short_forecast_partition
    original_lock = repository_module.lock_for_partition_ddl

    def guard(
        connection, name, *, cap_ms=repository_module.PARTITION_GUARD_STATEMENT_TIMEOUT_MS
    ) -> None:
        try:
            original_guard(connection, name, cap_ms=cap_ms)
        except ShortForecastPartitionRetained:
            checks.append((cap_ms, "retained"))
            raise
        checks.append((cap_ms, "clear"))

    def lock_after_a_late_write(connection, **kwargs) -> None:
        if kwargs.get("drop_foreign_keys") and not landed:
            # 검사와 lock 사이: 다른 세션이 그 날짜로 미래 대상시각 단기예보를 commit한다.
            with repository.engine.begin() as writer:
                _stage_short_forecast(writer, "late", known_at=known_at)
            landed.append("late")
        original_lock(connection, **kwargs)

    monkeypatch.setattr(
        WeatherRepository, "_preserve_short_forecast_partition", staticmethod(guard)
    )
    monkeypatch.setattr(repository_module, "lock_for_partition_ddl", lock_after_a_late_write)
    report = repository.purge_expired_history(retention_days=2, ahead_days=7)

    assert landed == ["late"]
    # 첫 검사는 비어 있음을 확인했고, 그 뒤 들어온 행을 lock 뒤 재확인이 잡았다.
    assert checks == [
        (repository_module.PARTITION_GUARD_STATEMENT_TIMEOUT_MS, "clear"),
        (repository_module.PARTITION_RECHECK_STATEMENT_TIMEOUT_MS, "retained"),
    ]
    assert report.partitions_retained_short_forecast == (expired,)
    assert report.partitions_dropped == () and report.partitions_deferred == ()
    assert expired in _names(repository)
    with repository.engine.connect() as connection:
        assert connection.execute(
            text(f"SELECT value_id FROM {expired}")
        ).scalars().all() == ["late"]
    assert _source_exists(repository, "late")
    assert _foreign_keys_valid(repository)


def test_a_recheck_that_overruns_its_cap_defers_the_day_instead_of_failing(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L1: lock 뒤 재확인이 짧은 상한을 넘으면 reader를 더 세우지 않고 연기한다(실패 아님)."""
    monkeypatch.setattr(repository_module, "PARTITION_DDL_ATTEMPTS", 2)
    monkeypatch.setattr(repository_module, "PARTITION_DDL_RETRY_SECONDS", 0.05)
    expired = _stage_expired_partition(repository, days_ago=10)
    original_guard = WeatherRepository._preserve_short_forecast_partition
    slow: list[bool] = [True]
    rechecks: list[int] = []

    def guard(
        connection, name, *, cap_ms=repository_module.PARTITION_GUARD_STATEMENT_TIMEOUT_MS
    ) -> None:
        if slow[0] and cap_ms == repository_module.PARTITION_RECHECK_STATEMENT_TIMEOUT_MS:
            rechecks.append(cap_ms)
            # PostgreSQL이 실제로 문장 상한으로 취소하게 한다(57014).
            connection.execute(text("SET LOCAL statement_timeout = '50ms'"))
            connection.execute(text("SELECT pg_sleep(1)"))
        original_guard(connection, name, cap_ms=cap_ms)

    monkeypatch.setattr(
        WeatherRepository, "_preserve_short_forecast_partition", staticmethod(guard)
    )
    report = repository.purge_expired_history(retention_days=2, ahead_days=7)
    assert len(rechecks) == 2  # 두 시도 모두 lock 뒤 재확인에서 상한을 넘었다
    assert report.partitions_deferred == (expired,)
    assert report.partitions_dropped == ()
    assert report.partitions_retained_short_forecast == ()
    assert expired in _names(repository)
    assert _foreign_keys_valid(repository)

    slow[0] = False
    report = repository.purge_expired_history(retention_days=2, ahead_days=7)
    assert report.partitions_dropped == (expired,)


def test_a_brief_ddl_lock_wait_gives_way_to_a_writer_quickly(
    repository: WeatherRepository,
) -> None:
    """L3: 야간 DDL은 수집에 지면 짧은 상한에 물러난다. 운영자·migration 경로는 그대로다."""
    from sqlalchemy.exc import OperationalError

    with _Holder(repository, "LOCK TABLE weather_values IN ROW EXCLUSIVE MODE"):
        for brief, low, high in ((True, 0.3, 1.5), (False, 2.0, 10.0)):
            began = time.monotonic()
            with (
                pytest.raises(OperationalError) as caught,
                repository.engine.begin() as connection,
            ):
                lock_for_partition_ddl(connection, drop_foreign_keys=True, brief=brief)
            waited = time.monotonic() - began
            assert is_lock_conflict(caught.value)
            assert low <= waited < high, (brief, waited)


def _relabel_as_autovacuum(
    monkeypatch: pytest.MonkeyPatch, pid: int | None
) -> list[tuple[str, str, set[LockHolder]]]:
    """``pid`` 세션을 autovacuum worker로 보이게 한다. 돌려준 목록에 물음마다 (표, mode, 보유자).

    시험에서 autovacuum worker를 마음대로 띄울 수 없다. 판정 SQL은 그대로 실행하고 그 결과에서
    대역 세션의 종류만 바꾼다. ``pid``가 ``None``이면 아무것도 바꾸지 않고 기록만 한다.
    """
    real = partitions_module.conflicting_lock_holders
    seen: list[tuple[str, str, set[LockHolder]]] = []

    def relabeled(connection, table: str, mode: str) -> set[LockHolder]:
        holders = {
            holder._replace(kind=AUTOVACUUM_BACKEND)
            if pid is not None and holder.pid == pid
            else holder
            for holder in real(connection, table, mode)
        }
        seen.append((table, mode, holders))
        return holders

    monkeypatch.setattr(partitions_module, "conflicting_lock_holders", relabeled)
    return seen


def test_only_an_autovacuum_blocker_gets_the_long_wait(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L3: 짧은 상한에 진 뒤 차단자가 autovacuum뿐이면 ``ddl_lock_timeout_ms``까지 기다린다.

    autovacuum은 대기자가 deadlock_timeout을 넘겨 기다려야 그 deadlock 검사가 취소한다.
    대역 세션이 1.2초 뒤에 lock을 놓아 그 취소를 흉내 낸다.
    """
    with _Holder(
        repository, "LOCK TABLE weather_values IN ROW EXCLUSIVE MODE", release_after=1.2
    ) as stand_in:
        seen = _relabel_as_autovacuum(monkeypatch, stand_in.pid)
        began = time.monotonic()
        with repository.engine.begin() as connection:
            lock_for_partition_ddl(connection, drop_foreign_keys=True, brief=True)
            waited = time.monotonic() - began
            after = connection.execute(text("SHOW lock_timeout")).scalar_one()
    # 표마다 LOCK 전에 한 번, 짧은 대기에 진 weather_values만 그 직후에 한 번 더 묻는다.
    assert [(table, mode) for table, mode, _ in seen] == [
        ("weather_source_records", "SHARE ROW EXCLUSIVE"),
        ("weather_locations", "SHARE ROW EXCLUSIVE"),
        ("weather_values", "ACCESS EXCLUSIVE"),
        ("weather_values", "ACCESS EXCLUSIVE"),
        ("weather_current_values", "ACCESS EXCLUSIVE"),
    ]
    assert 1.0 <= waited < 3.0, waited
    # 긴 대기는 그 한 lock에만 쓰고 짧은 상한으로 되돌린다.
    assert after == f"{BRIEF_DDL_LOCK_TIMEOUT_MS}ms"


@pytest.mark.parametrize("autovacuum", [True, False])
def test_readers_queued_behind_a_brief_wait_do_not_decide_the_blocker(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch, autovacuum: bool
) -> None:
    """L3: 짧은 대기 동안 뒤에 줄 선 reader가 autovacuum 판정을 뒤집지 않는다.

    부모에 거는 LOCK은 부모를 먼저 잡고, autovacuum이 쥔 오늘 파티션에서 기다린다. 그동안 온
    reader는 부모에서 줄을 서고, savepoint로 되돌리는 순간 lock을 받아 짧은 대기 직후의 보유자에
    섞인다. 대기 직후의 보유자만 보던 판정은 그 reader 때문에 autovacuum만 막는 경우를 놓쳐,
    지도 읽기가 도는 밤에는 detach·미래 파티션 생성이 autovacuum을 끝내 이기지 못했다. 그
    reader는 LOCK 전에 없던 transaction이라 세지 않는다. LOCK 전부터 같은 파티션을 쥔
    client(``autovacuum=False``)는 reader가 섞여도 차단자이므로 짧은 상한에 물러난다.
    """
    from sqlalchemy.exc import OperationalError

    today = partition_name(kst_now().date())
    with (
        # 대역 세션: autovacuum처럼 오늘 파티션만 쥐고, deadlock 검사가 취소한 것처럼 놓는다.
        _Holder(
            repository,
            f"LOCK TABLE {today} IN SHARE UPDATE EXCLUSIVE MODE",
            release_after=1.2,
        ) as stand_in,
        _ReaderProbe(repository, hold=0.05) as reader,
    ):
        seen = _relabel_as_autovacuum(monkeypatch, stand_in.pid if autovacuum else None)
        time.sleep(0.1)
        began = time.monotonic()
        outcome = "locked"
        try:
            with repository.engine.begin() as connection:
                lock_for_partition_ddl(connection, drop_foreign_keys=True, brief=True)
        except OperationalError as exc:
            assert is_lock_conflict(exc)
            outcome = "gave way"
        waited = time.monotonic() - began
    assert reader.errors == [] and reader.reads > 0
    values = [holders for table, _, holders in seen if table == VALUES_TABLE]
    assert len(values) == 2, seen  # LOCK 전, 짧은 대기에 진 직후
    before, after = values
    held_before = {holder.transaction for holder in before}
    # 뒤에 줄 섰던 reader가 실제로 대기 직후의 보유자에 섞였다(예전 판정이 연기하던 상황).
    assert any(
        holder.kind == "client backend" and holder.transaction not in held_before
        for holder in after
    ), (before, after)
    if autovacuum:
        assert outcome == "locked" and waited < 3.0, (outcome, waited)
    else:
        assert outcome == "gave way" and waited < 1.5, (outcome, waited)


def test_blocking_holder_kinds_ignore_sessions_that_arrived_during_the_wait() -> None:
    """차단자는 LOCK 전부터 쥐고 있던 transaction과 autovacuum worker다(DB 없이)."""
    vacuum = LockHolder("5/1", 10, AUTOVACUUM_BACKEND)
    ingest = LockHolder("6/7", 11, "client backend")
    queued_reader = LockHolder("7/9", 12, "client backend")
    # 같은 연결(pid)의 다음 transaction: 대기 전 transaction이 끝나고 새로 줄을 섰다.
    ingest_next = LockHolder("6/8", 11, "client backend")
    late_vacuum = LockHolder("8/1", 13, AUTOVACUUM_BACKEND)
    assert blocking_holder_kinds([vacuum], [vacuum, queued_reader]) == {AUTOVACUUM_BACKEND}
    assert blocking_holder_kinds([vacuum, ingest], [vacuum, ingest]) == {
        AUTOVACUUM_BACKEND, "client backend",
    }
    assert blocking_holder_kinds([ingest], [ingest_next, late_vacuum]) == {AUTOVACUUM_BACKEND}
    assert blocking_holder_kinds([vacuum], [queued_reader]) == set()


def test_lock_holder_kinds_follow_the_conflict_table_and_the_partition_tree(
    repository: WeatherRepository,
) -> None:
    """차단자 판정 SQL: 충돌하지 않는 보유자는 빼고, 파티션 하나만 잡은 보유자도 부모의 차단자다."""
    today = partition_name(kst_now().date())

    def kinds(connection, table: str, mode: str) -> set[str]:
        return {holder.kind for holder in conflicting_lock_holders(connection, table, mode)}

    with _LocationReader(repository), repository.engine.connect() as connection:
        # 읽기(ACCESS SHARE)는 SHARE ROW EXCLUSIVE를 막지 않고 ACCESS EXCLUSIVE는 막는다.
        assert "client backend" not in kinds(
            connection, "weather_locations", "SHARE ROW EXCLUSIVE"
        )
        assert "client backend" in kinds(connection, "weather_locations", "ACCESS EXCLUSIVE")
    # autovacuum처럼 파티션 하나만 SHARE UPDATE EXCLUSIVE로 잡아도 부모 lock의 차단자로 보인다.
    with (
        _Holder(repository, f"LOCK TABLE {today} IN SHARE UPDATE EXCLUSIVE MODE") as holder,
        repository.engine.connect() as connection,
    ):
        holders = conflicting_lock_holders(connection, "weather_values", "ACCESS EXCLUSIVE")
        assert (holder.pid, "client backend") in {(h.pid, h.kind) for h in holders}
        assert "client backend" not in kinds(
            connection, "weather_source_records", "ACCESS EXCLUSIVE"
        )
        # pg_stat_activity는 transaction 안에서 처음 읽은 snapshot에 고정된다. 판정은 매번 그
        # snapshot을 버리므로, 첫 물음 뒤에 연결한 세션(새 pid)의 종류도 안다.
        late_engine = create_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            with late_engine.begin() as late:
                late.execute(text("LOCK TABLE weather_source_records IN ROW EXCLUSIVE MODE"))
                late_pid = int(late.execute(text("SELECT pg_backend_pid()")).scalar_one())
                holders = conflicting_lock_holders(
                    connection, "weather_source_records", "ACCESS EXCLUSIVE"
                )
                assert (late_pid, "client backend") in {(h.pid, h.kind) for h in holders}
        finally:
            late_engine.dispose()


def test_the_first_attempt_also_waits_for_a_quiet_moment(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L3: 첫 시도도 조용한 순간을 기다린다. 예전에는 첫 시도만 눈먼 채 수집 뒤에 줄을 섰다."""
    monkeypatch.setattr(repository_module, "PARTITION_DDL_RETRY_SECONDS", 5.0)

    def timed():
        began = time.monotonic()
        calls: list[float] = []

        def step(connection) -> bool:
            calls.append(time.monotonic() - began)
            return True

        return calls, step

    writer = "LOCK TABLE weather_values IN ROW EXCLUSIVE MODE"
    with _Holder(repository, writer, release_after=1.0):
        calls, step = timed()
        assert repository._try_partition_ddl(step, breaker="forward") == (True, True)
    assert len(calls) == 1 and 0.8 <= calls[0] < 4.0, calls
    # 수집·reader와 충돌하지 않는 단계(floor 확인·VALIDATE)는 첫 시도 앞에서 기다리지 않는다.
    with _Holder(repository, writer, release_after=1.0):
        calls, step = timed()
        assert repository._try_partition_ddl(
            step, breaker="forward", quiet_first=False
        ) == (True, True)
    assert len(calls) == 1 and calls[0] < 0.5, calls


def test_map_reads_wait_behind_a_lost_ddl_attempt_for_the_brief_wait_only(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L3 수용 지표의 로컬 재현: DDL 시도가 수집에 지는 동안 지도 reader가 기다린 가장 긴 시간.

    ACCESS EXCLUSIVE 요청이 대기열에 있으면 뒤에 온 ACCESS SHARE도 그 뒤에 선다. 기본
    상한(``ddl_lock_timeout_ms``, 3초)이면 reader가 약 3초, 짧은 상한이면 약 0.4초 멈춘다.
    """
    monkeypatch.setattr(repository_module, "PARTITION_DDL_ATTEMPTS", 1)
    monkeypatch.setattr(repository_module, "PARTITION_DDL_RETRY_SECONDS", 0.1)
    stalls: dict[bool, float] = {}
    for brief in (True, False):
        with _Holder(repository, "LOCK TABLE weather_values IN ROW EXCLUSIVE MODE"):
            with _ReaderProbe(repository) as reader:
                time.sleep(0.2)
                done, _ = repository._try_partition_ddl(
                    lambda connection, brief=brief: lock_for_partition_ddl(
                        connection, brief=brief
                    ),
                    breaker="forward",
                )
            assert done is False
            assert reader.errors == [] and reader.reads > 0
            stalls[brief] = reader.longest
    assert stalls[True] < 1.5, stalls
    assert stalls[False] >= 2.0, stalls
