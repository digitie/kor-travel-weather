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

#: A partition-DDL statement that runs longer than this is cancelled.  Every
#: statement inside the ACCESS EXCLUSIVE window is meant to be a catalog
#: change; one that turns into a scan (a floor missing, a foreign key still in
#: place) must fail rather than hold every writer for as long as it reads.
DDL_STATEMENT_TIMEOUT = "60s"


def kst_midnight(day: date) -> datetime:
    """Public name of the KST day boundary every partition bound uses."""
    return _kst_midnight(day)


def ddl_lock_timeout_ms(connection: Connection) -> int:
    """How long partition DDL waits for a lock: three ``deadlock_timeout``s.

    It must outlast ``deadlock_timeout``.  An autovacuum that blocks a lock
    request is cancelled only by the waiter's deadlock check, which runs once
    the waiter has waited ``deadlock_timeout``; a lock timeout shorter than
    that gives up first, every time, and the DDL can never get past an
    autovacuum of DEFAULT (production's DEFAULT is due for one within a day).

    Waiting longer than ``deadlock_timeout`` cannot make an ingest the victim
    of a deadlock with this DDL: the DDL always takes a lock and then waits
    before the ingest that completes a cycle starts waiting, so the DDL's
    deadlock check fires first and it is the DDL that aborts (40P01, retried).
    The cost is that writers queue behind the waiting request for at most this
    long.
    """
    deadlock_ms = int(
        connection.execute(
            text("SELECT setting::int FROM pg_settings WHERE name = 'deadlock_timeout'")
        ).scalar_one()
    )
    return max(2000, 3 * deadlock_ms)


def lock_for_partition_ddl(connection: Connection, *, drop_foreign_keys: bool = False) -> None:
    """Take every lock partition DDL needs, in the ingest's order, up front.

    An ingest writes facts first and the projection second, so the DDL takes
    the fact table first and ``weather_current_values`` second.  Taken lazily,
    mid statement, the projection lock is what deadlocked the retention run of
    2026-09-30 against an ingest.

    ``CREATE TABLE ... PARTITION OF`` needs SHARE ROW EXCLUSIVE on the
    projection (it clones the foreign key's triggers).  Dropping that foreign
    key -- what keeps a DETACH from checking every projection row while it
    holds the whole tree -- needs ACCESS EXCLUSIVE on it.  Both are held only
    for the catalog changes that follow, bounded by ``DDL_STATEMENT_TIMEOUT``.
    """
    timeout = ddl_lock_timeout_ms(connection)
    connection.execute(text(f"SET LOCAL lock_timeout = '{timeout}ms'"))
    connection.execute(text(f"SET LOCAL statement_timeout = '{DDL_STATEMENT_TIMEOUT}'"))
    connection.execute(text(f"LOCK TABLE {VALUES_TABLE} IN ACCESS EXCLUSIVE MODE"))
    has_projection = connection.execute(
        text("SELECT to_regclass('weather_current_values') IS NOT NULL")
    ).scalar_one()
    if has_projection:
        mode = "ACCESS EXCLUSIVE" if drop_foreign_keys else "SHARE ROW EXCLUSIVE"
        connection.execute(text(f"LOCK TABLE weather_current_values IN {mode} MODE"))


def is_lock_conflict(exc: BaseException) -> bool:
    """A lock timeout or a deadlock: the DDL lost a race and can retry."""
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    return sqlstate in {"55P03", "40P01"}


def drop_foreign_keys_into_facts(connection: Connection) -> list[tuple[str, str, str]]:
    """Drop every foreign key that references the fact table; return them.

    A DETACH of a partition the projection references checks every projection
    row against it, under ACCESS EXCLUSIVE on the whole tree: on production's
    3.6M-row projection that is a nested loop of millions of probes or a hash
    join over the 21 GB DEFAULT -- minutes to hours with every ingest blocked,
    which ``lock_timeout`` does nothing to bound.  Without the foreign key the
    DETACH is a catalog change.  The caller re-adds it ``NOT VALID`` in the
    same transaction and validates it afterwards under SHARE UPDATE EXCLUSIVE,
    while writers carry on.  Returns ``(table, name, definition)``.
    """
    rows = connection.execute(
        text(
            "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
            "FROM pg_constraint WHERE contype = 'f' AND confrelid = to_regclass(:t) "
            "AND conparentid = 0 ORDER BY 1, 2"
        ),
        {"t": VALUES_TABLE},
    ).all()
    keys = [
        (table, name, definition.replace(" NOT VALID", ""))
        for table, name, definition in rows
        if table != VALUES_TABLE and not table.startswith(f"{VALUES_TABLE}_")
    ]
    for table, name, _ in keys:
        connection.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {name}"))
    return keys


def readd_foreign_keys_not_valid(
    connection: Connection, keys: list[tuple[str, str, str]]
) -> None:
    """New rows are checked from now on; existing ones by ``validate_foreign_keys``."""
    for table, name, definition in keys:
        connection.execute(
            text(f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition} NOT VALID")
        )


