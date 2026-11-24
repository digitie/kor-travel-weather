"""실제 PostgreSQL에서 GC 대기·원래 TX·호출자 timeout/cancel 경계를 확인한다."""

import asyncio
import hashlib
import time
from contextlib import contextmanager
from threading import Event, Thread

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from test_export_database_artifact_gc import (
    NOW,
    _slow_real_gc,
    present,
    put,
)
from test_export_database_artifact_gc import (
    postgres_gc_repo as postgres_gc_repo,
)

from kortravelweather.export_artifacts import ExportGuardTimeout, PreparedExportChunk
from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore
from kortravelweather.repository import Base


def chunk(number):
    payload = ('{"n":' + str(number) + '}\n').encode()
    return PreparedExportChunk(payload, hashlib.sha256(payload).hexdigest(), 1, NOW)


def native_location(session, name):
    session.execute(Base.metadata.tables["weather_locations"].insert().values(
        location_id=name, name=name, latitude=37.5, longitude=127,
        enabled=True, metadata={}, created_at=NOW, updated_at=NOW,
    ))


def native_present(repository, name):
    table = Base.metadata.tables["weather_locations"]
    with repository.engine.connect() as connection:
        return connection.scalar(select(table.c.location_id).where(table.c.location_id == name))


@contextmanager
def foreign_guard(repository):
    """소유 thread만 guard를 해제한다. writer가 타 backend를 취소하지 않는다."""
    entered, release = Event(), Event()
    errors = []

    def hold():
        try:
            with DatabaseExportArtifactStore(repository).guard(holder="local-foreign-holder"):
                entered.set()
                if not release.wait(9):
                    raise AssertionError("시험 소유자가 holder를 해제해야 한다")
        except Exception as error:
            errors.append(error)

    owner = Thread(target=hold)
    owner.start()
    try:
        assert entered.wait(3)
        yield owner
    finally:
        release.set()
        owner.join(3)
        assert not owner.is_alive()
        assert not errors, [type(error).__name__ for error in errors]


def test_default_delete_capture_waits_for_nonexpired_reference_and_real_delete(
    postgres_gc_repo, monkeypatch,
):
    repository, pinned, _pins = postgres_gc_repo
    stale, _ = put(repository, 72)
    item = chunk(72)
    assert item.digest == stale
    store = DatabaseExportArtifactStore(repository)
    started = time.monotonic()
    # delete GC의 첫 참조 판정은 공유 guard라 capture와 함께 간다. capture는 지울 대상을
    # 배타 guard 안에서 다시 판정하고 지우는 구간만 기다린다. 그 구간에 capture를 시작한다.
    with (
        _slow_real_gc(
            repository, monkeypatch, "delete", delay=2.7, exclusive_only=True,
        ) as (reports, _finished),
        repository._session_factory.begin() as session,
    ):
        native_location(session, "committed-native")
        store.record_in_transaction(session, item)
    assert time.monotonic() - started >= 2.7
    assert reports[0]["deleted"] == 1 and reports[0]["live"] == 1
    # GC가 먼저 지운 동일 digest를 같은 bytes로 다시 저장한다.
    assert {pinned, stale} <= present(repository)
    assert native_present(repository, "committed-native") == "committed-native"
    assert list(store.read(stale, byte_count=len(item.payload), point_count=1)) == [{"n": 72}]


def test_guard_expiry_is_finite_and_does_not_abort_outer_native_transaction(postgres_gc_repo):
    repository, pinned, _pins = postgres_gc_repo
    item = chunk(73)
    store = DatabaseExportArtifactStore(repository)
    with foreign_guard(repository) as owner:
        started = time.monotonic()
        with repository._session_factory.begin() as session:
            native_location(session, "before-timeout")
            with pytest.raises(ExportGuardTimeout):
                store.record_in_transaction(session, item)
            assert time.monotonic() - started >= 4.9
            assert owner.is_alive(), "다른 소유자의 guard를 취소하면 안 된다"
            assert session.scalar(text("SELECT 1")) == 1
            native_location(session, "after-timeout")
    assert item.digest not in present(repository)
    assert pinned in present(repository)
    assert native_present(repository, "before-timeout") == "before-timeout"
    assert native_present(repository, "after-timeout") == "after-timeout"


def test_record_success_remains_atomic_and_guard_survives_until_caller_rollback(postgres_gc_repo):
    repository, pinned, _pins = postgres_gc_repo
    item = chunk(74)
    store = DatabaseExportArtifactStore(repository)
    with (
        pytest.raises(RuntimeError, match="caller rollback"),
        repository._session_factory.begin() as session,
    ):
        native_location(session, "rolled-back-native")
        store.record_in_transaction(session, item)
        assert item.digest not in present(repository), (
            "다른 TX에는 미완료 artifact가 보이지 않는다"
        )
        with (
            pytest.raises(ExportGuardTimeout),
            store.guard(timeout_seconds=.05, holder="other-delete"),
        ):
            pytest.fail("outer commit 전에 shared guard가 풀리면 안 된다")
        raise RuntimeError("caller rollback")
    assert item.digest not in present(repository)
    assert native_present(repository, "rolled-back-native") is None
    assert pinned in present(repository)


def test_caller_statement_timeout_keeps_original_sqlstate_and_rolls_back(postgres_gc_repo):
    repository, pinned, _pins = postgres_gc_repo
    item = chunk(75)
    store = DatabaseExportArtifactStore(repository)
    with (
        pytest.raises(DBAPIError) as raised,
        repository._session_factory.begin() as session,
    ):
        native_location(session, "statement-timeout-native")
        session.execute(text("SET LOCAL statement_timeout = '25ms'"))
        connection = session.connection()

        def slow_advisory(_c, _cur, statement, parameters, _ctx, many):
            if "pg_try_advisory_xact_lock_shared" in statement:
                statement += " FROM (SELECT pg_sleep(.1)) AS local_delay"
            return statement, parameters

        event.listen(connection, "before_cursor_execute", slow_advisory, retval=True)
        try:
            store.record_in_transaction(session, item)
        finally:
            event.remove(connection, "before_cursor_execute", slow_advisory)
    assert raised.value.orig.sqlstate == "57014"
    assert not isinstance(raised.value, ExportGuardTimeout)
    assert item.digest not in present(repository)
    assert native_present(repository, "statement-timeout-native") is None
    assert pinned in present(repository)


def test_caller_cancellation_during_contention_is_not_swallowed(postgres_gc_repo):
    repository, pinned, _pins = postgres_gc_repo
    item = chunk(76)
    store = DatabaseExportArtifactStore(repository)
    cancellation = asyncio.CancelledError("local caller cancellation")
    attempts = 0
    with (
        foreign_guard(repository) as owner,
        pytest.raises(asyncio.CancelledError) as raised,
        repository._session_factory.begin() as session,
    ):
        native_location(session, "cancelled-native")
        connection = session.connection()

        def cancel_wait(_c, _cur, statement, _parameters, _ctx, _many):
            nonlocal attempts
            if "pg_try_advisory_xact_lock_shared" in statement:
                attempts += 1
                if attempts == 2:
                    assert owner.is_alive()
                    raise cancellation

        event.listen(connection, "before_cursor_execute", cancel_wait)
        try:
            store.record_in_transaction(session, item)
        finally:
            event.remove(connection, "before_cursor_execute", cancel_wait)
    assert raised.value is cancellation and attempts == 2
    assert item.digest not in present(repository)
    assert native_present(repository, "cancelled-native") is None
    assert pinned in present(repository)
