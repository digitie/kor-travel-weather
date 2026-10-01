"""Operator steps for a ``weather_values`` DEFAULT partition that grew large.

The fresh production database of 2026-09-20 never had a dated partition, so
every fact landed in DEFAULT (35M rows, 36 GB by 2026-10-01).  Two steps get it
back to the design, and both are operator steps rather than migrations because
each touches a 36 GB table on a disk that is the bottleneck:

1. ``establish_floor`` -- non-destructive.  Gives DEFAULT its floor
   (``known_at < F``) and creates the dated partitions from ``F`` forward.
   ``scripts/weather_values_forward_partitions.py`` runs it.
2. ``purge_report`` / ``purge_execute`` -- destructive.  Once every row in
   DEFAULT is past retention, swaps DEFAULT for an empty one and drops the old
   table.  ``scripts/weather_values_purge_default.py`` runs it, dry-run first.

Both print what they do, take every DDL lock with a short lock timeout and
retry, and are idempotent.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError, OperationalError

from .models import KST, kst_now
from .partitions import (
    DEFAULT_FLOOR_CONSTRAINT,
    DEFAULT_PARTITION,
    VALUES_TABLE,
    add_default_floor,
    default_partition_floor,
    ensure_default_partition,
    ensure_forward_partitions,
    existing_partitions,
    is_lock_conflict,
    kst_midnight,
    lock_for_partition_ddl,
    validate_default_floor,
)

Log = Callable[[str], None]

#: How long a step keeps retrying a lost lock race before giving up.
LOCK_RETRY_ATTEMPTS = 120
LOCK_RETRY_SECONDS = 2.0
#: Refuse to start the floor step this close to the floor: until the forward
#: partitions exist, a row dated at or after the floor is refused.
MIN_HOURS_BEFORE_FLOOR = 3.0


def _with_lock_retries(engine: Engine, step: Callable[[Connection], Any], log: Log) -> Any:
    for attempt in range(1, LOCK_RETRY_ATTEMPTS + 1):
        try:
            with engine.begin() as connection:
                return step(connection)
        except OperationalError as exc:
            if not is_lock_conflict(exc) or attempt == LOCK_RETRY_ATTEMPTS:
                raise
            if attempt == 1 or attempt % 10 == 0:
                log(f"  lock busy, retrying ({attempt}/{LOCK_RETRY_ATTEMPTS})")
            time.sleep(LOCK_RETRY_SECONDS)
    raise AssertionError("unreachable")


def _floor_state(connection: Connection) -> tuple[bool, bool, str | None]:
    row = connection.execute(
        text(
            "SELECT c.convalidated, pg_get_constraintdef(c.oid) FROM pg_constraint c "
            "WHERE c.conrelid = to_regclass(:table) AND c.conname = :name"
        ),
        {"table": DEFAULT_PARTITION, "name": DEFAULT_FLOOR_CONSTRAINT},
    ).first()
    if row is None:
        return False, False, None
    return True, bool(row[0]), str(row[1])


def describe(engine: Engine, log: Log) -> None:
    with engine.connect() as connection:
        size = connection.execute(
            text(
                "SELECT pg_size_pretty(pg_relation_size(to_regclass(:t))), "
                "pg_size_pretty(pg_total_relation_size(to_regclass(:t))), "
                "(SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass(:t))"
            ),
            {"t": DEFAULT_PARTITION},
        ).one()
        exists, validated, definition = _floor_state(connection)
        partitions = existing_partitions(connection)
    log(
        f"{DEFAULT_PARTITION}: heap {size[0]}, with indexes {size[1]}, "
        f"~{size[2]:,} rows (planner estimate)"
    )
    log(
        f"floor {DEFAULT_FLOOR_CONSTRAINT}: "
        + (f"{definition} ({'validated' if validated else 'NOT VALID'})" if exists else "absent")
    )
    dated = [name for name, day in partitions if day is not None]
    log(f"dated partitions: {len(dated)}" + (f" ({dated[0]} .. {dated[-1]})" if dated else ""))


def establish_floor(
    engine: Engine,
    *,
    floor_day: date,
    ahead_days: int,
    log: Log,
    dry_run: bool = False,
    force: bool = False,
) -> list[str]:
    """Give DEFAULT its floor and create the partitions from it forward.

    Lock modes, on a 36 GB DEFAULT:

    * ``ADD CONSTRAINT ... NOT VALID`` -- ACCESS EXCLUSIVE on DEFAULT for a
      catalog change: milliseconds once granted.  It waits behind any open
      transaction that touched DEFAULT, so it is taken with a 500 ms lock
      timeout and retried rather than queued (a queued ACCESS EXCLUSIVE would
      stall every writer behind it).
    * ``VALIDATE CONSTRAINT`` -- SHARE UPDATE EXCLUSIVE on DEFAULT: reads and
      inserts continue.  It reads the whole heap once (36 GB sequentially;
      expect tens of minutes on this disk under load).  Only VACUUM and other
      DDL on DEFAULT wait for it; autovacuum yields to it.
    * ``CREATE TABLE ... PARTITION OF`` per day -- SHARE ROW EXCLUSIVE on
      ``weather_current_values`` and ACCESS EXCLUSIVE on the fact table, taken
      up front by ``lock_for_partition_ddl`` with the same timeout and retry.
      With the floor validated PostgreSQL proves DEFAULT holds nothing for the
      new day from the catalog, so the lock is held for milliseconds.

    Idempotent: an existing floor is reused (it never moves), a validated one
    is not re-read, and existing partitions are skipped.  If validation fails
    -- DEFAULT holds a row dated at or after the floor -- the NOT VALID floor is
    removed again, leaving the table as it was.
    """
    floor = kst_midnight(floor_day)
    with engine.connect() as connection:
        exists, validated, definition = _floor_state(connection)
    if exists:
        log(f"floor already present: {definition}; it is reused, not moved")
    else:
        hours = (floor - kst_now()).total_seconds() / 3600
        if hours < MIN_HOURS_BEFORE_FLOOR and not force:
            raise SystemExit(
                f"floor {floor.isoformat()} is {hours:.1f} h away; validation can take "
                f"longer than that and rows dated after the floor are refused until the "
                f"partitions exist. Pick a later --floor-day or pass --force."
            )
        log(f"floor to add: known_at < {floor.isoformat()}")
    if dry_run:
        log("dry run: no DDL issued")
        return []

    if not exists:
        started = time.monotonic()

        def add(connection: Connection) -> None:
            ensure_default_partition(connection)
            connection.execute(text("SET LOCAL lock_timeout = '500ms'"))
            add_default_floor(connection, floor)

        _with_lock_retries(engine, add, log)
        log(f"added NOT VALID floor in {time.monotonic() - started:.1f}s")
    if not validated:
        started = time.monotonic()
        log("validating floor (SHARE UPDATE EXCLUSIVE; inserts continue) ...")

        def validate(connection: Connection) -> None:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            validate_default_floor(connection)

        try:
            _with_lock_retries(engine, validate, log)
        except IntegrityError:
            log("validation failed: DEFAULT holds a row at or after the floor")
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '5s'"))
                connection.execute(
                    text(
                        f"ALTER TABLE {DEFAULT_PARTITION} "
                        f"DROP CONSTRAINT IF EXISTS {DEFAULT_FLOOR_CONSTRAINT}"
                    )
                )
            log("removed the NOT VALID floor again; nothing else was changed")
            raise
        log(f"validated in {time.monotonic() - started:.1f}s")

    with engine.connect() as connection:
        valid_floor = default_partition_floor(connection)
    assert valid_floor is not None
    created: list[str] = []
    end = kst_now().date() + timedelta(days=ahead_days)
    day = valid_floor.astimezone(KST).date()
    while day <= end:
        started = time.monotonic()

        def create(connection: Connection, on: date = day) -> list[str]:
            lock_for_partition_ddl(connection)
            return ensure_forward_partitions(connection, floor=valid_floor, start=on, end=on)

        made = _with_lock_retries(engine, create, log)
        if made:
            log(f"created {made[0]} in {time.monotonic() - started:.2f}s")
        created.extend(made)
        day += timedelta(days=1)
    log(f"done: {len(created)} partition(s) created")
    return created


def purge_report(engine: Engine, *, retention_days: int, log: Log, exact: bool) -> dict[str, Any]:
    """What ``purge_execute`` would remove, without changing anything.

    The FK impact is the projection rows whose ``known_at`` is below the floor:
    those point into DEFAULT and must be deleted before it can be detached.
    """
    describe(engine, log)
    cutoff = kst_now() - timedelta(days=retention_days)
    with engine.connect() as connection:
        floor = default_partition_floor(connection)
        report: dict[str, Any] = {"cutoff": cutoff, "floor": floor}
        if floor is None:
            log("no validated floor: run weather_values_forward_partitions.py first")
            report["eligible"] = False
            return report
        report["eligible"] = floor <= cutoff
        log(f"retention cutoff {cutoff.isoformat()}, floor {floor.isoformat()}")
        if not report["eligible"]:
            log(
                "NOT eligible yet: DEFAULT may hold rows inside the retention window. "
                f"Eligible from {(floor + timedelta(days=retention_days)).isoformat()}"
            )
        pointers = connection.execute(
            text("SELECT count(*) FROM weather_current_values WHERE known_at < :floor"),
            {"floor": floor},
        ).scalar_one()
        orphaned = connection.execute(
            text(
                "SELECT count(*) FROM (SELECT location_id FROM weather_current_values "
                "GROUP BY location_id HAVING max(known_at) < :floor) quiet"
            ),
            {"floor": floor},
        ).scalar_one()
        report.update(pointers=pointers, locations_losing_current=orphaned)
        log(
            f"FK impact: {pointers:,} weather_current_values rows point below the floor "
            f"and would be deleted; {orphaned:,} locations would have no current value"
        )
        if exact:
            rows, oldest, newest = connection.execute(
                text(f"SELECT count(*), min(known_at), max(known_at) FROM ONLY {DEFAULT_PARTITION}")
            ).one()
            report.update(rows=rows, oldest=oldest, newest=newest)
            log(f"exact: {rows:,} rows, known_at {oldest} .. {newest}")
    return report


def purge_execute(engine: Engine, *, retention_days: int, log: Log) -> None:
    """Swap DEFAULT for an empty one and drop the old table.  Destructive.

    1. Delete the projection rows pointing below the floor (normally none:
       the nightly retention already deleted every pointer older than its
       cutoff, which is later than the floor here).
    2. One DDL transaction, locks taken up front with a short timeout and
       retried: ``DETACH PARTITION`` (ACCESS EXCLUSIVE on the fact table;
       PostgreSQL also checks that no ``weather_current_values`` row still
       references the detached rows, which reads the projection once while
       the lock is held -- the one step here whose duration grows with data),
       ``DROP TABLE`` of the detached DEFAULT (its files are unlinked at
       commit; the 36 GB comes back at once), then a new empty DEFAULT with
       the same floor (instant on an empty table).  Every writer waits for
       this transaction, so it is the step to run at a quiet hour.
    """
    report = purge_report(engine, retention_days=retention_days, log=log, exact=False)
    if not report.get("eligible"):
        raise SystemExit("refusing: DEFAULT is not entirely past retention")
    floor: datetime = report["floor"]

    with engine.begin() as connection:
        deleted = connection.execute(
            text("DELETE FROM weather_current_values WHERE known_at < :floor"),
            {"floor": floor},
        ).rowcount
    log(f"deleted {deleted:,} projection rows below the floor")

    def swap(connection: Connection) -> None:
        lock_for_partition_ddl(connection)
        connection.execute(
            text(f"ALTER TABLE {VALUES_TABLE} DETACH PARTITION {DEFAULT_PARTITION}")
        )
        connection.execute(text(f"DROP TABLE {DEFAULT_PARTITION}"))
        ensure_default_partition(connection)
        add_default_floor(connection, floor)
        validate_default_floor(connection)

    started = time.monotonic()
    _with_lock_retries(engine, swap, log)
    log(
        f"detached and dropped the old DEFAULT; new empty DEFAULT with the same floor "
        f"in place ({time.monotonic() - started:.1f}s)"
    )
