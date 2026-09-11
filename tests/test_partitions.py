"""``weather_values``의 파티션 관리."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from kortravelweather.partitions import (
    DEFAULT_PARTITION,
    default_partition_rows,
    drop_partitions_before,
    ensure_default_partition,
    ensure_partitions,
    existing_partitions,
    partition_name,
)
from kortravelweather.repository import WeatherRepository

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)


def _repository() -> WeatherRepository:
    repository = WeatherRepository(TEST_DATABASE_URL)
    repository.create_schema()
    return repository


def test_the_fact_table_is_partitioned_by_day() -> None:
    repository = _repository()
    with repository.engine.connect() as connection:
        kind = connection.execute(
            text("SELECT relkind FROM pg_class WHERE relname = 'weather_values'")
        ).scalar_one()
        strategy = connection.execute(
            text(
                "SELECT pg_get_partkeydef(c.oid) FROM pg_class c "
                "WHERE c.relname = 'weather_values'"
            )
        ).scalar_one()
    assert kind == "p", "weather_values is a plain table, so nothing can be dropped"
    assert "known_at" in strategy


def test_ensure_partitions_is_idempotent_and_covers_the_range() -> None:
    """The nightly job runs it every night over an overlapping window."""
    repository = _repository()
    start = date(2031, 4, 1)
    end = date(2031, 4, 3)
    with repository.engine.begin() as connection:
        first = ensure_partitions(connection, start=start, end=end)
        second = ensure_partitions(connection, start=start, end=end)
        names = {name for name, _ in existing_partitions(connection)}
    assert first == second
    assert len(first) == 3
    assert {partition_name(start + timedelta(days=i)) for i in range(3)} <= names


def test_a_backwards_range_is_refused_rather_than_silently_empty() -> None:
    """A swapped pair would create nothing and report success."""
    repository = _repository()
    with repository.engine.begin() as connection, pytest.raises(ValueError):
        ensure_partitions(connection, start=date(2031, 4, 3), end=date(2031, 4, 1))


def test_only_days_entirely_before_the_cutoff_are_dropped() -> None:
    """The day the cutoff falls in is kept whole.

    A partition covers a whole day, so dropping the cutoff's own day would
    discard the hours on the near side of it -- data that is inside the
    retention window.
    """
    repository = _repository()
    days = [date(2031, 5, day) for day in (1, 2, 3)]
    with repository.engine.begin() as connection:
        ensure_partitions(connection, start=days[0], end=days[-1])
        dropped = drop_partitions_before(connection, date(2031, 5, 3))
        remaining = {name for name, _ in existing_partitions(connection)}
    # Partitions outlive a test (the fixture truncates rows, not tables), so
    # assert about these three days rather than the whole set.
    assert {partition_name(days[0]), partition_name(days[1])} <= set(dropped)
    assert partition_name(days[2]) not in dropped, "the cutoff's own day was dropped"
    assert partition_name(days[2]) in remaining


def test_a_partitions_boundary_is_anchored_to_kst_midnight() -> None:
    """A bare date literal is cast in the session's timezone, not KST.

    ``weather_values_20260911`` must cover the KST calendar day of the 11th --
    ``[2026-09-10 15:00 UTC, 2026-09-11 15:00 UTC)`` -- not UTC midnight to
    UTC midnight, which is nine hours earlier and starts on the wrong side of
    the KST day. A row collected between 00:00 and 09:00 KST used to land in
    the previous day's UTC-anchored partition, silently misfiling until
    retention dropped -- or failed to drop -- the wrong day.
    """
    repository = _repository()
    day = date(2031, 6, 1)
    with repository.engine.begin() as connection:
        ensure_partitions(connection, start=day, end=day)
        bound = connection.execute(
            text(
                "SELECT pg_get_expr(c.relpartbound, c.oid) FROM pg_class c "
                "WHERE c.relname = :name"
            ),
            {"name": partition_name(day)},
        ).scalar_one()
    assert "2031-05-31 15:00:00+00" in bound, (
        f"lower bound is not KST midnight on {day}: {bound}"
    )
    assert "2031-06-01 15:00:00+00" in bound, (
        f"upper bound is not KST midnight on {day + timedelta(days=1)}: {bound}"
    )


def test_existing_partitions_reads_back_the_kst_day_it_was_created_for() -> None:
    """The round trip must agree with ``partition_name``, not PostgreSQL's echo.

    PostgreSQL reports a partition's bound back in the connection's own
    timezone, so a KST-midnight boundary can be echoed with a UTC timestamp
    that starts on the previous calendar day. Reading that string's leading
    date naively would disagree with the name the partition was created
    under -- exactly the mismatch that let a day fail to be dropped, or drop
    the wrong one.
    """
    repository = _repository()
    day = date(2031, 6, 1)
    with repository.engine.begin() as connection:
        ensure_partitions(connection, start=day, end=day)
        parsed = dict(existing_partitions(connection))
    assert parsed[partition_name(day)] == day


def test_the_default_partition_is_never_dropped() -> None:
    """It has no bound, so "before the cutoff" cannot be true of it.

    Dropping it would take the stranded rows with it, silently -- and those
    rows are the only evidence that a partition was missing.
    """
    repository = _repository()
    with repository.engine.begin() as connection:
        ensure_default_partition(connection)
        drop_partitions_before(connection, date(2099, 1, 1))
        names = {name for name, _ in existing_partitions(connection)}
    assert DEFAULT_PARTITION in names


def test_default_partition_rows_counts_only_the_stranded_ones() -> None:
    """``count(*)`` on the parent would return every row in the table."""
    repository = _repository()
    with repository.engine.connect() as connection:
        assert default_partition_rows(connection) == 0
