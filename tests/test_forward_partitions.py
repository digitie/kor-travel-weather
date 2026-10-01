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
    DEFAULT_PARTITION,
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
    # Idempotent: a second run deletes nothing more.
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
