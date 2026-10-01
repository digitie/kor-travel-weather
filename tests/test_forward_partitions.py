"""Forward partitions on a fresh and on a populated database, without a DEFAULT scan.

Production's fresh database (2026-09-20) never had a dated partition: revision
0015 created partitions only for days that already had rows, and the nightly job
could not create one once DEFAULT held rows for that day -- the check is a full
scan of DEFAULT under ACCESS EXCLUSIVE, and on 2026-09-30 it deadlocked an
ingest instead.  Every fact went to DEFAULT (36 GB), every insert paid random
reads into indexes far larger than memory, and retention could drop nothing.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from alembic import command
from kortravelweather import repository as repository_module
from kortravelweather.default_partition import establish_floor
from kortravelweather.models import kst_now
from kortravelweather.partitions import (
    DEFAULT_FLOOR_CONSTRAINT,
    DEFAULT_PARTITION,
    add_default_floor,
    default_partition_floor,
    ensure_forward_partitions,
    existing_partitions,
    is_lock_conflict,
    kst_midnight,
    lock_for_partition_ddl,
    partition_name,
)
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import get_settings

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)


def _fresh_database(monkeypatch: pytest.MonkeyPatch, revision: str) -> WeatherRepository:
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()
    repository = WeatherRepository(TEST_DATABASE_URL)
    with repository.engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA public CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA public")
    command.upgrade(Config("alembic.ini"), revision)
    return repository


def _names(repository: WeatherRepository) -> set[str]:
    with repository.engine.connect() as connection:
        return {name for name, _ in existing_partitions(connection)}


def _stage_default_row(repository: WeatherRepository, known_at: datetime) -> None:
    """One fact in DEFAULT, the way production's 35M got there."""
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO weather_locations (location_id, name, latitude, longitude, "
                "created_at, updated_at) VALUES ('fp', 'fp', 37, 127, now(), now()) "
                "ON CONFLICT DO NOTHING"
            )
        )
        connection.execute(
            text(
                "INSERT INTO weather_source_records (source_record_key, provider, "
                "dataset_key, source_entity_type, source_entity_id, raw_payload_hash, "
                "payload, fetched_at, imported_at) VALUES (:key, 'p', 'd', "
                "'weather_response', 'fp', :key, '{}', :at, :at)"
            ),
            {"key": f"fp-{known_at.isoformat()}", "at": known_at},
        )
        connection.execute(
            text(
                "INSERT INTO weather_values (value_id, location_id, provider, dataset_key, "
                "weather_domain, forecast_style, metric_key, target_at, known_at, "
                "normalization_version, payload, collected_at, source_record_key, "
                "value_number) VALUES (:key, 'fp', 'p', 'd', 'weather', 'short', 'TMP', "
                ":at, :at, 'test', '{}', :at, :key, 1)"
            ),
            {"key": f"fp-{known_at.isoformat()}", "at": known_at},
        )
        assert connection.execute(
            text(f"SELECT count(*) FROM ONLY {DEFAULT_PARTITION}")
        ).scalar_one() >= 1


def test_migration_on_an_empty_database_creates_the_forward_window(monkeypatch) -> None:
    repository = _fresh_database(monkeypatch, "head")
    today = kst_now().date()

    names = _names(repository)
    for offset in range(-1, 8):
        assert partition_name(today + timedelta(days=offset)) in names, offset
    with repository.engine.connect() as connection:
        floor = default_partition_floor(connection)
    assert floor == kst_midnight(today - timedelta(days=1))