def unvalidated_foreign_keys_into_facts(connection: Connection) -> list[tuple[str, str]]:
    return [
        (str(table), str(name))
        for table, name in connection.execute(
            text(
                "SELECT conrelid::regclass::text, conname FROM pg_constraint "
                "WHERE contype = 'f' AND confrelid = to_regclass(:t) "
                "AND conparentid = 0 AND NOT convalidated"
            ),
            {"t": VALUES_TABLE},
        ).all()
    ]


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


#: ``default_partition_rows`` stops counting here: it runs nightly, and a full
#: count of a large DEFAULT is a full read of it.
DEFAULT_ROWS_COUNT_CAP = 100_000


def default_partition_rows(connection: Connection) -> int:
    """How many rows are in DEFAULT, counted up to ``DEFAULT_ROWS_COUNT_CAP``.

    With a floor in place every one of them is dated before it -- a backfill
    into a day nobody partitioned, or what was there before the floor existed
    -- and none can be dated after it.  Retention never drops them; only
    ``scripts/weather_values_purge_default.py`` does.  The count is capped so
    the nightly job reads at most that many rows, not the whole partition.
    """
    return int(
        connection.execute(
            text(
                f"SELECT count(*) FROM (SELECT 1 FROM ONLY {DEFAULT_PARTITION} "
                f"LIMIT {DEFAULT_ROWS_COUNT_CAP}) capped"
            )
        ).scalar_one()
    )


def validate_foreign_keys_into_facts(connection: Connection) -> list[str]:
    """Validate what ``readd_foreign_keys_not_valid`` left unvalidated.

    ``VALIDATE CONSTRAINT`` takes SHARE UPDATE EXCLUSIVE on the referencing
    table and ROW SHARE on the fact table: readers and writers carry on while
    it reads the projection once.  Raises the foreign-key violation if a row
    points at a fact that is gone (see ``delete_dangling_projection_rows``).
    """
    connection.execute(text(f"SET LOCAL lock_timeout = '{ddl_lock_timeout_ms(connection)}ms'"))
    validated: list[str] = []
    for table, name in unvalidated_foreign_keys_into_facts(connection):
        connection.execute(text(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}"))
        validated.append(name)
    return validated


def delete_dangling_projection_rows(connection: Connection) -> int:
    """Delete projection rows whose fact is gone, so the foreign key validates.

    Only reachable when a pointer to an expiring fact was written between the
    pointer delete and the drop; a plain DELETE, so writers carry on.
    """
    return int(
        connection.execute(
            text(
                "DELETE FROM weather_current_values c WHERE NOT EXISTS ("
                f"  SELECT 1 FROM {VALUES_TABLE} v "
                "  WHERE v.value_id = c.value_id AND v.known_at = c.known_at)"
            )
        ).rowcount
        or 0
    )


def forward_partition_days(connection: Connection, *, today: date) -> int | None:
    """Whole days of dated partitions ahead of ``today``; ``None`` if there are none.

    0 means today's partition is the last one: tomorrow's facts have nowhere
    to go -- with a floor they are refused, without one they sink into DEFAULT.
    """
    days = [day for _, day in existing_partitions(connection) if day is not None]
    if not days:
        return None
    return (max(days) - today).days


def dated_partition_bounds(connection: Connection) -> list[tuple[datetime, datetime]]:
    """``(lower, upper)`` of every dated partition."""
    return _existing_partition_bounds(connection)


def drop_unvalidated_floor(connection: Connection) -> bool:
    """Drop the floor if it exists but was never validated; return whether it did.

    An unvalidated floor still refuses every new DEFAULT row dated from it on,
    yet proves nothing to the planner, so no partition can be created for those
    days without the scan the floor exists to avoid.  It is only ever a step
    in progress; anywhere else it is a leftover, and a harmful one.
    """
    row = connection.execute(
        text(
            "SELECT convalidated FROM pg_constraint "
            "WHERE conrelid = to_regclass(:table) AND conname = :name"
        ),
        {"table": DEFAULT_PARTITION, "name": DEFAULT_FLOOR_CONSTRAINT},
    ).first()
    if row is None or row[0]:
        return False
    connection.execute(
        text(f"ALTER TABLE {DEFAULT_PARTITION} DROP CONSTRAINT {DEFAULT_FLOOR_CONSTRAINT}")
    )
    return True


def floor_constraint_present(connection: Connection) -> bool:
    """Whether DEFAULT has the floor constraint at all, validated or not."""
    return (
        connection.execute(
            text(
                "SELECT 1 FROM pg_constraint "
                "WHERE conrelid = to_regclass(:table) AND conname = :name"
            ),
            {"table": DEFAULT_PARTITION, "name": DEFAULT_FLOOR_CONSTRAINT},
        ).first()
        is not None
    )
