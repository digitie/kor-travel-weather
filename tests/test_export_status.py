from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from queue import Queue
from time import monotonic, sleep

import pytest
from sqlalchemy import MetaData, create_engine, event, select, text

from kortravelweather.export_status import ExportSourceStatuses, SourceKey

NOW = datetime(2026, 10, 23, 9, tzinfo=UTC)
KEY = SourceKey("python-kma-api", "kma_short_forecast", "short")


@pytest.fixture
def store(weather_test_database_url):
    engine = create_engine(weather_test_database_url)
    statuses = ExportSourceStatuses(MetaData())
    statuses.table.create(engine, checkfirst=True)
    with engine.begin() as connection:
        connection.execute(statuses.table.delete())
    yield engine, statuses
    engine.dispose()


def terminal(connection, statuses, *, key=KEY, offset=0, status="success", run_id=None):
    started = NOW + timedelta(minutes=offset)
    return statuses.record_terminal(
        connection, key, run_id=run_id or f"run-{offset}", started_at=started,
        finished_at=started + timedelta(seconds=20), status=status,
        source_collected_at=started + timedelta(seconds=5) if status == "success" else None,
    )


def test_failure_preserves_success_and_late_worker_cannot_overwrite_it(store):
    engine, statuses = store
    with engine.begin() as connection:
        assert terminal(connection, statuses)
        assert terminal(connection, statuses, offset=2, status="partial")
        assert not terminal(connection, statuses, offset=1, status="failed")
        row = statuses.read(connection, (KEY,), as_of=NOW)[0]
        assert row.last_status == "partial" and row.last_attempt_failed
        assert row.last_success_at == NOW + timedelta(seconds=20)
        assert row.source_collected_at == NOW + timedelta(seconds=5)
        assert row.last_failed_at == NOW + timedelta(minutes=2, seconds=20)
        assert terminal(connection, statuses, offset=3)
        assert not terminal(connection, statuses, offset=2, status="failed")
        row = statuses.read(connection, (KEY,), as_of=NOW)[0]
        assert not row.last_attempt_failed and row.last_run_id == "run-3"
        assert row.last_failed_at == NOW + timedelta(minutes=2, seconds=20)


def test_terminal_replay_is_noop_and_dataset_scopes_do_not_mix(store):
    engine, statuses = store
    other = SourceKey(KEY.provider, "other_short_source", KEY.forecast_style)
    with engine.begin() as connection:
        assert terminal(connection, statuses)
        assert not terminal(connection, statuses)
        assert not terminal(connection, statuses, status="failed")
        assert terminal(connection, statuses, key=other, status="failed")
        rows = {r.key: r for r in statuses.read(connection, (other, KEY), as_of=NOW)}
        assert rows[KEY].last_status == "success"
        assert rows[other].last_status == "failed" and rows[other].last_success_at is None


def test_schedule_clock_is_independent_and_expired_schedule_is_unknown(store):
    engine, statuses = store
    with engine.begin() as connection:
        assert statuses.record_schedule(
            connection, KEY, evaluated_at=NOW, next_scheduled_at=NOW + timedelta(hours=1)
        )
        assert terminal(connection, statuses)
        assert not statuses.record_schedule(
            connection, KEY, evaluated_at=NOW - timedelta(seconds=1), next_scheduled_at=None
        )
        assert statuses.read(connection, (KEY,), as_of=NOW)[0].next_scheduled_at == (
            NOW + timedelta(hours=1)
        )
        expired = statuses.read(connection, (KEY,), as_of=NOW + timedelta(hours=1))[0]
        assert expired.next_scheduled_at is None
        assert statuses.record_schedule(
            connection, KEY, evaluated_at=NOW + timedelta(seconds=1), next_scheduled_at=None
        )
        assert statuses.read(connection, (KEY,), as_of=NOW)[0].last_status == "success"
        assert statuses.read(connection, (KEY,), as_of=NOW)[0].next_scheduled_at is None


