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

from datetime import date, datetime, timedelta

from sqlalchemy import Connection, text

from .models import KST

VALUES_TABLE = "weather_values"
#: Everything outside the declared ranges lands here.  It should stay empty; it
#: exists so that a missing partition degrades to "collected but awkward to
#: drop" instead of "ingestion stops".  ``report_default_partition_rows`` is how
#: anyone finds out it did not stay empty.
DEFAULT_PARTITION = f"{VALUES_TABLE}_default"


def partition_name(day: date) -> str:
    return f"{VALUES_TABLE}_{day:%Y%m%d}"


def _kst_midnight(day: date) -> datetime:
    """A day boundary as an explicit KST instant, not a bare date.

    A bare ``'2026-09-11'`` literal is cast to ``timestamptz`` using the
    session's timezone -- UTC on this deployment -- so the boundary actually
    sat at UTC midnight, nine hours before the KST day it was named for
    started.  Any row collected between midnight and 09:00 KST (still the
    previous day in UTC) landed in the wrong partition, invisible until
    retention dropped -- or failed to drop -- the day it should have been in.
    Spelling out the offset anchors the boundary to KST regardless of what
    timezone the connection happens to default to.
    """
    return datetime(day.year, day.month, day.day, tzinfo=KST)


def _existing_partition_bounds(connection: Connection) -> list[tuple[datetime, datetime]]:
    """Every dated partition's ``(lower, upper)`` bound, in whatever timezone
    PostgreSQL happens to echo it back in. The DEFAULT partition has no bound
    and is skipped."""
    rows = connection.execute(
        text(
            "SELECT pg_get_expr(c.relpartbound, c.oid) "
            "FROM pg_class c "
            "JOIN pg_inherits i ON i.inhrelid = c.oid "
            "JOIN pg_class p ON p.oid = i.inhparent "
            "WHERE p.relname = :parent"
        ),
        {"parent": VALUES_TABLE},
    ).scalars().all()
    bounds: list[tuple[datetime, datetime]] = []
    for bound in rows:
        if not bound or "FROM (" not in bound or " TO (" not in bound:
            continue  # the DEFAULT partition has no FROM/TO to parse.
        try:
            lower_literal = bound.split("FROM (", 1)[1].split(")", 1)[0].strip().strip("'")
            upper_literal = bound.split(" TO (", 1)[1].split(")", 1)[0].strip().strip("'")
            bounds.append(
                (datetime.fromisoformat(lower_literal), datetime.fromisoformat(upper_literal))
            )
        except (ValueError, IndexError):
            continue
    return bounds


def _overlapping_upper_bound(
    bounds: list[tuple[datetime, datetime]], *, lower: datetime, upper: datetime
) -> datetime | None:
    """Among existing partitions, the upper bound of one that would overlap
    the candidate ``[lower, upper)`` range, or ``None`` if none does.

    Two ranges overlap exactly when each starts before the other ends.
    Scoping the check to genuine overlap -- not merely "some other partition
    ends later than this range starts" -- matters because a shared table can
    hold partitions for entirely unrelated periods (a historical backfill, a
    test fixture); those must never inflate a day nowhere near them.
    """
    overlapping = [
        existing_upper
        for existing_lower, existing_upper in bounds
        if existing_lower < upper and existing_upper > lower
    ]
    return max(overlapping) if overlapping else None


def ensure_default_partition(connection: Connection) -> None:
    connection.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {DEFAULT_PARTITION} "
            f"PARTITION OF {VALUES_TABLE} DEFAULT"
        )
    )


#: The DEFAULT partition's upper bound: ``CHECK (known_at < floor)``.
#:
#: Creating a partition while a DEFAULT partition exists makes PostgreSQL prove
#: that DEFAULT holds no row for the new range.  Without a constraint that
#: proves it, the proof is a full scan of DEFAULT under an ACCESS EXCLUSIVE
#: lock on it -- which, on the 36 GB DEFAULT the fresh 2026-09-20 database grew
#: because no dated partition was ever created, is every writer stopped for as
#: long as the disk takes to read it.  With this constraint validated, every day
#: at or after the floor is proven empty from the catalog alone and the
#: partition is created in milliseconds.
#:
#: The floor is set once, at the first forward day, and never moves: every day
#: from it onwards gets its own partition, so DEFAULT only ever receives rows
#: dated before it (a backfill into a day nobody partitioned).  The trade-off is
#: deliberate: a fact dated past the forward window is now refused instead of
#: landing in DEFAULT, where retention could never reach it.  The window is
#: ``retention_ahead_days`` long and the maintenance job extends it nightly.
DEFAULT_FLOOR_CONSTRAINT = f"{DEFAULT_PARTITION}_known_at_floor"

