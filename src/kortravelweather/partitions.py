"""``weather_values``의 일별 파티션 관리.

Why the fact table is partitioned at all: retention by ``DELETE`` writes a
tombstone and a WAL record for every row and then needs a vacuum to give the
space back.  At roughly three million facts a day on a disk that sustains about
160 random reads a second, that is hours of work every night to remove data
nobody wants.  Dropping a partition is a catalog change -- no scan, no WAL for
the rows, no vacuum, and the space is returned at once.

The partition key is ``known_at``: it is the axis retention is expressed in,
the projection already carries it, and it is never null in practice or in
principle (the insert path falls back to ``collected_at``, which has a default).

Three callers share this module rather than each spelling the DDL out --
``create_schema`` for development and tests, the revision that converts the
table, and the nightly maintenance job.  A partition that exists in one of those
and not the others is an insert that fails at midnight.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import Connection, text

VALUES_TABLE = "weather_values"
#: Everything outside the declared ranges lands here.  It should stay empty; it
#: exists so that a missing partition degrades to "collected but awkward to
#: drop" instead of "ingestion stops".  ``report_default_partition_rows`` is how
#: anyone finds out it did not stay empty.
DEFAULT_PARTITION = f"{VALUES_TABLE}_default"


def partition_name(day: date) -> str:
    return f"{VALUES_TABLE}_{day:%Y%m%d}"


def ensure_default_partition(connection: Connection) -> None:
    connection.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {DEFAULT_PARTITION} "
            f"PARTITION OF {VALUES_TABLE} DEFAULT"
        )
    )


def ensure_partitions(connection: Connection, *, start: date, end: date) -> list[str]:
    """Create the daily partitions covering ``[start, end]`` inclusive.

    Idempotent, so the maintenance job can run it every night and the migration
    can run it over whatever range the existing data occupies.
    """
    if end < start:
        raise ValueError("end는 start 이후여야 합니다.")
    created: list[str] = []
    day = start
    while day <= end:
        name = partition_name(day)
        # ``IF NOT EXISTS`` is not available for ATTACH-style partition
        # creation in every supported server, but it is for CREATE TABLE ...
        # PARTITION OF, which is what this is.
        connection.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {VALUES_TABLE} "
                f"FOR VALUES FROM ('{day:%Y-%m-%d}') TO ('{day + timedelta(days=1):%Y-%m-%d}')"
            )
        )
        created.append(name)
        day += timedelta(days=1)
    return created


def existing_partitions(connection: Connection) -> list[tuple[str, date | None]]:
    """Return ``(name, first day covered)`` for each partition, oldest first.

    The DEFAULT partition has no bound and sorts last with ``None``.
    """
    rows = connection.execute(
        text(
            "SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) "
            "FROM pg_class c "
            "JOIN pg_inherits i ON i.inhrelid = c.oid "
            "JOIN pg_class p ON p.oid = i.inhparent "
            "WHERE p.relname = :parent"
        ),
        {"parent": VALUES_TABLE},
    ).all()
    parsed: list[tuple[str, date | None]] = []
    for name, bound in rows:
        parsed.append((name, _lower_bound(bound)))
    parsed.sort(key=lambda item: (item[1] is None, item[1]))
    return parsed


def _lower_bound(bound: str | None) -> date | None:
    """Read the first day a partition covers out of its bound expression."""
    if not bound or "FROM (" not in bound:
        return None
    fragment = bound.split("FROM (", 1)[1].split(")", 1)[0]
    literal = fragment.strip().strip("'").strip()
    try:
        return date.fromisoformat(literal[:10])
    except ValueError:
        return None


def drop_partitions_before(connection: Connection, cutoff: date) -> list[str]:
    """Drop every daily partition whose whole range precedes ``cutoff``.

    A partition is dropped only when its *last* day is also before the cutoff,
    so the day the cutoff falls in is kept whole.  Callers must have removed the
    projection rows pointing into it first, or the detach is refused -- which is
    the behaviour we want: a fact must never vanish from under a pointer.

    The detach takes a brief ``ACCESS EXCLUSIVE`` on the parent.  It is a
    catalog change, so it is held for milliseconds, but waiting for it is not
    bounded -- which is why the maintenance job runs at a quiet hour and after
    the ingest schedules.
    """
    dropped: list[str] = []
    for name, lower in existing_partitions(connection):
        if lower is None or lower + timedelta(days=1) > cutoff:
            continue
        # DROP alone is refused while a foreign key references the parent:
        # the constraint depends on every partition, whether or not any row
        # points into this one.  Detaching first turns it into an ordinary
        # table, and the detach itself checks that nothing still references it
        # -- which is the guard we want, and why the caller clears the
        # projection's pointers before getting here.
        connection.execute(text(f"ALTER TABLE {VALUES_TABLE} DETACH PARTITION {name}"))
        connection.execute(text(f"DROP TABLE {name}"))
        dropped.append(name)
    return dropped


def default_partition_rows(connection: Connection) -> int:
    """How many rows fell outside every declared range.

    Non-zero means a partition was missing when something inserted, and those
    rows will never be dropped by retention because they are not in a dated
    partition.  Silent growth is exactly what partitioning was meant to end.
    """
    return int(
        connection.execute(
            text(f"SELECT count(*) FROM ONLY {DEFAULT_PARTITION}")
        ).scalar_one()
    )
