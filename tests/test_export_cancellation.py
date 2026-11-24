"""연결이 끊긴 export 요청의 단계·대기·SQL 중단과 같은 키 대기자 보호(호출 검토 A6)."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from kortravelweather import export_cancellation
from kortravelweather.export_cancellation import (
    ExportCancellation,
    ExportRequestCancelled,
    checkpoint,
    install_export_cancellation,
    leading,
    wait_event,
)
from kortravelweather.export_page_cache import ExportPageCache
from kortravelweather.export_selection_cache import ExportSelectionCache


def reader():
    return SimpleNamespace(
        selection_deadline=60, selection_checked_after=0,
        reusable_page_size=lambda: 1, validate_reusable_page=lambda: None,
    )


def run_with(cancellation, function, *args):
    with export_cancellation.export_cancellation(cancellation):
        return function(*args)


def test_non_leader_stops_at_once_and_leader_waits_for_grace_and_waiters():
    clock = [0.0]
    cancellation = ExportCancellation(clock=lambda: clock[0], grace_seconds=15)
    waiters = [True]
    with export_cancellation.export_cancellation(cancellation):
        checkpoint("freeze")  # 끊기기 전에는 아무것도 하지 않는다.
        cancellation.mark_disconnected()
        with leading(lambda: waiters[0]):
            checkpoint("freeze")  # 유예 안
            clock[0] = 20
            checkpoint("freeze")  # 유예가 지났어도 같은 키 대기자가 있다.
            waiters[0] = False
            with pytest.raises(ExportRequestCancelled) as stopped:
                checkpoint("freeze")
        assert stopped.value.stage == "freeze" and cancellation.stopped_stage == "freeze"
        with pytest.raises(ExportRequestCancelled):
            checkpoint("render")  # 리더 구간 밖은 끊김 즉시 멈춘다.


def test_explicit_cancel_stops_even_inside_the_leader_section():
    cancellation = ExportCancellation(grace_seconds=60)
    with export_cancellation.export_cancellation(cancellation), leading(lambda: True):
        cancellation.mark_disconnected()
        checkpoint("freeze")
        cancellation.cancel()
        with pytest.raises(ExportRequestCancelled):
            checkpoint("freeze")


def test_checkpoint_and_wait_are_plain_outside_a_watched_request():
    checkpoint("freeze")
    event = threading.Event()
    started = time.monotonic()
    assert wait_event(event, 0.05, stage="page_wait") is False
    assert time.monotonic() - started >= 0.04
    event.set()
    assert wait_event(event, 5, stage="page_wait") is True


def test_waiting_stops_promptly_after_disconnect():
    cancellation = ExportCancellation()
    never = threading.Event()
    timer = threading.Timer(0.2, cancellation.mark_disconnected)
    timer.start()
    started = time.monotonic()
    with pytest.raises(ExportRequestCancelled) as stopped:
        run_with(cancellation, lambda: wait_event(never, 30, stage="page_wait"))
    assert stopped.value.stage == "page_wait"
    assert time.monotonic() - started < 2


def test_page_waiter_that_disconnects_leaves_without_touching_the_leader():
    cache = ExportPageCache(clock=lambda: 0)
    entered, release = threading.Event(), threading.Event()
    builds = []

    def load():
        builds.append(1)
        entered.set()
        assert release.wait(5)
        return ({"page": 1}, "etag")

    waiter = ExportCancellation()
    with ThreadPoolExecutor(2) as pool:
        leader = pool.submit(cache.read, "a", reader(), load)
        assert entered.wait(5)
        follower = pool.submit(run_with, waiter, cache.read, "a", reader(), load)
        wait_for_waiter(cache, "a")
        waiter.mark_disconnected()
        with pytest.raises(ExportRequestCancelled):
            follower.result(timeout=2)
        assert not cache.has_waiters("a") and not leader.done()
        release.set()
        assert leader.result() == ({"page": 1}, "etag")
    assert builds == [1] and "a" in cache.entries and not cache.inflight


def wait_for_waiter(cache, key):
    deadline = time.monotonic() + 5
    while not cache.has_waiters(key):
        assert time.monotonic() < deadline, "같은 키 대기자가 생기지 않았습니다."
        time.sleep(0.01)


def test_disconnected_leader_without_waiters_stops_after_grace():
    cache = ExportPageCache(clock=lambda: 0)
    entered = threading.Event()
    leader_cancel = ExportCancellation(grace_seconds=0.2)
    checks = []

    def leader_load():
        entered.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            checkpoint("freeze")
            checks.append(time.monotonic())
            time.sleep(0.01)
        pytest.fail("끊긴 리더가 유예 뒤에도 계속했습니다.")

    with ThreadPoolExecutor(1) as pool:
        leader = pool.submit(run_with, leader_cancel, cache.read, "a", reader(), leader_load)
        assert entered.wait(5)
        leader_cancel.mark_disconnected()
        disconnected = time.monotonic()
        with pytest.raises(ExportRequestCancelled):
            leader.result(timeout=5)
    # 유예(0.2초) 동안은 계속했고, 유예 뒤 곧 멈췄다.
    assert any(at > disconnected + 0.1 for at in checks)
    assert max(checks) < disconnected + 1.5
    assert not cache.inflight and not cache.entries


def test_cancelled_leader_hands_the_key_to_the_waiter():
    cache = ExportPageCache(clock=lambda: 0)
    entered, release = threading.Event(), threading.Event()
    leader_cancel = ExportCancellation()

    def leader_load():
        entered.set()
        assert release.wait(5)
        checkpoint("freeze")
        pytest.fail("취소된 리더가 계속했습니다.")

    with ThreadPoolExecutor(2) as pool:
        leader = pool.submit(run_with, leader_cancel, cache.read, "a", reader(), leader_load)
        assert entered.wait(5)
        follower = pool.submit(cache.read, "a", reader(), lambda: ({"own": 1}, "etag"))
        wait_for_waiter(cache, "a")
        # 리더 구간 전에 끊겨 취소가 확정된 요청이다(대기자가 있어도 멈춘다).
        leader_cancel.mark_disconnected()
        leader_cancel.cancel()
        release.set()
        with pytest.raises(ExportRequestCancelled):
            leader.result(timeout=5)
        # 대기자는 503이나 리더의 취소를 받지 않고 스스로 새 리더가 된다.
        assert follower.result(timeout=5) == ({"own": 1}, "etag")
    assert not cache.inflight and "a" in cache.entries and not cache.has_waiters("a")


def test_leader_keeps_building_while_a_same_key_request_waits():
    cache = ExportPageCache(clock=lambda: 0)
    entered, release = threading.Event(), threading.Event()
    leader_cancel = ExportCancellation(grace_seconds=0)
    builds = []

    def load():
        builds.append(1)
        entered.set()
        assert release.wait(5)
        checkpoint("freeze")  # 끊긴 뒤지만 대기자가 있어 계속한다.
        return ({"page": 1}, "etag")

    with ThreadPoolExecutor(2) as pool:
        leader = pool.submit(run_with, leader_cancel, cache.read, "a", reader(), load)
        assert entered.wait(5)
        follower = pool.submit(cache.read, "a", reader(), lambda: pytest.fail("두 번 만들었다"))
        wait_for_waiter(cache, "a")
        leader_cancel.mark_disconnected()
        release.set()
        assert leader.result(timeout=5) == ({"page": 1}, "etag")
        assert follower.result(timeout=5) == ({"page": 1}, "etag")
    assert builds == [1]


def test_leader_keeps_building_while_a_same_key_request_waits_for_a_slot():
    cache = ExportPageCache(clock=lambda: 0)
    leader_cancel = ExportCancellation(grace_seconds=0)
    queued = ExportCancellation()
    kept = []

    def load():
        leader_cancel.mark_disconnected()
        checkpoint("freeze")  # 유예가 없어도 export 슬롯을 기다리는 같은 키 요청이 있다.
        kept.append(1)
        queued.mark_disconnected()  # 그 요청의 요청자도 떠나면 더 기다릴 이유가 없다.
        checkpoint("freeze")
        pytest.fail("기다릴 요청이 없는데 끊긴 리더가 계속했습니다.")

    with cache.arriving("a", queued):
        with pytest.raises(ExportRequestCancelled):
            run_with(leader_cancel, cache.read, "a", reader(), load)
        assert kept == [1]
    assert not cache.arrivals and not cache.inflight and not cache.entries


def test_arrival_leaves_on_read_and_never_counts_the_request_itself():
    cache = ExportPageCache(clock=lambda: 0)
    me = ExportCancellation(grace_seconds=0)
    seen = []

    def load():
        seen.append(cache.has_waiters("a"))
        return ({"page": 1}, "etag")

    with cache.arriving("a", me):
        assert cache.has_waiters("a")  # 슬롯 대기 중에는 같은 키 관심으로 센다.
        run_with(me, cache.read, "a", reader(), load)
        assert not cache.arrivals  # read에 들어오면 도착 등록에서 빠진다.
    assert seen == [False]


def test_cancelled_hit_validation_keeps_the_valid_entry():
    cache = ExportPageCache(clock=lambda: 0)
    old = reader()
    cache.read("a", old, lambda: ({"page": 1}, "etag"))
    gone = ExportCancellation()
    gone.mark_disconnected()
    old.validate_reusable_page = lambda: checkpoint("metadata")
    with pytest.raises(ExportRequestCancelled):
        run_with(gone, cache.read, "a", reader(), lambda: pytest.fail("다시 만들었다"))
    assert "a" in cache.entries


def test_cancelled_selection_validation_keeps_the_shared_selection():
    cache = ExportSelectionCache(clock=lambda: 0)
    result = SimpleNamespace(expired=(), scopes=())
    cache.read("k", lambda: result, checked_after=0)
    gone = ExportCancellation()
    gone.mark_disconnected()

    def validate(_selection):
        checkpoint("metadata")

    with pytest.raises(ExportRequestCancelled):
        run_with(gone, lambda: cache.read(
            "k", lambda: pytest.fail("다시 읽었다"), checked_after=0, validate=validate,
        ))
    assert "k" in cache.entries


def test_selection_waiter_stops_on_disconnect():
    cache = ExportSelectionCache(clock=time.monotonic)
    entered, release = threading.Event(), threading.Event()

    def slow():
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(expired=(), scopes=())

    gone = ExportCancellation()
    with ThreadPoolExecutor(2) as pool:
        owner = pool.submit(cache.read, "k", slow, checked_after=time.monotonic())
        assert entered.wait(5)
        waiter = pool.submit(run_with, gone, lambda: cache.read(
            "k", slow, checked_after=time.monotonic(),
        ))
        time.sleep(0.1)
        gone.mark_disconnected()
        with pytest.raises(ExportRequestCancelled):
            waiter.result(timeout=2)
        release.set()
        owner.result(timeout=5)


def test_sql_hook_blocks_next_statement_and_cancels_running_one(weather_test_database_url):
    from sqlalchemy import text

    engine = create_engine(weather_test_database_url, pool_size=1, max_overflow=0)
    install_export_cancellation(engine)
    install_export_cancellation(engine)  # 두 번 설치해도 hook은 하나다.
    try:
        gone = ExportCancellation()
        gone.mark_disconnected()

        def one():
            with engine.connect() as connection, connection.begin():
                connection.execute(text("select 1"))

        with pytest.raises(ExportRequestCancelled):
            run_with(gone, one)
        assert engine.pool.checkedout() == 0

        running = ExportCancellation()
        pids, outcome = [], []

        def sleep():
            with engine.connect() as connection, connection.begin():
                pids.append(connection.exec_driver_sql("select pg_backend_pid()").scalar())
                connection.exec_driver_sql("select pg_sleep(30)")

        def body():
            try:
                run_with(running, sleep)
            except ExportRequestCancelled as exc:
                outcome.append(exc)

        started = time.monotonic()
        worker = threading.Thread(target=body)
        worker.start()
        deadline = time.monotonic() + 5
        while running._running is None or not pids:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        time.sleep(0.2)
        running.mark_disconnected()
        running.cancel()
        worker.join(10)
        assert not worker.is_alive()
        assert outcome and outcome[0].stage == "sql" and running.sql_cancel_requests == 1
        assert time.monotonic() - started < 10  # pg_sleep(30)을 끝까지 기다리지 않았다.
        assert engine.pool.checkedout() == 0
        with engine.connect() as connection:
            # 취소는 문장만 끝냈다. 연결은 폐기되지 않고 pool에서 다시 쓴다.
            assert connection.exec_driver_sql("select pg_backend_pid()").scalar() == pids[0]
        with engine.connect() as connection:
            assert connection.exec_driver_sql("select 1").scalar() == 1  # 감시 밖은 그대로
    finally:
        engine.dispose()