#: Partition DDL waits at most this long for each lock, then gives up and is
#: retried.  Below PostgreSQL's default ``deadlock_timeout`` (1 s) on purpose:
#: if the DDL ends up in a lock cycle with an ingest, it is the DDL that times
#: out first, never the ingest that the deadlock detector would otherwise kill.
DDL_LOCK_TIMEOUT_MS = 500


def kst_midnight(day: date) -> datetime:
    """Public name of the KST day boundary every partition bound uses."""
    return _kst_midnight(day)


def lock_for_partition_ddl(connection: Connection) -> None:
    """Take every lock partition DDL needs, in one fixed order, up front.

    ``CREATE TABLE ... PARTITION OF`` and ``DETACH PARTITION`` lock the parent
    and DEFAULT, and -- because ``weather_current_values`` has a foreign key
    into the fact table -- the referencing table too.  Taken lazily, mid
    statement, that last lock is what deadlocked the retention run of
    2026-09-30 against an ingest: the DDL held the new partition and waited for
    ``weather_current_values``; the ingest held ``weather_current_values`` and
    waited for the new partition.  Taking both before any DDL, each with a
    lock timeout below the deadlock timeout, means the DDL either holds
    everything before it starts or gives up without having held anything an
    ingest waits on for long.  The ACCESS EXCLUSIVE lock lasts only as long as
    the catalog change, which with a validated floor is milliseconds.
    """
    connection.execute(text(f"SET LOCAL lock_timeout = '{DDL_LOCK_TIMEOUT_MS}ms'"))
    has_projection = connection.execute(
        text("SELECT to_regclass('weather_current_values') IS NOT NULL")
    ).scalar_one()
    if has_projection:
        connection.execute(
            text("LOCK TABLE weather_current_values IN SHARE ROW EXCLUSIVE MODE")
        )
    connection.execute(text(f"LOCK TABLE {VALUES_TABLE} IN ACCESS EXCLUSIVE MODE"))


def is_lock_conflict(exc: BaseException) -> bool:
    """A lock timeout or a deadlock: the DDL lost a race and can retry."""
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    return sqlstate in {"55P03", "40P01"}


def default_partition_floor(connection: Connection) -> datetime | None:
    """The validated floor of DEFAULT, or ``None`` when there is none yet.

    A floor added ``NOT VALID`` and not yet validated proves nothing to the
    planner, so it counts as absent here.
    """
    row = connection.execute(
        text(
            "SELECT c.convalidated, "
            "  substring(pg_get_constraintdef(c.oid) from $re$'([^']+)'$re$)::timestamptz "
            "FROM pg_constraint c "
            "WHERE c.conrelid = to_regclass(:table) AND c.conname = :name"
        ),
        {"table": DEFAULT_PARTITION, "name": DEFAULT_FLOOR_CONSTRAINT},
    ).first()
    if row is None or not row[0]:
        return None
    return row[1]


def add_default_floor(connection: Connection, floor: datetime) -> bool:
    """Add the floor ``NOT VALID``; return whether it was added.

    ``NOT VALID`` makes this a catalog change: new rows are checked from now
    on, existing ones are not read.  It still needs ACCESS EXCLUSIVE on DEFAULT
    for that instant.  An existing floor -- validated or not -- is left alone:
    the floor never moves.
    """
    exists = connection.execute(
        text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conrelid = to_regclass(:table) AND conname = :name"
        ),
        {"table": DEFAULT_PARTITION, "name": DEFAULT_FLOOR_CONSTRAINT},
    ).first()
    if exists is not None:
        return False
    connection.execute(
        text(
            f"ALTER TABLE {DEFAULT_PARTITION} ADD CONSTRAINT {DEFAULT_FLOOR_CONSTRAINT} "
            f"CHECK (known_at < '{floor.isoformat()}') NOT VALID"
        )
    )
    return True


def validate_default_floor(connection: Connection) -> None:
    """Prove the floor against every existing DEFAULT row.

    ``VALIDATE CONSTRAINT`` takes only SHARE UPDATE EXCLUSIVE: inserts and
    reads carry on while it scans, which on a large DEFAULT is the whole cost.
    """
    connection.execute(
        text(f"ALTER TABLE {DEFAULT_PARTITION} VALIDATE CONSTRAINT {DEFAULT_FLOOR_CONSTRAINT}")
    )


def default_partition_is_empty(connection: Connection) -> bool:
    """Cheap even on a huge DEFAULT: it stops at the first visible row."""
    return not connection.execute(
        text(f"SELECT EXISTS (SELECT 1 FROM ONLY {DEFAULT_PARTITION})")
    ).scalar_one()


