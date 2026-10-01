"""Operator steps for a ``weather_values`` DEFAULT partition that grew large.

The fresh production database of 2026-09-20 never had a dated partition, so
every fact landed in DEFAULT (21 GB of heap plus 15 GB of indexes by
2026-10-01).  Two steps get it back to the design, and both are operator steps
rather than migrations because each touches a table that size on a disk that is
the bottleneck:

1. ``establish_floor`` -- non-destructive.  Gives DEFAULT its floor
   (``known_at < F``) and creates the dated partitions from ``F`` forward.
   ``scripts/weather_values_forward_partitions.py`` runs it.
2. ``purge_report`` / ``purge_execute`` -- destructive.  Removes what is past
   retention.  ``scripts/weather_values_purge_default.py`` runs it, dry-run
   first.

Both hold the retention job's advisory lock for as long as they run, so the
nightly job never interleaves with them, print every step, and are idempotent.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
    ddl_lock_timeout_ms,
    default_partition_floor,
    delete_dangling_projection_rows,
    drop_foreign_keys_into_facts,
    drop_unvalidated_floor,
    ensure_default_partition,
    ensure_forward_partitions,
    existing_partitions,
    is_lock_conflict,
    kst_midnight,
    lock_for_partition_ddl,
    missing_forward_days,
    readd_foreign_keys_not_valid,
    validate_default_floor,
    validate_foreign_keys_into_facts,
)

Log = Callable[[str], None]

#: How long a step keeps retrying a lost lock race before giving up: about ten
#: minutes at the default ``deadlock_timeout``.
LOCK_RETRY_ATTEMPTS = 120
LOCK_RETRY_SECONDS = 2.0
#: Refuse to add a floor this close to it.  Until its partitions exist, an
#: unvalidated floor refuses every row dated from it on.
MIN_HOURS_BEFORE_FLOOR = 3.0
#: The retention job's advisory lock key; see ``purge_expired_history``.
_MAINTENANCE_LOCK = "weather_history_purge"


@contextmanager
def maintenance_lock(engine: Engine, log: Log) -> Iterator[None]:
    """Hold the retention job's advisory lock for the whole operator step.

    While an operator step holds it, the nightly job waits instead of acting
    on a half-built state; and when the nightly job does hold it, it can tell
    that an unvalidated floor is a leftover rather than a step in progress.
    """
    connection = engine.connect()
    try:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": _MAINTENANCE_LOCK}
        ).scalar_one()
        if not acquired:
            log("waiting for the retention job to finish ...")
            connection.execute(
                text("SELECT pg_advisory_lock(hashtext(:key))"), {"key": _MAINTENANCE_LOCK}
            )
        connection.commit()
        yield
    finally:
        try:
            connection.execute(
                text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": _MAINTENANCE_LOCK}
            )
            connection.commit()
        finally:
            connection.close()


def _with_lock_retries(engine: Engine, step: Callable[[Connection], Any], log: Log) -> Any:
    for attempt in range(1, LOCK_RETRY_ATTEMPTS + 1):
        try:
            with engine.begin() as connection:
                return step(connection)
        except OperationalError as exc:
            if not is_lock_conflict(exc) or attempt == LOCK_RETRY_ATTEMPTS:
                raise
            if attempt == 1 or attempt % 10 == 0:
                log(f"  lock busy, retrying ({attempt}/{LOCK_RETRY_ATTEMPTS}): {_blockers(engine)}")
            time.sleep(LOCK_RETRY_SECONDS)
    raise AssertionError("unreachable")


def _blockers(engine: Engine) -> str:
    """Who holds a lock on the fact tree right now -- for the progress log."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT DISTINCT a.pid, a.backend_type, l.mode, "
                "  left(regexp_replace(coalesce(a.query, ''), '\\s+', ' ', 'g'), 60) "
                "FROM pg_locks l JOIN pg_stat_activity a USING (pid) "
                "WHERE l.granted AND l.relation IN ("
                "  SELECT oid FROM pg_class WHERE relname LIKE :pattern "
                "  OR relname = 'weather_current_values') AND a.pid <> pg_backend_pid()"
            ),
            {"pattern": f"{VALUES_TABLE}%"},
        ).all()
    return "; ".join(f"{pid} {kind} {mode} {query}" for pid, kind, mode, query in rows) or "none"


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
        f"~{max(size[2] or 0, 0):,} rows (planner estimate)"
    )
    log(
        f"floor {DEFAULT_FLOOR_CONSTRAINT}: "
        + (f"{definition} ({'validated' if validated else 'NOT VALID'})" if exists else "absent")
    )
    dated = [name for name, day in partitions if day is not None]
    log(f"dated partitions: {len(dated)}" + (f" ({dated[0]} .. {dated[-1]})" if dated else ""))


