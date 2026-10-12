"""The operator's DEFAULT swap: two commits, every lock up front, none upgraded.

``scripts/weather_values_purge_default.py --execute`` used to detach *and drop*
DEFAULT in one transaction.  It took SHARE ROW EXCLUSIVE on the fact table's
referenced tables up front, but the DROP then asked for ACCESS EXCLUSIVE on
them mid-transaction: an ingest that had read ``weather_source_records``
(ACCESS SHARE) and was waiting to insert into it closed a cycle, and the
deadlock check killed whichever had waited first -- the ingest.  The swap is
now split like the nightly job's detach and drop (2026-10-13 review).  These
tests run against real PostgreSQL.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import Connection, event, text
from sqlalchemy.exc import OperationalError

import kortravelweather.default_partition as operator_steps
from alembic import command
from kortravelweather.default_partition import purge_execute
from kortravelweather.models import kst_now
from kortravelweather.partitions import (
    DEFAULT_PARTITION,
    DETACHED_DEFAULT,
    default_partition_floor,
)
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import get_settings

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)

#: PostgreSQL's table lock modes, weakest first.
_STRENGTH = [
    "AccessShareLock",
    "RowShareLock",
    "RowExclusiveLock",
    "ShareUpdateExclusiveLock",
    "ShareLock",
    "ShareRowExclusiveLock",
    "ExclusiveLock",
    "AccessExclusiveLock",
]


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch) -> Iterator[WeatherRepository]:
    """A head schema whose DEFAULT holds one fact, wholly past a 1-day retention.

    The migration's floor is yesterday 00:00 KST, so with ``retention_days=1``
    the cutoff is past it and ``purge_execute`` takes the swap branch.
    """
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()
    repo = WeatherRepository(TEST_DATABASE_URL)
    with repo.engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA public CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA public")
    command.upgrade(Config("alembic.ini"), "head")
    old = kst_now() - timedelta(days=30)
    with repo.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO weather_locations (location_id, name, latitude, longitude, "
                "created_at, updated_at) VALUES ('swap', 'swap', 37, 127, now(), now())"
            )
        )
        connection.execute(
            text(
                "INSERT INTO weather_source_records (source_record_key, provider, "
                "dataset_key, source_entity_type, source_entity_id, raw_payload_hash, "
                "payload, fetched_at, imported_at) VALUES ('old-source', 'p', 'd', "
                "'weather_response', 'swap', 'old-source', '{}', :at, :at)"
            ),
            {"at": old},
        )
        connection.execute(
            text(
                "INSERT INTO weather_values (value_id, location_id, provider, dataset_key, "
                "weather_domain, forecast_style, metric_key, target_at, known_at, "
                "normalization_version, payload, collected_at, source_record_key, "
                "value_number) VALUES ('old-fact', 'swap', 'p', 'd', 'weather', 'short', "
                "'TMP', :at, :at, 'test', '{}', :at, 'old-source', 1)"
            ),
            {"at": old},
        )
    assert _rows(repo, DEFAULT_PARTITION) == 1
    yield repo
    get_settings.cache_clear()


def _rows(repository: WeatherRepository, table: str) -> int:
    with repository.engine.connect() as connection:
        return int(connection.execute(text(f"SELECT count(*) FROM ONLY {table}")).scalar_one())


def _table_locks(connection: Connection) -> dict[int, str]:
    """The strongest lock this transaction holds on each table, by oid."""
    held: dict[int, str] = {}
    for oid, mode in connection.execute(
        text(
            "SELECT l.relation::int, l.mode FROM pg_locks l "
            "JOIN pg_class c ON c.oid = l.relation "
            "WHERE l.pid = pg_backend_pid() AND l.locktype = 'relation' AND l.granted "
            "AND c.relkind IN ('r', 'p') "
            "AND c.relnamespace = current_schema()::regnamespace"
        )
    ).all():
        if oid not in held or _STRENGTH.index(mode) > _STRENGTH.index(held[oid]):
            held[oid] = mode
    return held


def _upgrades(
    start: dict[int, str], end: dict[int, str], existing: set[int]
) -> list[tuple[int, str | None, str]]:
    """Locks on pre-existing tables taken or strengthened after the up-front phase."""
    return [
        (oid, start.get(oid), mode)
        for oid, mode in end.items()
        if oid in existing
        and (oid not in start or _STRENGTH.index(mode) > _STRENGTH.index(start[oid]))
    ]


def _existing_tables(repository: WeatherRepository) -> set[int]:
    with repository.engine.connect() as connection:
        return {
            int(oid)
            for oid in connection.execute(
                text(
                    "SELECT oid::int FROM pg_class WHERE relkind IN ('r', 'p') "
                    "AND relnamespace = current_schema()::regnamespace"
                )
            ).scalars()
        }


def test_the_upgrade_detector_sees_an_upgrade(repository: WeatherRepository) -> None:
    """Negative control: the check below must fail on what the old swap did."""
    existing = _existing_tables(repository)
    with repository.engine.begin() as connection:
        connection.execute(text("LOCK TABLE weather_source_records IN SHARE ROW EXCLUSIVE MODE"))
        start = _table_locks(connection)
        connection.execute(text("LOCK TABLE weather_source_records IN ACCESS EXCLUSIVE MODE"))
        end = _table_locks(connection)
    assert [mode for _, _, mode in _upgrades(start, end, existing)] == ["AccessExclusiveLock"]


def test_the_swap_commits_twice_and_takes_every_lock_up_front_in_ingest_order(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = _existing_tables(repository)
    log: list[tuple[int, str, str]] = []

    def on_begin(connection: Connection) -> None:
        log.append((id(connection), "begin", ""))

    def on_commit(connection: Connection) -> None:
        log.append((id(connection), "commit", ""))

    def on_execute(connection: Connection, _cursor: Any, statement: str, *_: Any) -> None:
        log.append((id(connection), "sql", " ".join(statement.split())))

    snapshots: dict[str, dict[int, str]] = {}

    def snapshot_after(name: str, original: Any) -> Any:
        def wrapped(connection: Connection, *args: Any, **kwargs: Any) -> Any:
            result = original(connection, *args, **kwargs)
            snapshots[name] = _table_locks(connection)
            return result

        return wrapped

    for name in (
        "lock_for_partition_ddl",
        "readd_foreign_keys_not_valid",
        "lock_for_old_default_drop",
        "drop_detached_partition",
    ):
        monkeypatch.setattr(
            operator_steps, name, snapshot_after(name, getattr(operator_steps, name))
        )
    engine = repository.engine
    event.listen(engine, "begin", on_begin)
    event.listen(engine, "commit", on_commit)
    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        purge_execute(engine, retention_days=1, log=lambda _: None)
    finally:
        event.remove(engine, "begin", on_begin)
        event.remove(engine, "commit", on_commit)
        event.remove(engine, "before_cursor_execute", on_execute)

    # Group the statements into committed transactions, in commit order.
    open_tx: dict[int, list[str]] = {}
    committed: list[list[str]] = []
    for connection_id, kind, statement in log:
        if kind == "begin":
            open_tx[connection_id] = []
        elif kind == "commit":
            committed.append(open_tx.pop(connection_id, []))
        elif connection_id in open_tx:
            open_tx[connection_id].append(statement)

    def tx_with(fragment: str) -> list[list[str]]:
        return [tx for tx in committed if any(fragment in sql for sql in tx)]

    swaps = tx_with(f"DETACH PARTITION {DEFAULT_PARTITION}")
    drops = tx_with(f"DROP TABLE {DETACHED_DEFAULT}")
    assert len(swaps) == 1 and len(drops) == 1, committed
    swap, drop = swaps[0], drops[0]
    assert swap is not drop, "detach and drop must commit separately"
    assert committed.index(swap) < committed.index(drop)
    assert not any(sql.startswith("DROP TABLE") for sql in swap)

    def locks(tx: list[str]) -> list[str]:
        return [sql for sql in tx if sql.startswith("LOCK TABLE")]

    assert locks(swap) == [
        "LOCK TABLE weather_source_records IN SHARE ROW EXCLUSIVE MODE",
        "LOCK TABLE weather_locations IN SHARE ROW EXCLUSIVE MODE",
        "LOCK TABLE weather_values IN ACCESS EXCLUSIVE MODE",
        "LOCK TABLE weather_current_values IN ACCESS EXCLUSIVE MODE",
    ]
    assert locks(drop) == [
        "LOCK TABLE weather_source_records IN ACCESS EXCLUSIVE MODE",
        "LOCK TABLE weather_locations IN ACCESS EXCLUSIVE MODE",
        f"LOCK TABLE {DETACHED_DEFAULT} IN ACCESS EXCLUSIVE MODE",
    ]
    for tx in (swap, drop):
        ddl = [i for i, sql in enumerate(tx) if sql.startswith(("ALTER", "CREATE", "DROP"))]
        last_lock = max(i for i, sql in enumerate(tx) if sql.startswith("LOCK TABLE"))
        assert ddl and last_lock < min(ddl), tx

    # And what PostgreSQL actually holds: nothing taken or strengthened on a
    # pre-existing table after the up-front locks, in either transaction.
    assert _upgrades(
        snapshots["lock_for_partition_ddl"], snapshots["readd_foreign_keys_not_valid"], existing
    ) == []
    assert _upgrades(
        snapshots["lock_for_old_default_drop"], snapshots["drop_detached_partition"], existing
    ) == []

    assert _rows(repository, DEFAULT_PARTITION) == 0
    with engine.connect() as connection:
        assert default_partition_floor(connection) is not None
        assert connection.execute(
            text("SELECT to_regclass(:t)"), {"t": DETACHED_DEFAULT}
        ).scalar_one() is None
        rows = connection.execute(
            text(
                "SELECT convalidated FROM pg_constraint WHERE contype = 'f' "
                "AND conrelid = 'weather_current_values'::regclass "
                "AND confrelid = 'weather_values'::regclass"
            )
        ).scalars().all()
    assert rows and all(rows)


def test_an_ingest_holding_source_records_is_not_deadlocked_by_the_swap(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact interleaving the one-transaction swap lost.

    An ingest has read ``weather_source_records`` (ACCESS SHARE) and, while
    the swap holds the fact table, asks to insert into it.  It waits for the
    swap's first transaction, which needs nothing more from it and commits;
    the drop then waits for the ingest.  Before the split the DROP asked for
    ACCESS EXCLUSIVE on ``weather_source_records`` mid-transaction and the
    deadlock check killed the ingest.
    """
    engine = repository.engine
    in_swap = threading.Event()
    ingest_pid: list[int] = []

    def ingest_waiting() -> bool:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = :pid AND NOT granted)"),
                    {"pid": ingest_pid[0]},
                ).scalar_one()
            )

    original = operator_steps.drop_foreign_keys_into_facts

    def hold_until_the_ingest_waits(connection: Connection) -> Any:
        in_swap.set()
        deadline = time.monotonic() + 20
        while not ingest_waiting():
            assert time.monotonic() < deadline, "the ingest never queued behind the swap"
            time.sleep(0.05)
        return original(connection)

    monkeypatch.setattr(operator_steps, "drop_foreign_keys_into_facts", hold_until_the_ingest_waits)
    failures: list[BaseException] = []

    def swap() -> None:
        try:
            purge_execute(engine, retention_days=1, log=lambda _: None)
        except BaseException as exc:  # noqa: BLE001 - reported by the main thread
            failures.append(exc)

    ingest = engine.connect()
    try:
        ingest_pid.append(int(ingest.execute(text("SELECT pg_backend_pid()")).scalar_one()))
        ingest.execute(text("SELECT count(*) FROM weather_source_records")).scalar_one()
        worker = threading.Thread(target=swap)
        worker.start()
        assert in_swap.wait(60), failures
        now = kst_now()
        ingest.execute(
            text(
                "INSERT INTO weather_source_records (source_record_key, provider, "
                "dataset_key, source_entity_type, source_entity_id, raw_payload_hash, "
                "payload, fetched_at, imported_at) VALUES ('new-source', 'p', 'd', "
                "'weather_response', 'swap', 'new-source', '{}', :at, :at)"
            ),
            {"at": now},
        )
        ingest.commit()
    except OperationalError as exc:
        pytest.fail(f"the ingest lost to the swap: {exc}")
    finally:
        ingest.close()
        worker.join(120)
    assert not failures, failures
    assert _rows(repository, DEFAULT_PARTITION) == 0
    assert _rows(repository, "weather_source_records") == 2