def missing_forward_days(
    connection: Connection, *, floor: datetime, start: date, end: date
) -> list[date]:
    """Days in ``[start, end]`` at or after the floor that have no partition."""
    existing = {name for name, _ in existing_partitions(connection)}
    days: list[date] = []
    day = start
    while day <= end:
        if _kst_midnight(day) >= floor and partition_name(day) not in existing:
            days.append(day)
        day += timedelta(days=1)
    return days


def ensure_forward_partitions(
    connection: Connection, *, floor: datetime, start: date, end: date
) -> list[str]:
    """Create the missing partitions at or after the floor; return their names.

    Only days the floor proves DEFAULT-free are touched, so nothing here scans
    DEFAULT.  Days before the floor are left to DEFAULT: creating one would
    need exactly the scan this exists to avoid, and they are past days.
    """
    created: list[str] = []
    for day in missing_forward_days(connection, floor=floor, start=start, end=end):
        created.extend(ensure_partitions(connection, start=day, end=day))
    return created


def ensure_partitions(connection: Connection, *, start: date, end: date) -> list[str]:
    """Create the daily partitions covering ``[start, end]`` inclusive.

    Idempotent, so the maintenance job can run it every night and the migration
    can run it over whatever range the existing data occupies.

    Every partition this function has ever created for this deployment used a
    UTC midnight boundary, not the KST one ``_kst_midnight`` computes
    now -- that KST fix landed after partitions already existed out to
    ``weather_values_20260917``, and ``CREATE TABLE IF NOT EXISTS`` silently
    no-ops on an existing name without checking whether its bounds match what
    was asked for.  So every night since, the *next* new day -- the first one
    whose partition does not already exist -- computed a KST-aligned lower
    bound nine hours earlier than the old UTC scheme's boundary, which
    overlapped it, and PostgreSQL refused the whole statement:
    ``partition "weather_values_20260918" would overlap partition
    "weather_values_20260917"``.  The maintenance job has been failing outright
    on this every night since, which means nothing has been purged either --
    ``ensure_partitions`` runs first in that job, and the exception aborts
    before ``drop_partitions_before`` is ever reached.

    The fix does not touch existing partitions -- rewriting a live partition's
    bounds means moving its rows, and that is not something to do implicitly
    inside a nightly maintenance call.  Instead, a new day's lower bound is
    clamped to the upper bound of any *existing* partition it would otherwise
    overlap, so it can never collide with what is already there.  A table that
    shares storage across unrelated periods (a historical backfill, a test
    fixture) must not have those inflate a day nowhere near them -- the check
    is genuine overlap, not merely "something else in the table ends later."
    In production this clamp only ever fires once, on the first genuinely new
    day right after the legacy ones; that one partition ends up covering fewer
    than 24 hours (nine, for this exact transition), and every partition after
    it is a clean, fully KST-aligned day.
    """
    if end < start:
        raise ValueError("end는 start 이후여야 합니다.")
    existing_bounds = _existing_partition_bounds(connection)
    created: list[str] = []
    day = start
    while day <= end:
        name = partition_name(day)
        lower = _kst_midnight(day)
        upper = _kst_midnight(day + timedelta(days=1))
        clamp = _overlapping_upper_bound(existing_bounds, lower=lower, upper=upper)
        if clamp is not None and clamp > lower:
            lower = clamp
        # ``IF NOT EXISTS`` is not available for ATTACH-style partition
        # creation in every supported server, but it is for CREATE TABLE ...
        # PARTITION OF, which is what this is.
        connection.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {VALUES_TABLE} "
                f"FOR VALUES FROM ('{lower.isoformat()}') "
                f"TO ('{upper.isoformat()}')"
            )
        )
        created.append(name)
        existing_bounds.append((lower, upper))
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
    """Read the first KST day a partition covers out of its bound expression.

    PostgreSQL echoes ``pg_get_expr``'s ``timestamptz`` literal back in the
    connection's own timezone, not the KST offset it was created with, so a
    partition named for a KST day can come back stamped with the *previous*
    UTC day for the hours before 09:00 KST.  Parsing it as an aware instant
    and converting to KST -- rather than trusting whatever calendar day the
    string happens to start with -- is what keeps this agreeing with
    ``partition_name`` regardless of the session's display timezone.
    """
    if not bound or "FROM (" not in bound:
        return None
    fragment = bound.split("FROM (", 1)[1].split(")", 1)[0]
    literal = fragment.strip().strip("'").strip()
    try:
        parsed = datetime.fromisoformat(literal)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(KST)
    return parsed.date()


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
