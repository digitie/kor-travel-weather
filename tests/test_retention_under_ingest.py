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

    def lost_race(connection):
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