def test_missing_scope_remains_explicit_and_read_is_one_bounded_select(store):
    engine, statuses = store
    missing = SourceKey("unknown-provider", "unknown-source", "observed")
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with engine.begin() as connection:
        terminal(connection, statuses)
        event.listen(engine, "before_cursor_execute", capture)
        try:
            rows = statuses.read(connection, (KEY, missing), as_of=NOW)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) == 1
    assert "weather_export_source_status" in statements[0]
    assert "weather_values" not in statements[0] and "weather_sync_runs" not in statements[0]
    assert len(rows) == 2 and rows[1].last_status is None


def test_terminal_rollback_does_not_leave_false_success(store):
    engine, statuses = store
    with engine.connect() as connection:
        transaction = connection.begin()
        assert terminal(connection, statuses)
        transaction.rollback()
        assert connection.scalar(select(statuses.table.c.provider)) is None


def test_delayed_failure_waiting_for_new_success_cannot_replace_it(store):
    engine, statuses = store
    backend_ids = Queue()

    def older_failure():
        with engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            backend_ids.put(connection.scalar(text("SELECT pg_backend_pid()")))
            return terminal(connection, statuses, offset=1, status="failed")

    with ThreadPoolExecutor(max_workers=1) as executor, engine.begin() as first:
        first_pid = first.scalar(text("SELECT pg_backend_pid()"))
        assert terminal(first, statuses, offset=2)
        future = executor.submit(older_failure)
        second_pid = backend_ids.get(timeout=3)
        blocked = False
        try:
            with engine.connect() as observer:
                deadline = monotonic() + 3
                while monotonic() < deadline:
                    blockers = observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {
                        "pid": second_pid,
                    })
                    if first_pid in blockers:
                        blocked = True
                        break
                    sleep(0.01)
            assert blocked
        finally:
            first.commit()
        assert future.result(timeout=3) is False
    with engine.connect() as connection:
        state = statuses.read(connection, (KEY,), as_of=NOW)[0]
        assert state.last_run_id == "run-2" and not state.last_attempt_failed
        assert state.last_failed_at is None


@pytest.mark.parametrize("change", [
    {"started_at": NOW.replace(tzinfo=None)},
    {"finished_at": NOW - timedelta(seconds=1)},
    {"source_collected_at": None},
    {"source_collected_at": NOW + timedelta(hours=1)},
    {"status": "running"},
    {"run_id": ""},
])
def test_invalid_terminal_is_rejected_before_sql(store, change):
    engine, statuses = store
    values = dict(
        run_id="r", started_at=NOW, finished_at=NOW + timedelta(seconds=20),
        source_collected_at=NOW, status="success",
    ) | change
    with engine.begin() as connection:
        with pytest.raises(ValueError):
            statuses.record_terminal(connection, KEY, **values)
        assert connection.scalar(select(statuses.table.c.provider)) is None


def test_kst_input_is_preserved_as_utc(store):
    engine, statuses = store
    kst = timezone(timedelta(hours=9))
    with engine.begin() as connection:
        statuses.record_terminal(
            connection, KEY, run_id="kst", started_at=NOW.astimezone(kst),
            finished_at=(NOW + timedelta(seconds=20)).astimezone(kst),
            source_collected_at=NOW.astimezone(kst), status="success",
        )
        assert statuses.read(connection, (KEY,), as_of=NOW)[0].source_collected_at == NOW


def test_scope_and_query_bounds_are_checked(store):
    engine, statuses = store
    with pytest.raises(ValueError):
        SourceKey("p", "d", "alerts")
    with engine.begin() as connection:
        for keys in ((), (KEY, KEY), tuple(SourceKey("p", str(n), "short") for n in range(129))):
            with pytest.raises(ValueError):
                statuses.read(connection, keys, as_of=NOW)
        with pytest.raises(ValueError):
            statuses.record_schedule(connection, KEY, evaluated_at=NOW, next_scheduled_at=NOW)
