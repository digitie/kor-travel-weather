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