def test_an_interrupted_swap_holds_its_sources_and_the_rerun_drops_it(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Between the two commits the old DEFAULT is a detached table.

    Its facts still cite their source records (ON DELETE RESTRICT), so the
    nightly source purge must leave them, or the whole nightly run fails.  A
    re-run of the script finds the table and only drops it.
    """
    def lost(_connection: Connection) -> bool:
        raise RuntimeError("killed between the commits")

    original = operator_steps.drop_old_default
    monkeypatch.setattr(operator_steps, "drop_old_default", lost)
    with pytest.raises(RuntimeError, match="between the commits"):
        purge_execute(repository.engine, retention_days=1, log=lambda _: None)
    monkeypatch.setattr(operator_steps, "drop_old_default", original)

    assert _rows(repository, DEFAULT_PARTITION) == 0
    assert _rows(repository, DETACHED_DEFAULT) == 1
    with repository.engine.connect() as connection:
        assert default_partition_floor(connection) is not None
        assert not connection.execute(
            text("SELECT relispartition FROM pg_class WHERE relname = :t"),
            {"t": DETACHED_DEFAULT},
        ).scalar_one()

    report = repository.purge_expired_history(retention_days=1, ahead_days=7)
    assert report.sources_deleted == 0
    assert _rows(repository, "weather_source_records") == 1

    lines: list[str] = []
    purge_execute(repository.engine, retention_days=1, log=lines.append)
    assert any("interrupted swap" in line for line in lines)
    with repository.engine.connect() as connection:
        assert connection.execute(
            text("SELECT to_regclass(:t)"), {"t": DETACHED_DEFAULT}
        ).scalar_one() is None
    # The rerun only finished the drop: the new DEFAULT was not swapped again.
    assert not any("detached the old DEFAULT" in line for line in lines)
    report = repository.purge_expired_history(retention_days=1, ahead_days=7)
    assert report.sources_deleted == 1