def test_migration_leaves_a_populated_default_alone_and_the_script_takes_over(
    monkeypatch,
) -> None:
    # Production's shape: 0015 ran on an empty table, then DEFAULT filled up.
    repository = _fresh_database(monkeypatch, "0016_purge_lookup_indexes")
    now = kst_now()
    _stage_default_row(repository, now)
    command.upgrade(Config("alembic.ini"), "head")

    today = now.date()
    assert partition_name(today + timedelta(days=1)) not in _names(repository)
    with repository.engine.connect() as connection:
        assert default_partition_floor(connection) is None

    # The nightly job must not scan it either: it reports and moves on.
    report = repository.purge_expired_history(retention_days=2, ahead_days=3)
    assert report.default_floor is None
    assert report.partitions_created == ()

    lines: list[str] = []
    created = establish_floor(
        repository.engine,
        floor_day=today + timedelta(days=1),
        ahead_days=3,
        log=lines.append,
        force=True,
    )
    assert created == [partition_name(today + timedelta(days=n)) for n in (1, 2, 3)]
    with repository.engine.connect() as connection:
        assert default_partition_floor(connection) == kst_midnight(today + timedelta(days=1))
    # Today's rows stay where they are; nothing was moved or deleted.
    with repository.engine.connect() as connection:
        assert connection.execute(
            text(f"SELECT count(*) FROM ONLY {DEFAULT_PARTITION}")
        ).scalar_one() == 1
    # Idempotent: a second run creates nothing and keeps the floor.
    assert establish_floor(
        repository.engine,
        floor_day=today + timedelta(days=5),
        ahead_days=3,
        log=lines.append,
        force=True,
    ) == []
    assert any("reused" in line for line in lines)

    # From here the nightly job extends the window on its own.
    report = repository.purge_expired_history(retention_days=2, ahead_days=5)
    assert report.partitions_created == tuple(
        partition_name(today + timedelta(days=n)) for n in (4, 5)
    )
    command.upgrade(Config("alembic.ini"), "head")


def test_a_floor_that_does_not_hold_is_removed_again(monkeypatch) -> None:
    repository = _fresh_database(monkeypatch, "0016_purge_lookup_indexes")
    today = kst_now().date()
    # A row dated after the floor: validation must fail and leave no trace.
    _stage_default_row(repository, kst_midnight(today + timedelta(days=2)))
    with pytest.raises(Exception, match="violated|check"):
        establish_floor(
            repository.engine,
            floor_day=today + timedelta(days=1),
            ahead_days=3,
            log=lambda _: None,
            force=True,
        )
    with repository.engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM pg_constraint WHERE conrelid = to_regclass(:t) "
                "AND contype = 'c' AND conname LIKE '%floor%'"
            ),
            {"t": DEFAULT_PARTITION},
        ).scalar_one() == 0
    command.upgrade(Config("alembic.ini"), "head")


def test_forward_partitions_are_created_without_scanning_default(monkeypatch) -> None:
    """PostgreSQL says so itself at DEBUG1 when the floor proves DEFAULT empty."""
    repository = _fresh_database(monkeypatch, "head")
    _stage_default_row(repository, kst_now() - timedelta(days=30))
    day = kst_now().date() + timedelta(days=20)
    messages: list[str] = []
    with repository.engine.begin() as connection:
        raw = connection.connection.driver_connection
        raw.add_notice_handler(lambda diag: messages.append(diag.message_primary or ""))
        connection.execute(text("SET LOCAL client_min_messages = debug1"))
        floor = default_partition_floor(connection)
        assert floor is not None
        lock_for_partition_ddl(connection)
        created = ensure_forward_partitions(connection, floor=floor, start=day, end=day)
    assert created == [partition_name(day)]
    assert any(
        DEFAULT_PARTITION in message and "implied by existing constraints" in message
        for message in messages
    ), messages


def test_partition_ddl_gives_way_to_an_ingest_instead_of_deadlocking(monkeypatch) -> None:
    """A writer holding the projection must not be the deadlock victim.

    On 2026-09-30 the retention run created a partition, then waited for
    ``weather_current_values`` while an ingest that held it waited for the new
    partition.  The DDL now takes its locks first, with a timeout below the
    deadlock timeout, and retries -- so it is the DDL that backs off.
    """
    repository = _fresh_database(monkeypatch, "head")
    monkeypatch.setattr(repository_module, "PARTITION_DDL_ATTEMPTS", 2)
    monkeypatch.setattr(repository_module, "PARTITION_DDL_RETRY_SECONDS", 0.05)
    day = kst_now().date() + timedelta(days=40)

    def create(connection):
        return repository._ensure_forward_window(connection, start=day, end=day)

    writer = repository.engine.connect()
    writer_tx = writer.begin()
    writer.execute(text("LOCK TABLE weather_current_values IN ROW EXCLUSIVE MODE"))
    try:
        with pytest.raises(OperationalError) as caught:
            repository._partition_ddl(create)
        assert is_lock_conflict(caught.value)
        # The writer's transaction is untouched and still commits.
        writer.execute(text("SELECT 1"))
    finally:
        writer_tx.commit()
        writer.close()

    created, _ = repository._partition_ddl(create)
    assert created == [partition_name(day)]