def _drop_floor(engine: Engine, log: Log) -> None:
    def drop(connection: Connection) -> None:
        connection.execute(text(f"SET LOCAL lock_timeout = '{ddl_lock_timeout_ms(connection)}ms'"))
        drop_unvalidated_floor(connection)

    try:
        _with_lock_retries(engine, drop, log)
        log("removed the unvalidated floor; the table is as it was before")
    except Exception:
        log(
            f"!!! could not remove the unvalidated floor. Before it is reached, run: "
            f"ALTER TABLE {DEFAULT_PARTITION} DROP CONSTRAINT {DEFAULT_FLOOR_CONSTRAINT};"
        )
        raise


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

    Invariant: **a validated floor implies its partitions exist.**  The floor
    is validated and the partitions are created in one transaction, so either
    both commit or neither does.  An unvalidated floor -- left by a failure,
    an interrupt or a lost connection -- is removed: by this function on any
    failure, by the next run of it before anything else, and by the nightly
    job, which can only run while no operator step holds its advisory lock.

    Steps and locks, on production's 21 GB DEFAULT:

    1. ``ADD CONSTRAINT ... NOT VALID`` -- its own short transaction.  ACCESS
       EXCLUSIVE on DEFAULT for a catalog change: milliseconds once granted.
    2. One transaction: ``VALIDATE CONSTRAINT`` -- SHARE UPDATE EXCLUSIVE on
       DEFAULT, so reads and inserts continue -- reads the heap once (21 GB
       sequentially; tens of minutes, so off-peak).  Then, in a savepoint
       retried until it gets them, ACCESS EXCLUSIVE on the fact table and
       SHARE ROW EXCLUSIVE on the projection, and ``CREATE TABLE ... PARTITION
       OF`` for each day: with the floor now valid inside this transaction,
       PostgreSQL proves DEFAULT empty for them from the catalog, so each is
       milliseconds.  A lost lock race rolls back the savepoint only, never
       the validation.

    Every lock wait is three ``deadlock_timeout``s: long enough that an
    autovacuum in the way is cancelled by the deadlock check, short enough to
    bound how long writers queue behind the request.
    """
    floor = kst_midnight(floor_day)
    with maintenance_lock(engine, log):
        with engine.connect() as connection:
            exists, validated, definition = _floor_state(connection)
        if exists and not validated:
            log(f"found an unvalidated floor ({definition}) from an interrupted run")
            if dry_run:
                log("dry run: it would be removed first")
            else:
                _drop_floor(engine, log)
            exists = False
        if validated:
            with engine.connect() as connection:
                valid_floor = default_partition_floor(connection)
            assert valid_floor is not None
            log(f"floor already validated: {definition}; it is reused, never moved")
            if dry_run:
                return []
            return _create_missing(engine, valid_floor, ahead_days, log)

        hours = (floor - kst_now()).total_seconds() / 3600
        log(f"floor to add: known_at < {floor.isoformat()} ({hours:.1f} h from now)")
        if hours < MIN_HOURS_BEFORE_FLOOR and not force:
            raise SystemExit(
                f"the floor is {hours:.1f} h away; validation can outlast that, and "
                "rows dated after an unbacked floor are refused. Pick a later --floor-day."
            )
        if dry_run:
            log("dry run: no DDL issued")
            return []

        def add(connection: Connection) -> None:
            ensure_default_partition(connection)
            connection.execute(
                text(f"SET LOCAL lock_timeout = '{ddl_lock_timeout_ms(connection)}ms'")
            )
            add_default_floor(connection, floor)

        started = time.monotonic()
        _with_lock_retries(engine, add, log)
        log(f"added the floor NOT VALID in {time.monotonic() - started:.1f}s")
        try:
            created = _validate_and_create(engine, floor, ahead_days, log)
        except BaseException:
            log("validation or partition creation failed; removing the floor again")
            _drop_floor(engine, log)
            raise
        log(f"done: floor validated, {len(created)} partition(s) created")
        return created


def _forward_end(ahead_days: int) -> date:
    return kst_now().date() + timedelta(days=ahead_days)


def _validate_and_create(
    engine: Engine, floor: datetime, ahead_days: int, log: Log
) -> list[str]:
    start = floor.astimezone(KST).date()
    end = _forward_end(ahead_days)
    validated = False
    for attempt in range(1, LOCK_RETRY_ATTEMPTS + 1):
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(f"SET LOCAL lock_timeout = '{ddl_lock_timeout_ms(connection)}ms'")
                )
                started = time.monotonic()
                log("validating the floor (SHARE UPDATE EXCLUSIVE; inserts continue) ...")
                validate_default_floor(connection)
                validated = True
                log(f"validated in {(time.monotonic() - started) / 60:.1f} min")
                return _create_in_savepoints(connection, engine, floor, start, end, log)
        except OperationalError as exc:
            # Retry only a lost race for the VALIDATE's own lock, which costs
            # nothing; once validated, the savepoints have already retried.
            if validated or not is_lock_conflict(exc) or attempt == LOCK_RETRY_ATTEMPTS:
                raise
            log(f"  validate lock busy, retrying: {_blockers(engine)}")
            time.sleep(LOCK_RETRY_SECONDS)
    raise AssertionError("unreachable")


def _create_in_savepoints(
    connection: Connection, engine: Engine, floor: datetime, start: date, end: date, log: Log
) -> list[str]:
    for attempt in range(1, LOCK_RETRY_ATTEMPTS + 1):
        savepoint = connection.begin_nested()
        try:
            started = time.monotonic()
            lock_for_partition_ddl(connection)
            created = ensure_forward_partitions(connection, floor=floor, start=start, end=end)
            savepoint.commit()
            log(f"created {len(created)} partition(s) in {time.monotonic() - started:.2f}s")
            return created
        except OperationalError as exc:
            savepoint.rollback()
            if not is_lock_conflict(exc) or attempt == LOCK_RETRY_ATTEMPTS:
                raise
            if attempt == 1 or attempt % 10 == 0:
                log(f"  partition lock busy, retrying ({attempt}): {_blockers(engine)}")
            time.sleep(LOCK_RETRY_SECONDS)
    raise AssertionError("unreachable")


def _create_missing(engine: Engine, floor: datetime, ahead_days: int, log: Log) -> list[str]:
    end = _forward_end(ahead_days)

    start = floor.astimezone(KST).date()

    def create(connection: Connection) -> list[str]:
        if not missing_forward_days(connection, floor=floor, start=start, end=end):
            return []
        lock_for_partition_ddl(connection)
        return ensure_forward_partitions(connection, floor=floor, start=start, end=end)

    created: list[str] = _with_lock_retries(engine, create, log)
    log(f"created {len(created)} missing partition(s)")
    return created


# -- purge ---------------------------------------------------------------------


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
    total, bounds = max(int(row[0] or 0), 0), list(row[1])
    before = sum(1 for bound in bounds if bound < cutoff)
    return round(total * before / max(len(bounds), 1)), bounds[0], bounds[-1]


def purge_report(engine: Engine, *, retention_days: int, log: Log, exact: bool) -> dict[str, Any]:
    """What ``purge_execute`` would remove, without changing anything."""
    describe(engine, log)
    cutoff = kst_now() - timedelta(days=retention_days)
    with engine.connect() as connection:
        floor = default_partition_floor(connection)
        estimate, oldest, newest = _estimated_rows_before(connection, cutoff)
        report: dict[str, Any] = {
            "cutoff": cutoff,
            "floor": floor,
            "expired_rows_estimate": estimate,
            # Every row in DEFAULT is dated before the floor, so once the floor
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
            "FK impact: weather_current_values rows pointing before the cutoff are deleted "
            "first (the foreign key is ON DELETE RESTRICT); newer pointers are untouched. "
            "The swap drops that foreign key for the catalog change and re-validates it "
            "afterwards under SHARE UPDATE EXCLUSIVE."
        )
        if floor is None:
            log("no validated floor yet: run weather_values_forward_partitions.py first")
        elif report["whole_partition_expired"]:
            log("the whole DEFAULT is past retention: --execute detaches and drops it")
        else:
            log(
                "DEFAULT still holds rows inside retention. --execute deletes only expired "
                "rows, batch by batch, and refuses if there are none. The whole partition "
                f"can be swapped out from {floor + timedelta(days=retention_days)}"
            )
            if oldest is not None:
                log(f"the first rows expire from {oldest + timedelta(days=retention_days)}")
    return report


def _walk_delete(
    engine: Engine,
    *,
    table: str,
    predicate: str,
    params: dict[str, Any],
    log: Log,
    batch_blocks: int,
    pause_seconds: float,
    start_block: int = 0,
    purge: bool = False,
) -> int:
    """Delete matching rows by walking the heap ``batch_blocks`` pages at a time.

    Neither table has an index that leads with ``known_at``; a plain DELETE
    would be one statement reading the whole heap, and a ``LIMIT`` loop
    re-reads it from the start every batch.  A TID range scan reads each page
    once, one short transaction per range, ROW EXCLUSIVE only -- every reader
    and writer carries on.  Resumable from ``start_block``; a range already
    cleaned deletes nothing.
    """
    from .repository import PURGE_GUC

    with engine.connect() as connection:
        pages = int(
            connection.execute(
                text(
                    "SELECT pg_relation_size(to_regclass(:t)) "
                    "/ current_setting('block_size')::int"
                ),
                {"t": table},
            ).scalar_one()
        )
    log(f"{table}: walking {pages:,} pages from block {start_block:,}, {batch_blocks} per batch")
    total = 0
    started = time.monotonic()
    block = start_block
    batches = 0
    while block < pages:
        upper = block + batch_blocks
        deleted = 0
        for attempt in range(1, LOCK_RETRY_ATTEMPTS + 1):
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(f"SET LOCAL lock_timeout = '{ddl_lock_timeout_ms(connection)}ms'")
                    )
                    if purge:
                        connection.execute(text(f"SET LOCAL {PURGE_GUC} = 'on'"))
                    deleted = int(
                        connection.execute(
                            text(
                                f"DELETE FROM ONLY {table} "
                                "WHERE ctid >= CAST(:lo AS tid) AND ctid < CAST(:hi AS tid) "
                                f"AND {predicate}"
                            ),
                            {"lo": f"({block},0)", "hi": f"({upper},0)", **params},
                        ).rowcount
                        or 0
                    )
                break
            except OperationalError as exc:
                if not is_lock_conflict(exc) or attempt == LOCK_RETRY_ATTEMPTS:
                    raise
                time.sleep(LOCK_RETRY_SECONDS)
        total += deleted
        block = upper
        batches += 1
        if batches % 50 == 0 or block >= pages:
            elapsed = time.monotonic() - started
            done = (min(block, pages) - start_block) / max(pages - start_block, 1)
            left = elapsed / done - elapsed if done else 0.0
            log(
                f"{table}: block {min(block, pages):,}/{pages:,} ({done:.0%}), deleted "
                f"{total:,}, {elapsed / 60:.1f} min, ~{left / 60:.0f} min left; "
                f"resume with --start-block {block}"
            )
        if pause_seconds:
            time.sleep(pause_seconds)
    return total


def _delete_expired_pointers(engine: Engine, cutoff: datetime, log: Log) -> int:
    deleted = _walk_delete(
        engine,
        table="weather_current_values",
        predicate="known_at < :cutoff",
        params={"cutoff": cutoff},
        log=lambda _: None,
        batch_blocks=4096,
        pause_seconds=0,
    )
    log(f"deleted {deleted:,} projection rows before the cutoff")
    return deleted


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

    The immutability trigger lets the DELETE through only inside these
    transactions (``SET LOCAL``).  A projection row written to an expired
    fact after the pointer pass makes a batch fail on the foreign key; the
    pointers are cleared again and the batch redone.
    """
    _delete_expired_pointers(engine, cutoff, log)
    for _ in range(3):
        try:
            deleted = _walk_delete(
                engine,
                table=DEFAULT_PARTITION,
                predicate="known_at < :cutoff",
                params={"cutoff": cutoff},
                log=log,
                batch_blocks=batch_blocks,
                pause_seconds=pause_seconds,
                start_block=start_block,
                purge=True,
            )
            log(f"deleted {deleted:,} expired rows from {DEFAULT_PARTITION}")
            return deleted
        except IntegrityError:
            log("a projection row points at an expired fact; clearing pointers and resuming")
            _delete_expired_pointers(engine, cutoff, log)
    raise RuntimeError("projection rows keep pointing at expired facts; is an ingest replaying?")


