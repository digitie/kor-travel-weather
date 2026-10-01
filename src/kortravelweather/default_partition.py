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


def _estimated_rows_before(connection: Connection, cutoff: datetime) -> tuple[int, Any, Any]:
    """Rows of DEFAULT dated before ``cutoff``, from the planner's statistics.

    Instant and read-only: ``pg_stats`` holds an equi-depth histogram of
    ``known_at``, so the share of bounds before the cutoff is the share of
    rows.  ``--exact`` replaces it with a real count (a full read).
    """
    row = connection.execute(
        text(
            "SELECT (SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass(:t)), "
            "  histogram_bounds::text::timestamptz[] "
            "FROM pg_stats WHERE tablename = :t AND attname = 'known_at'"
        ),
        {"t": DEFAULT_PARTITION},
    ).first()
    if row is None or not row[1]:
        return 0, None, None
    total, bounds = int(row[0] or 0), list(row[1])
    before = sum(1 for bound in bounds if bound < cutoff)
    return round(total * before / max(len(bounds), 1)), bounds[0], bounds[-1]


def purge_report(engine: Engine, *, retention_days: int, log: Log, exact: bool) -> dict[str, Any]:
    """What ``purge_execute`` would remove, without changing anything."""
    describe(engine, log)
    cutoff = kst_now() - timedelta(days=retention_days)
    with engine.connect() as connection:
        floor = default_partition_floor(connection)
        estimate, oldest, newest = _estimated_rows_before(connection, cutoff)
        pointers = connection.execute(
            text("SELECT count(*) FROM weather_current_values WHERE known_at < :cutoff"),
            {"cutoff": cutoff},
        ).scalar_one()
        report: dict[str, Any] = {
            "cutoff": cutoff,
            "floor": floor,
            "expired_rows_estimate": estimate,
            "pointers": pointers,
            # Every row in DEFAULT is older than the floor, so once the floor
            # itself is past retention the whole partition is expired.
            "whole_partition_expired": floor is not None and floor <= cutoff,
        }
        log(f"retention: {retention_days} days -> cutoff {cutoff.isoformat()}")
        log(f"known_at in DEFAULT (statistics): {oldest} .. {newest}")
        log(f"rows before the cutoff (estimate): {estimate:,}")
        if exact:
            rows, expired = connection.execute(
                text(
                    f"SELECT count(*), count(*) FILTER (WHERE known_at < :cutoff) "
                    f"FROM ONLY {DEFAULT_PARTITION}"
                ),
                {"cutoff": cutoff},
            ).one()
            report.update(rows=rows, expired_rows=expired)
            log(f"exact: {rows:,} rows, {expired:,} before the cutoff")
        log(
            f"FK impact: {pointers:,} weather_current_values rows point before the cutoff. "
            "They are deleted first: the foreign key is ON DELETE RESTRICT, so a fact "
            "cannot go while a pointer to it exists. Newer pointers are untouched."
        )
        if floor is None:
            log("no validated floor yet: run weather_values_forward_partitions.py first")
        elif report["whole_partition_expired"]:
            log("the whole DEFAULT is past retention: --execute detaches and drops it")
        else:
            whole = floor + timedelta(days=retention_days)
            log(
                "DEFAULT still holds rows inside retention: --execute deletes only the "
                f"expired ones, in small batches. The whole partition can go from {whole}"
            )
            if oldest is not None:
                log(f"the first rows expire from {oldest + timedelta(days=retention_days)}")
    return report


def _delete_expired_pointers(engine: Engine, cutoff: datetime, log: Log) -> int:
    """Delete projection rows older than the cutoff, 10,000 per transaction."""
    total = 0
    while True:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '2s'"))
            deleted = int(
                connection.execute(
                    text(
                        "DELETE FROM weather_current_values WHERE value_id IN ("
                        "  SELECT value_id FROM weather_current_values "
                        "  WHERE known_at < :cutoff LIMIT 10000)"
                    ),
                    {"cutoff": cutoff},
                ).rowcount
                or 0
            )
        total += deleted
        if not deleted:
            break
    log(f"deleted {total:,} projection rows before the cutoff")
    return total