def _stage_pointer(repository: WeatherRepository, known_at: datetime) -> None:
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO weather_current_values (value_id, location_id, provider, "
                "dataset_key, weather_domain, forecast_style, metric_key, target_at, "
                "known_at, source_record_key) SELECT value_id, location_id, provider, "
                "dataset_key, weather_domain, forecast_style, metric_key, target_at, "
                "known_at, source_record_key FROM weather_values WHERE known_at = :at"
            ),
            {"at": known_at},
        )


def _default_rows(repository: WeatherRepository) -> int:
    with repository.engine.connect() as connection:
        return connection.execute(
            text(f"SELECT count(*) FROM ONLY {DEFAULT_PARTITION}")
        ).scalar_one()


def test_purge_deletes_only_expired_rows_in_batches_while_default_stays(monkeypatch) -> None:
    from kortravelweather.default_partition import purge_execute, purge_report

    repository = _fresh_database(monkeypatch, "0016_purge_lookup_indexes")
    now = kst_now()
    old = now - timedelta(days=30)
    _stage_default_row(repository, old)
    _stage_default_row(repository, old + timedelta(hours=1))
    _stage_default_row(repository, now)
    _stage_pointer(repository, old)
    command.upgrade(Config("alembic.ini"), "head")
    establish_floor(
        repository.engine,
        floor_day=now.date() + timedelta(days=1),
        ahead_days=2,
        log=lambda _: None,
        force=True,
    )

    _analyze(repository)
    lines: list[str] = []
    report = purge_report(repository.engine, retention_days=2, log=lines.append, exact=True)
    assert report["expired_rows"] == 2
    assert report["pointers"] == 1
    assert report["whole_partition_expired"] is False
    assert _default_rows(repository) == 3, "the dry run changed something"

    purge_execute(
        repository.engine,
        retention_days=2,
        log=lines.append,
        batch_blocks=1,
        pause_seconds=0,
    )
    assert _default_rows(repository) == 1
    with repository.engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM weather_current_values")
        ).scalar_one() == 0
        assert default_partition_floor(connection) is not None
    # A second run finds nothing expired and refuses rather than re-reading
    # the whole partition to delete nothing.
    with pytest.raises(SystemExit, match="no row past retention"):
        purge_execute(repository.engine, retention_days=2, log=lines.append, pause_seconds=0)
    assert _default_rows(repository) == 1
    assert any("resume with --start-block" in line for line in lines)


def test_purge_swaps_out_a_default_that_is_entirely_past_retention(monkeypatch) -> None:
    from kortravelweather.default_partition import purge_execute

    repository = _fresh_database(monkeypatch, "0016_purge_lookup_indexes")
    today = kst_now().date()
    old = kst_now() - timedelta(days=30)
    _stage_default_row(repository, old)
    _stage_pointer(repository, old)
    command.upgrade(Config("alembic.ini"), "head")
    establish_floor(
        repository.engine,
        floor_day=today - timedelta(days=10),
        ahead_days=2,
        log=lambda _: None,
        force=True,
    )
    before = _names(repository)

    purge_execute(repository.engine, retention_days=2, log=lambda _: None)

    assert _default_rows(repository) == 0
    assert _names(repository) == before, "dated partitions must be untouched"
    with repository.engine.connect() as connection:
        assert default_partition_floor(connection) == kst_midnight(today - timedelta(days=10))
        assert connection.execute(
            text("SELECT count(*) FROM weather_current_values")
        ).scalar_one() == 0
    # The new DEFAULT still refuses edits outside a purge.
    _stage_default_row(repository, old + timedelta(hours=2))
    with repository.engine.begin() as connection, pytest.raises(Exception, match="immutable"):
        connection.execute(text(f"DELETE FROM {DEFAULT_PARTITION}"))
    command.upgrade(Config("alembic.ini"), "head")


def _analyze(repository: WeatherRepository) -> None:
    with repository.engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        connection.execute(text(f"ANALYZE {DEFAULT_PARTITION}"))


def _floor_constraints(repository: WeatherRepository) -> int:
    with repository.engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT count(*) FROM pg_constraint WHERE conrelid = to_regclass(:t) "
                "AND conname = :n"
            ),
            {"t": DEFAULT_PARTITION, "n": DEFAULT_FLOOR_CONSTRAINT},
        ).scalar_one()


def _populated_default(monkeypatch) -> WeatherRepository:
    repository = _fresh_database(monkeypatch, "0016_purge_lookup_indexes")
    _stage_default_row(repository, kst_now())
    command.upgrade(Config("alembic.ini"), "head")
    return repository