#: Manual VACUUM is unthrottled by default; this caps its I/O so ingest keeps
#: the disk.  Roughly autovacuum's own default pace.
VACUUM_COST_DELAY = "2ms"
VACUUM_COST_LIMIT = 200


def vacuum_default(engine: Engine, log: Log) -> None:
    """Throttled plain VACUUM: SHARE UPDATE EXCLUSIVE, so reads and inserts continue.

    It removes the dead tuples and their index entries; it does not shrink the
    files.  The space returns to the OS when DEFAULT is swapped out, once the
    whole partition is past retention.  The nightly job's partition DDL waits
    behind it (a manual VACUUM is not cancelled for it), so run it well away
    from the nightly maintenance hour.
    """
    started = time.monotonic()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f"SET vacuum_cost_delay = '{VACUUM_COST_DELAY}'"))
        connection.execute(text(f"SET vacuum_cost_limit = {VACUUM_COST_LIMIT}"))
        connection.execute(text(f"VACUUM (ANALYZE) {DEFAULT_PARTITION}"))
    log(f"vacuumed {DEFAULT_PARTITION} in {(time.monotonic() - started) / 60:.1f} min")


def _validate_foreign_keys(engine: Engine, log: Log) -> None:
    started = time.monotonic()
    try:
        validated = _with_lock_retries(engine, validate_foreign_keys_into_facts, log)
    except IntegrityError:
        with engine.begin() as connection:
            removed = delete_dangling_projection_rows(connection)
        log(f"removed {removed:,} projection rows pointing at dropped facts")
        validated = _with_lock_retries(engine, validate_foreign_keys_into_facts, log)
    log(f"validated {validated} in {time.monotonic() - started:.1f}s")


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

    * Whole partition expired (floor <= cutoff, so every row is): delete the
      projection rows pointing before the cutoff, then one DDL transaction,
      locks taken up front in the ingest's order with a lock timeout and a
      statement timeout: drop the projection's foreign key, detach and drop
      DEFAULT, create a new empty DEFAULT with the same floor, re-add the
      foreign key ``NOT VALID``.  Every statement is a catalog change, so
      writers wait milliseconds, not the minutes-to-hours a DETACH spends
      checking a live foreign key against 3.6M projection rows.  The foreign
      key is then validated under SHARE UPDATE EXCLUSIVE while writers carry
      on.  All the space comes back at once; no dead tuples, no vacuum.
    * Some rows still inside retention: delete only the expired rows, batch
      by batch, then a throttled VACUUM.  Refused when nothing is expired.
    """
    with maintenance_lock(engine, log):
        report = purge_report(engine, retention_days=retention_days, log=log, exact=False)
        floor = report["floor"]
        if floor is None:
            raise SystemExit("refusing: no validated floor; run weather_values_forward_partitions.py")
        cutoff: datetime = report["cutoff"]
        if report["whole_partition_expired"]:
            _delete_expired_pointers(engine, cutoff, log)

            def swap(connection: Connection) -> None:
                lock_for_partition_ddl(connection, drop_foreign_keys=True)
                keys = drop_foreign_keys_into_facts(connection)
                connection.execute(
                    text(f"ALTER TABLE {VALUES_TABLE} DETACH PARTITION {DEFAULT_PARTITION}")
                )
                connection.execute(text(f"DROP TABLE {DEFAULT_PARTITION}"))
                ensure_default_partition(connection)
                add_default_floor(connection, floor)
                validate_default_floor(connection)
                readd_foreign_keys_not_valid(connection, keys)

            started = time.monotonic()
            _with_lock_retries(engine, swap, log)
            log(
                "detached and dropped the old DEFAULT; a new empty DEFAULT with the same "
                f"floor is in place ({time.monotonic() - started:.2f}s under ACCESS EXCLUSIVE)"
            )
            _validate_foreign_keys(engine, log)
        else:
            if report["expired_rows_estimate"] == 0 and start_block == 0:
                raise SystemExit(
                    "refusing: statistics show no row past retention yet, so a batched walk "
                    "would read the whole partition to delete nothing. Run --exact to check."
                )
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