def purge_expired_rows(
    engine: Engine,
    *,
    cutoff: datetime,
    log: Log,
    batch_blocks: int = 2048,
    pause_seconds: float = 0.2,
    start_block: int = 0,
) -> int:
    """Delete DEFAULT's rows dated before ``cutoff`` in short batches.

    DEFAULT has no index leading with ``known_at``, so a plain
    ``DELETE ... WHERE known_at <`` would be one statement reading the whole
    heap.  This walks the heap instead, ``batch_blocks`` pages at a time
    (16 MB at the default) with a TID range scan, deleting only expired rows in
    each range, one short transaction per range.  Each transaction takes ROW
    EXCLUSIVE on DEFAULT -- compatible with every reader and writer -- and row
    locks only on expired rows nobody else touches; ``lock_timeout`` bounds
    any wait.  The immutability trigger lets the DELETE through only inside
    these transactions (``SET LOCAL``).

    Resumable: progress lines print the next block, and ``start_block`` picks
    up there.  Idempotent: a range already cleaned deletes nothing.
    """
    from .repository import PURGE_GUC

    _delete_expired_pointers(engine, cutoff, log)
    with engine.connect() as connection:
        pages = int(
            connection.execute(
                text(
                    "SELECT pg_relation_size(to_regclass(:t)) "
                    "/ current_setting('block_size')::int"
                ),
                {"t": DEFAULT_PARTITION},
            ).scalar_one()
        )
    log(f"walking {pages:,} pages from block {start_block:,}, {batch_blocks} per batch")
    deleted_total = 0
    started = time.monotonic()
    block = start_block
    batches = 0
    while block < pages:
        upper = block + batch_blocks
        deleted = 0
        for attempt in range(1, LOCK_RETRY_ATTEMPTS + 1):
            try:
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout = '2s'"))
                    connection.execute(text(f"SET LOCAL {PURGE_GUC} = 'on'"))
                    deleted = int(
                        connection.execute(
                            text(
                                f"DELETE FROM ONLY {DEFAULT_PARTITION} "
                                "WHERE ctid >= CAST(:lo AS tid) AND ctid < CAST(:hi AS tid) "
                                "AND known_at < :cutoff"
                            ),
                            {"lo": f"({block},0)", "hi": f"({upper},0)", "cutoff": cutoff},
                        ).rowcount
                        or 0
                    )
                break
            except OperationalError as exc:
                if not is_lock_conflict(exc) or attempt == LOCK_RETRY_ATTEMPTS:
                    raise
                time.sleep(LOCK_RETRY_SECONDS)
            except IntegrityError:
                # An ingest wrote a pointer to an expired fact after the
                # pointer pass.  Clear it and redo this range.
                if attempt == LOCK_RETRY_ATTEMPTS:
                    raise
                _delete_expired_pointers(engine, cutoff, log)
        deleted_total += deleted
        block = upper
        batches += 1
        if batches % 50 == 0 or block >= pages:
            elapsed = time.monotonic() - started
            done = (min(block, pages) - start_block) / max(pages - start_block, 1)
            left = elapsed / done - elapsed if done else 0.0
            log(
                f"block {min(block, pages):,}/{pages:,} ({done:.0%}), deleted "
                f"{deleted_total:,}, {elapsed / 60:.1f} min, ~{left / 60:.0f} min left; "
                f"resume with --start-block {block}"
            )
        if pause_seconds:
            time.sleep(pause_seconds)
    log(f"deleted {deleted_total:,} expired rows from {DEFAULT_PARTITION}")
    return deleted_total


def vacuum_default(engine: Engine, log: Log) -> None:
    """Plain VACUUM: SHARE UPDATE EXCLUSIVE, so reads and inserts continue.

    It removes the dead tuples and their index entries; it does not shrink the
    files.  The space returns to the OS when DEFAULT is swapped out, once the
    whole partition is past retention.
    """
    started = time.monotonic()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f"VACUUM (ANALYZE) {DEFAULT_PARTITION}"))
    log(f"vacuumed {DEFAULT_PARTITION} in {(time.monotonic() - started) / 60:.1f} min")


def purge_execute(
    engine: Engine,
    *,
    retention_days: int,
    log: Log,
    vacuum: bool = True,
    start_block: int = 0,
    batch_blocks: int = 2048,
    pause_seconds: float = 0.2,
) -> None:
    """Remove what is past retention from DEFAULT.  Destructive.

    Two strategies, chosen by what the floor proves:

    * Whole partition expired (floor <= cutoff, so every row is): delete the
      projection rows pointing before the cutoff, then one DDL transaction --
      locks taken up front with a short timeout and retried -- detaches
      DEFAULT (ACCESS EXCLUSIVE on the fact table; PostgreSQL also checks that
      no projection row still references it, one read of the projection while
      the lock is held), drops it (files unlinked at commit: all the space
      back at once, no dead tuples, no vacuum) and creates a new empty DEFAULT
      with the same floor.  Writers wait for that one transaction only.
    * Some rows still inside retention: delete only the expired rows, batch
      by batch (``purge_expired_rows``), then a plain VACUUM.  Nothing blocks
      writers; the files keep their size until the swap above.
    """
    report = purge_report(engine, retention_days=retention_days, log=log, exact=False)
    floor = report["floor"]
    if floor is None:
        raise SystemExit("refusing: no validated floor; run weather_values_forward_partitions.py")
    cutoff: datetime = report["cutoff"]
    if report["whole_partition_expired"]:
        _delete_expired_pointers(engine, cutoff, log)

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
            "detached and dropped the old DEFAULT; a new empty DEFAULT with the same "
            f"floor is in place ({time.monotonic() - started:.1f}s)"
        )
    else:
        purge_expired_rows(
            engine,
            cutoff=cutoff,
            log=log,
            batch_blocks=batch_blocks,
            pause_seconds=pause_seconds,
            start_block=start_block,
        )
        if vacuum:
            vacuum_default(engine, log)
    describe(engine, log)