def test_a_failure_after_validation_leaves_no_floor_behind(monkeypatch) -> None:
    """H3: a validated floor must imply its partitions exist.

    The partitions are created in the validating transaction, so a failure
    there rolls the validation back, and the unvalidated floor left by step 1
    is removed -- whatever the failure was.
    """
    import kortravelweather.default_partition as operator_steps

    repository = _populated_default(monkeypatch)

    def broken(*_args, **_kwargs):
        raise RuntimeError("connection lost")

    monkeypatch.setattr(operator_steps, "ensure_forward_partitions", broken)
    with pytest.raises(RuntimeError, match="connection lost"):
        establish_floor(
            repository.engine,
            floor_day=kst_now().date() + timedelta(days=1),
            ahead_days=3,
            log=lambda _: None,
            force=True,
        )
    assert _floor_constraints(repository) == 0
    assert partition_name(kst_now().date() + timedelta(days=1)) not in _names(repository)
    command.upgrade(Config("alembic.ini"), "head")


def test_a_validated_floor_always_has_its_partitions(monkeypatch) -> None:
    repository = _populated_default(monkeypatch)
    today = kst_now().date()
    establish_floor(
        repository.engine,
        floor_day=today + timedelta(days=1),
        ahead_days=4,
        log=lambda _: None,
        force=True,
    )
    with repository.engine.connect() as connection:
        floor = default_partition_floor(connection)
    assert floor == kst_midnight(today + timedelta(days=1))
    names = _names(repository)
    for offset in range(1, 5):
        assert partition_name(today + timedelta(days=offset)) in names, offset
    command.upgrade(Config("alembic.ini"), "head")


def test_a_leftover_unvalidated_floor_is_removed_by_the_next_run_and_the_nightly_job(
    monkeypatch,
) -> None:
    """An interrupted script (kill -9, lost connection) can leave step 1 behind.

    Left alone it would refuse every fact from its date on, with no partition
    to take them.  Both the next script run and the nightly job remove it.
    """
    repository = _populated_default(monkeypatch)
    today = kst_now().date()
    with repository.engine.begin() as connection:
        add_default_floor(connection, kst_midnight(today + timedelta(days=1)))
    assert _floor_constraints(repository) == 1

    report = repository.purge_expired_history(retention_days=2, ahead_days=3)
    assert report.default_floor is None
    assert _floor_constraints(repository) == 0, "the nightly job kept a leftover floor"

    with repository.engine.begin() as connection:
        add_default_floor(connection, kst_midnight(today + timedelta(days=1)))
    lines: list[str] = []
    establish_floor(
        repository.engine,
        floor_day=today + timedelta(days=2),
        ahead_days=3,
        log=lines.append,
        force=True,
    )
    assert any("interrupted run" in line for line in lines)
    with repository.engine.connect() as connection:
        assert default_partition_floor(connection) == kst_midnight(today + timedelta(days=2))
    command.upgrade(Config("alembic.ini"), "head")


def test_a_rerun_rechecks_the_time_guard(monkeypatch) -> None:
    repository = _populated_default(monkeypatch)
    today = kst_now().date()
    with repository.engine.begin() as connection:
        add_default_floor(connection, kst_midnight(today + timedelta(days=1)))
    # A floor less than three hours away is refused, leftover or not.
    with pytest.raises(SystemExit):
        establish_floor(repository.engine, floor_day=today, ahead_days=3, log=lambda _: None)
    assert _floor_constraints(repository) == 0
    command.upgrade(Config("alembic.ini"), "head")


def test_the_lock_wait_outlasts_the_deadlock_check(monkeypatch) -> None:
    """H2: autovacuum yields only to a waiter that reaches its deadlock check.

    A session holding SHARE UPDATE EXCLUSIVE on DEFAULT -- what an autovacuum
    holds -- for longer than ``deadlock_timeout``: the floor step must still
    get through on its first attempt.
    """
    import threading
    import time as clock

    import kortravelweather.default_partition as operator_steps
    from kortravelweather.partitions import ddl_lock_timeout_ms

    repository = _populated_default(monkeypatch)
    with repository.engine.connect() as connection:
        deadlock_ms = connection.execute(
            text("SELECT setting::int FROM pg_settings WHERE name = 'deadlock_timeout'")
        ).scalar_one()
        assert ddl_lock_timeout_ms(connection) > deadlock_ms
    monkeypatch.setattr(operator_steps, "LOCK_RETRY_ATTEMPTS", 1)
    held = threading.Event()

    def hold() -> None:
        with repository.engine.begin() as connection:
            connection.execute(
                text(f"LOCK TABLE {DEFAULT_PARTITION} IN SHARE UPDATE EXCLUSIVE MODE")
            )
            held.set()
            clock.sleep(deadlock_ms / 1000 + 0.5)

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(10)
    try:
        establish_floor(
            repository.engine,
            floor_day=kst_now().date() + timedelta(days=1),
            ahead_days=2,
            log=lambda _: None,
            force=True,
        )
    finally:
        holder.join()
    with repository.engine.connect() as connection:
        assert default_partition_floor(connection) is not None
    command.upgrade(Config("alembic.ini"), "head")


