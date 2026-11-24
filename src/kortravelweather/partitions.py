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

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import NamedTuple

from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError

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

    Against an ingest, which locks in the same order (facts, then
    projection), the DDL is normally the first to wait in a cycle, so its
    deadlock check fires first and the DDL aborts (40P01, retried).  That is
    not guaranteed for every session: a read of ``weather_current_values``
    that then reads the fact table -- the API's current-value path -- takes
    the two in the other order, and if it starts waiting first it is the one
    the deadlock check cancels.  That is accepted: it needs a read to
    interleave with the nightly drop or the one-time swap, the only DDL that
    takes the projection lock strongly enough, and the reader gets an error it
    can retry.  Changing the readers' lock order is out of scope.  The cost
    for everyone else is that they queue behind the waiting request for at
    most this long.
    """
    deadlock_ms = int(
        connection.execute(
            text("SELECT setting::int FROM pg_settings WHERE name = 'deadlock_timeout'")
        ).scalar_one()
    )
    return max(2000, 3 * deadlock_ms)


#: The order an ingest transaction takes the fact table's referenced tables in:
#: it inserts the source records its facts cite before the facts.
_INGEST_REFERENCE_ORDER = ("weather_source_records", "weather_locations")


def referenced_tables(connection: Connection) -> list[str]:
    """Tables the fact table's own foreign keys point at, in the ingest's order.

    Partition DDL locks them too, mid statement, unless they are taken first:
    ``CREATE TABLE ... PARTITION OF`` and ``DETACH`` take SHARE ROW EXCLUSIVE
    on each (they clone or create the foreign keys' triggers), and dropping a
    detached partition takes ACCESS EXCLUSIVE on each (it removes them) --
    measured on PostgreSQL 16.  Taken lazily after the fact table, that is
    the reverse of an ingest, which writes source records before facts: the
    ingest then waits for the fact table, the DDL for the source records, and
    the deadlock check kills whichever waited first -- usually the ingest.
    """
    tables = connection.execute(
        text(
            "SELECT DISTINCT confrelid::regclass::text FROM pg_constraint "
            "WHERE contype = 'f' AND conrelid = to_regclass(:t) AND conparentid = 0"
        ),
        {"t": VALUES_TABLE},
    ).scalars().all()
    order = {name: index for index, name in enumerate(_INGEST_REFERENCE_ORDER)}
    return sorted(
        (str(table) for table in tables if table != VALUES_TABLE),
        key=lambda table: (order.get(table, len(order)), table),
    )


#: 야간 유지보수(``WeatherRepository.purge_expired_history``)가 표 lock을 기다리는 기본
#: 상한(ms). 운영자 스크립트·migration·수집은 그대로 ``ddl_lock_timeout_ms``를 쓴다.
#:
#: ACCESS EXCLUSIVE 요청이 대기열에 서 있는 동안 뒤에 온 reader(지도 읽기)도 그 뒤에 줄을
#: 선다. ``ddl_lock_timeout_ms``(deadlock_timeout의 세 배, 기본 3초)를 그대로 쓰면 시도마다
#: reader가 최대 3초 멈췄다(2026-11-24 Map 호출 검토 L3). 수집 transaction에 진 시도는 짧게
#: 포기하고 다음 조용한 순간(``WeatherRepository._await_quiet_tables``)에 다시 한다.
#: 차단자가 autovacuum worker뿐이면 그때만 ``ddl_lock_timeout_ms``로 다시 기다린다
#: (``lock_table``): autovacuum은 대기자가 ``deadlock_timeout``을 넘겨 기다려야 그 deadlock
#: 검사가 취소하므로, 짧은 상한으로는 autovacuum을 영영 이기지 못한다.
BRIEF_DDL_LOCK_TIMEOUT_MS = 400

#: ``pg_stat_activity.backend_type``에서 autovacuum이 쓰는 이름.
AUTOVACUUM_BACKEND = "autovacuum worker"

#: 요청한 표 lock mode와 충돌하는, 이미 잡힌 lock mode(PostgreSQL 문서 "Table-Level Lock
#: Modes"의 충돌표). 이 모듈이 요청하는 mode만 둔다.
_CONFLICTING_MODES: dict[str, tuple[str, ...]] = {
    "SHARE": (
        "RowExclusiveLock", "ShareUpdateExclusiveLock", "ShareRowExclusiveLock",
        "ExclusiveLock", "AccessExclusiveLock",
    ),
    "SHARE ROW EXCLUSIVE": (
        "RowExclusiveLock", "ShareUpdateExclusiveLock", "ShareLock",
        "ShareRowExclusiveLock", "ExclusiveLock", "AccessExclusiveLock",
    ),
    "ACCESS EXCLUSIVE": (
        "AccessShareLock", "RowShareLock", "RowExclusiveLock", "ShareUpdateExclusiveLock",
        "ShareLock", "ShareRowExclusiveLock", "ExclusiveLock", "AccessExclusiveLock",
    ),
}


def set_ddl_timeouts(connection: Connection, *, lock_timeout_ms: int | None = None) -> None:
    """DDL transaction의 lock·문장 상한. ``lock_timeout_ms``가 없으면 ``ddl_lock_timeout_ms``."""
    timeout = ddl_lock_timeout_ms(connection) if lock_timeout_ms is None else lock_timeout_ms
    connection.execute(text(f"SET LOCAL lock_timeout = '{int(timeout)}ms'"))
    connection.execute(text(f"SET LOCAL statement_timeout = '{DDL_STATEMENT_TIMEOUT}'"))


class LockHolder(NamedTuple):
    """표 lock을 쥔 다른 transaction 하나(``conflicting_lock_holders``)."""

    #: ``pg_locks.virtualtransaction``. transaction마다 새로 받으므로 같은 연결(pid)을
    #: 다시 쓰는 다음 transaction과 구별된다.
    transaction: str
    pid: int | None
    #: ``pg_stat_activity.backend_type``. 모르면 ``"unknown"``.
    kind: str


def conflicting_lock_holders(connection: Connection, table: str, mode: str) -> set[LockHolder]:
    """``table``(파티션 포함)에 ``mode``와 충돌하는 lock을 이미 쥔 다른 transaction.

    세션 정보가 없는 보유자(준비된 transaction 등)의 ``kind``는 ``"unknown"``이다. superuser나
    ``pg_read_all_stats``가 아닌 role에는 background process(autovacuum 포함)의
    ``backend_type``이 NULL로 보여 역시 ``"unknown"``이 된다(compose의 ``POSTGRES_USER``는
    superuser다). granted 보유자를 충돌표로 걸러 읽는다. 파티션 부모에 거는 ``LOCK TABLE``은
    모든 파티션으로 내려가고 autovacuum은 파티션 하나를 잡으므로 ``table`` 자신과 그 파티션
    트리 전체를 본다.

    ``pg_stat_activity``는 transaction 안에서 처음 읽은 snapshot을 계속 돌려주므로(그 뒤에
    생긴 pid는 보이지 않는다) 읽기 전에 ``pg_stat_clear_snapshot()``으로 버린다.
    """
    connection.execute(text("SELECT pg_stat_clear_snapshot()"))
    rows = connection.execute(
        text(
            "SELECT DISTINCT l.virtualtransaction, l.pid, "
            "coalesce(a.backend_type, 'unknown') FROM pg_locks l "
            "LEFT JOIN pg_stat_activity a ON a.pid = l.pid "
            "WHERE l.locktype = 'relation' AND l.granted "
            "AND l.pid IS DISTINCT FROM pg_backend_pid() "
            "AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database()) "
            "AND l.mode = ANY (CAST(:modes AS text[])) "
            # pg_partition_tree는 파티션이 아닌 보통 표에는 행을 내지 않는다(PostgreSQL 16).
            "AND l.relation IN (SELECT to_regclass(:t) "
            "UNION ALL SELECT relid FROM pg_partition_tree(to_regclass(:t)))"
        ),
        {"t": table, "modes": list(_CONFLICTING_MODES[mode])},
    ).all()
    return {
        LockHolder(str(transaction), None if pid is None else int(pid), str(kind))
        for transaction, pid, kind in rows
    }


def blocking_holder_kinds(
    before: Iterable[LockHolder], after: Iterable[LockHolder]
) -> set[str]:
    """짧은 대기에 진 직후의 보유자(``after``) 중 차단자로 볼 것의 종류.

    ``after``에는 우리 요청 뒤에 줄 섰던 reader·수집도 섞인다. 부모에 거는 ``LOCK TABLE``은
    부모를 먼저 잡고 파티션을 차례로 잡으므로, autovacuum이 쥔 파티션에서 기다리는 동안
    이미 쥔 부모 lock 뒤에 그들이 줄을 서고, savepoint로 되돌리는 순간 lock을 받는다.
    그래서 LOCK 전(``before``)부터 같은 transaction이 쥐고 있던 보유자만 센다. transaction은
    끝날 때까지 표 lock을 놓지 않으므로 그 보유자는 대기 내내 쥐고 있었다.

    autovacuum worker는 대기 중에 시작했어도 센다. 아직 잡지 않은 파티션을 처리하기 시작한
    것이고, wraparound 방지가 아닌 autovacuum은 lock을 조건부(SKIP LOCKED)로만 잡아 누구 뒤에도
    줄 서지 않는다. 대기 직전에 시작해 대기 내내 쥔 수집은 놓칠 수 있으나, 그때도 autovacuum이
    함께 있어야 긴 대기로 가고 그 대기는 예전 상한(``ddl_lock_timeout_ms``)과 같다.
    """
    held_before = {holder.transaction for holder in before}
    return {
        holder.kind
        for holder in after
        if holder.transaction in held_before or holder.kind == AUTOVACUUM_BACKEND
    }


def lock_table(connection: Connection, table: str, mode: str, *, brief: bool = False) -> None:
    """``LOCK TABLE table IN mode MODE``.

    ``brief``이면 호출자가 둔 짧은 ``lock_timeout``(``BRIEF_DDL_LOCK_TIMEOUT_MS``)으로 먼저
    시도한다. 그 상한에 지면 savepoint로 되돌린 뒤, 차단자가 autovacuum worker뿐일 때만
    ``ddl_lock_timeout_ms``로 한 번 더 기다리고 짧은 상한을 되돌린다. 그 밖의
    보유자(수집·API reader·다른 DDL)에게 지면 lock 경합(55P03)을 그대로 올려 호출자가
    transaction을 rollback하고 조용한 순간에 다시 시도하게 한다.

    차단자는 LOCK 전과 짧은 대기에 진 직후의 보유자로 가린다(``blocking_holder_kinds``).
    ``pg_blocking_pids``는 그 세션이 기다리는 동안에만 답하므로 대기 중인 이 연결로는 물을 수
    없고, 다른 연결로 대기 중에 물으려면 시도마다 연결과 thread가 하나씩 더 든다.
    """
    statement = text(f"LOCK TABLE {table} IN {mode} MODE")
    if not brief:
        connection.execute(statement)
        return
    before = conflicting_lock_holders(connection, table, mode)
    try:
        with connection.begin_nested():
            connection.execute(statement)
        return
    except OperationalError as exc:
        if getattr(getattr(exc, "orig", None), "sqlstate", None) != "55P03":
            raise
        after = conflicting_lock_holders(connection, table, mode)
        if blocking_holder_kinds(before, after) != {AUTOVACUUM_BACKEND}:
            raise
    connection.execute(text(f"SET LOCAL lock_timeout = '{ddl_lock_timeout_ms(connection)}ms'"))
    connection.execute(statement)
    connection.execute(text(f"SET LOCAL lock_timeout = '{BRIEF_DDL_LOCK_TIMEOUT_MS}ms'"))


def lock_for_partition_ddl(
    connection: Connection, *, drop_foreign_keys: bool = False, brief: bool = False
) -> None:
    """Take every lock partition DDL needs, in the ingest's order, up front.

    An ingest writes source records, then facts, then the projection, so the
    DDL takes the fact table's referenced tables first (``referenced_tables``),
    the fact table second and ``weather_current_values`` last.  Taken lazily,
    mid statement, the projection lock is what deadlocked the retention run of
    2026-09-30 against an ingest, and the referenced tables are what made the
    DDL kill an ingest in ``tests/test_retention_under_ingest.py``.

    ``CREATE TABLE ... PARTITION OF`` needs SHARE ROW EXCLUSIVE on the
    projection (it clones the foreign key's triggers).  Dropping that foreign
    key -- what keeps a DETACH from checking every projection row while it
    holds the whole tree -- needs ACCESS EXCLUSIVE on it.  All are held only
    for the catalog changes that follow, bounded by ``DDL_STATEMENT_TIMEOUT``.

    ``brief``: 야간 유지보수용. 각 lock을 ``BRIEF_DDL_LOCK_TIMEOUT_MS``까지만 기다리고
    autovacuum에게 진 경우만 길게 다시 기다린다(``lock_table``).
    """
    set_ddl_timeouts(connection, lock_timeout_ms=BRIEF_DDL_LOCK_TIMEOUT_MS if brief else None)
    for table in referenced_tables(connection):
        lock_table(connection, table, "SHARE ROW EXCLUSIVE", brief=brief)
    lock_table(connection, VALUES_TABLE, "ACCESS EXCLUSIVE", brief=brief)
    has_projection = connection.execute(
        text("SELECT to_regclass('weather_current_values') IS NOT NULL")
    ).scalar_one()
    if has_projection:
        mode = "ACCESS EXCLUSIVE" if drop_foreign_keys else "SHARE ROW EXCLUSIVE"
        lock_table(connection, "weather_current_values", mode, brief=brief)


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
        detach_partition(connection, name)
        drop_detached_partition(connection, name)
        dropped.append(name)
    return dropped


def expired_partitions(connection: Connection, cutoff: date) -> list[tuple[str, date]]:
    """Dated partitions whose whole range precedes ``cutoff``, oldest first."""
    return [
        (name, lower)
        for name, lower in existing_partitions(connection)
        if lower is not None and lower + timedelta(days=1) <= cutoff
    ]


def detach_partition(connection: Connection, name: str) -> None:
    """Detach one dated partition.  A plain DETACH, not ``CONCURRENTLY``.

    ``DETACH ... CONCURRENTLY`` would take only SHARE UPDATE EXCLUSIVE on the
    parent, which no writer conflicts with -- but PostgreSQL refuses it while
    the table has a DEFAULT partition ("cannot detach partitions concurrently
    when a default partition exists", PostgreSQL 14-16), and this table has
    one by design: DEFAULT's floor is what lets a partition be created without
    scanning it, and what the ingest's expired-replay skip reads.  So the
    detach takes ACCESS EXCLUSIVE on the parent and the caller keeps that
    window to one partition, after locking in the ingest's order.
    """
    connection.execute(text(f"ALTER TABLE {VALUES_TABLE} DETACH PARTITION {name}"))


def lock_for_detached_drop(connection: Connection, *, brief: bool = False) -> None:
    """Dropping a detached partition removes the foreign-key triggers DETACH
    gave it on the referenced tables, under ACCESS EXCLUSIVE on each: take
    them up front, in the ingest's order, with the DDL timeouts.

    ``brief``는 ``lock_for_partition_ddl``과 같다.
    """
    set_ddl_timeouts(connection, lock_timeout_ms=BRIEF_DDL_LOCK_TIMEOUT_MS if brief else None)
    for table in referenced_tables(connection):
        lock_table(connection, table, "ACCESS EXCLUSIVE", brief=brief)


def drop_detached_partition(connection: Connection, name: str) -> None:
    connection.execute(text(f"DROP TABLE {name}"))


#: The old DEFAULT between the operator swap's two transactions
#: (``default_partition.purge_execute``): detached and renamed in the first,
#: dropped in the second.  Found again by this name when the second one did
#: not commit; until then its facts still cite source records.
DETACHED_DEFAULT = f"{DEFAULT_PARTITION}_detached"


def detached_default_present(connection: Connection) -> bool:
    return (
        connection.execute(
            text(
                "SELECT 1 FROM pg_class WHERE relname = :name AND relkind = 'r' "
                "AND NOT relispartition AND relnamespace = current_schema()::regnamespace"
            ),
            {"name": DETACHED_DEFAULT},
        ).first()
        is not None
    )


_DATED_NAME = rf"^{VALUES_TABLE}_[0-9]{{8}}$"


def detached_leftovers(connection: Connection) -> list[tuple[str, date]]:
    """Dated tables no longer attached: a drop that lost its lock race.

    Retention detaches and drops in separate transactions, so a run can stop
    in between.  The table is then invisible to ``existing_partitions``; this
    is how the next run finds it.
    """
    rows = connection.execute(
        text(
            "SELECT c.relname FROM pg_class c "
            "WHERE c.relkind = 'r' AND NOT c.relispartition "
            "AND c.relnamespace = current_schema()::regnamespace "
            "AND c.relname ~ :pattern ORDER BY 1"
        ),
        {"pattern": _DATED_NAME},
    ).scalars().all()
    leftovers: list[tuple[str, date]] = []
    for name in rows:
        try:
            day = datetime.strptime(str(name)[len(VALUES_TABLE) + 1 :], "%Y%m%d").date()
        except ValueError:
            continue
        leftovers.append((str(name), day))
    return leftovers


def other_sessions_locking(connection: Connection, tables: tuple[str, ...]) -> int:
    """How many other sessions hold or await a lock on any of ``tables``.

    A partition-DDL attempt made while this is non-zero queues behind those
    sessions and makes every newcomer queue behind it for up to a lock
    timeout; made while it is zero, it usually just succeeds.
    """
    return int(
        connection.execute(
            text(
                "SELECT count(DISTINCT l.pid) FROM pg_locks l "
                "WHERE l.locktype = 'relation' AND l.pid <> pg_backend_pid() "
                "AND l.relation = ANY ("
                "  SELECT to_regclass(t) FROM unnest(CAST(:tables AS text[])) t)"
            ),
            {"tables": list(tables)},
        ).scalar_one()
    )


def oldest_partition_age_days(connection: Connection, *, today: date) -> int | None:
    """Whole days from the oldest dated partition's first day to ``today``.

    The retention staleness signal.  Right after a nightly drop it equals
    the retention window; it grows by one for every night nothing is dropped.
    ``None`` when there is no dated partition.
    """
    days = [day for _, day in existing_partitions(connection) if day is not None]
    # A detached table whose drop keeps losing still holds the day's rows and
    # the space: it counts, or a drop failing every night would look healthy.
    days += [day for _, day in detached_leftovers(connection)]
    if not days:
        return None
    return (today - min(days)).days


#: Retention's mark on a day that was due and not done: tried and lost, or
#: skipped by the circuit breaker.  Kept on the relation itself (a table
#: comment): it survives the detach, goes with the drop, and needs no table
#: of its own.  ``partitions_overdue`` is "missed tonight and marked by an
#: earlier run"; a day held back behind a waiting drop is not marked.
DEFERRAL_MARK = "kortravelweather retention: deferred"


def deferral_marked(connection: Connection, name: str) -> bool | None:
    """Whether an earlier run marked ``name``; ``None`` if the table is gone.

    A catalog read: no lock on the table.
    """
    row = connection.execute(
        text(
            "SELECT to_regclass(:t) IS NOT NULL, obj_description(to_regclass(:t), 'pg_class')"
        ),
        {"t": name},
    ).one()
    if not row[0]:
        return None
    return bool(row[1]) and str(row[1]).startswith(DEFERRAL_MARK)


def write_deferral_mark(connection: Connection, name: str, *, lock_timeout_ms: int) -> None:
    """Mark ``name`` deferred.

    ``COMMENT`` takes SHARE UPDATE EXCLUSIVE on the table -- free on an
    expired day nobody writes to, but autovacuum holds the same lock, so it
    can lose; the caller retries and records a mark that never lands.
    """
    connection.execute(text(f"SET LOCAL lock_timeout = '{int(lock_timeout_ms)}ms'"))
    connection.execute(text(f"COMMENT ON TABLE {name} IS '{DEFERRAL_MARK}'"))


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


#: The end-of-run VALIDATE reads the whole projection.  Past this it is
#: cancelled and the key stays ``NOT VALID`` (new rows are still checked);
#: the next run validates it.
VALIDATE_STATEMENT_TIMEOUT = "10min"


def validate_foreign_keys_into_facts(connection: Connection) -> list[str]:
    """Validate what ``readd_foreign_keys_not_valid`` left unvalidated.

    ``VALIDATE CONSTRAINT`` takes SHARE UPDATE EXCLUSIVE on the referencing
    table and ROW SHARE on the fact table: readers and writers carry on while
    it reads the projection once.  Raises the foreign-key violation if a row
    points at a fact that is gone (see ``delete_dangling_projection_rows``).
    """
    connection.execute(text(f"SET LOCAL lock_timeout = '{ddl_lock_timeout_ms(connection)}ms'"))
    connection.execute(text(f"SET LOCAL statement_timeout = '{VALIDATE_STATEMENT_TIMEOUT}'"))
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