def test_dropping_a_partition_keeps_the_projection_foreign_key_valid(monkeypatch) -> None:
    """H1: the foreign key is dropped around the detach and validated after."""
    from kortravelweather.partitions import ensure_partitions

    repository = _fresh_database(monkeypatch, "head")
    old_day = kst_now().date() - timedelta(days=30)
    with repository.engine.begin() as connection:
        ensure_partitions(connection, start=old_day, end=old_day)
    repository.purge_expired_history(retention_days=2)
    assert partition_name(old_day) not in _names(repository)
    with repository.engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT conname, convalidated FROM pg_constraint WHERE contype = 'f' "
                "AND conrelid = 'weather_current_values'::regclass "
                "AND confrelid = 'weather_values'::regclass"
            )
        ).all()
    assert rows and all(valid for _, valid in rows), rows


def test_a_replayed_expired_source_is_skipped_not_fatal(monkeypatch) -> None:
    """M2: a replay keeps its first fetch time, which retention may have dropped.

    Its facts land between the floor and the oldest remaining partition, where
    no partition takes them and DEFAULT's floor refuses them; that used to fail
    the whole batch.  They are past retention, so they are skipped.
    """
    from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue
    from kortravelweather.partitions import drop_partitions_before

    repository = _fresh_database(monkeypatch, "head")
    today = kst_now().date()
    with repository.engine.begin() as connection:
        drop_partitions_before(connection, today)  # yesterday's partition is gone
    repository.upsert_location(
        WeatherLocation(location_id="m2", name="m2", latitude=37.5, longitude=127.0)
    )
    stale = kst_midnight(today) - timedelta(hours=12)
    fresh = kst_now()

    def record(key: str, at: datetime) -> dict:
        return {
            "source_record_key": key,
            "provider": "p",
            "dataset_key": "d",
            "source_entity_type": "weather_response",
            "source_entity_id": "m2",
            "payload": {"key": key},
            "fetched_at": at,
        }

    def value(key: str, at: datetime) -> WeatherValue:
        return WeatherValue(
            location_id="m2",
            provider="p",
            dataset_key="d",
            weather_domain="weather",
            forecast_style=ForecastStyle.SHORT,
            metric_key="TMP",
            target_at=at,
            known_at=at,
            value_number=1,
            source_record_key=key,
        )

    loaded = repository.ingest_batch(
        source_records=[record("stale", stale), record("fresh", fresh)],
        values=[value("stale", stale), value("fresh", fresh)],
    )
    assert loaded == 1
    with repository.engine.connect() as connection:
        keys = connection.execute(text("SELECT source_record_key FROM weather_values")).scalars()
        assert list(keys) == ["fresh"]


def test_the_forward_window_is_observable(monkeypatch) -> None:
    from kortravelweather.metrics import metrics_payload, observe_forward_partition_days

    repository = _fresh_database(monkeypatch, "head")
    assert repository.forward_partition_days() == 7
    report = repository.purge_expired_history(retention_days=2, ahead_days=7)
    assert report.forward_partition_days == 7
    observe_forward_partition_days(repository.forward_partition_days())
    assert b"ktw_forward_partition_days 7.0" in metrics_payload()


def test_the_batched_purge_refuses_when_nothing_has_expired(monkeypatch) -> None:
    from kortravelweather.default_partition import purge_execute

    repository = _populated_default(monkeypatch)
    establish_floor(
        repository.engine,
        floor_day=kst_now().date() + timedelta(days=1),
        ahead_days=2,
        log=lambda _: None,
        force=True,
    )
    _analyze(repository)
    with pytest.raises(SystemExit, match="no row past retention"):
        purge_execute(repository.engine, retention_days=2, log=lambda _: None)
    command.upgrade(Config("alembic.ini"), "head")
