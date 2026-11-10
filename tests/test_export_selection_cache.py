"""소유자 승인 60초 상한·중복 작업 억제·실패 후 갱신을 시계 대기 없이 검산한다."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from kortravelweather.export_artifacts import ExportGuardTimeout
from kortravelweather.export_inventory_selection import ApprovedSelection
from kortravelweather.export_selection_cache import ExportSelectionCache


def test_selection_cache_age_starts_before_read_and_never_extends_on_hit_or_failure():
    clock = [0]
    cache = ExportSelectionCache(clock=lambda: clock[0])
    calls = []
    result = ApprovedSelection((), False)

    def load():
        calls.append(1)
        clock[0] = 20  # DB 조회 시간도 허용 지연 60초에 포함한다.
        return result

    assert cache.read("key", load, checked_after=0) == (result, 60)
    clock[0] = 59.999
    assert cache.read("key", lambda: pytest.fail("hit"), checked_after=59) == (result, 60)
    cache.require_current(60)
    clock[0] = 60
    with pytest.raises(ExportGuardTimeout):
        cache.require_current(60)
    with pytest.raises(ValueError, match="실패"):
        cache.read("key", lambda: (_ for _ in ()).throw(ValueError("실패")), checked_after=60)
    assert not cache.entries and not cache.inflight
    failed_state = ApprovedSelection((), True)
    assert cache.read("key", lambda: failed_state, checked_after=60) == (failed_state, 120)
    assert calls == [1]


def test_selection_cache_slow_first_read_is_not_saved_or_returned_as_fresh():
    clock = [0]
    cache = ExportSelectionCache(clock=lambda: clock[0])

    def load():
        clock[0] = 60
        return ApprovedSelection((), False)

    with pytest.raises(ExportGuardTimeout):
        cache.read("key", load, checked_after=0)
    assert not cache.entries and not cache.inflight and cache.bytes == 0


def test_selection_cache_does_not_hold_lock_during_read_or_duplicate_same_key():
    """같은 키의 두 번째 요청은 즉시 503 대신 첫 읽기를 기다려 그 결과를 쓴다(2026-11-10).

    앱 공용 캐시를 실제로 쓰게 되면서 동시 요청이 같은 키를 만난다. 중복 DB 작업은 여전히 하나다.
    """
    cache = ExportSelectionCache()
    entered, release = Event(), Event()

    def load():
        entered.set()
        assert release.wait(5)
        return ApprovedSelection((), False)

    with ThreadPoolExecutor(2) as executor:
        job = executor.submit(cache.read, "key", load, checked_after=cache.clock())
        assert entered.wait(5)
        waiter = executor.submit(
            cache.read, "key", lambda: pytest.fail("중복"), checked_after=cache.clock()
        )
        try:
            # 다른 키는 잠금에 막히지 않는다.
            assert cache.read(
                "other", lambda: ApprovedSelection((), True), checked_after=cache.clock()
            )[0].unconfirmed
            assert not waiter.done()
        finally:
            release.set()
        assert not job.result(timeout=5)[0].unconfirmed
        assert waiter.result(timeout=5) == job.result(timeout=5)
    assert not cache.inflight


def test_selection_cache_min_deadline_skips_short_lived_entries_without_evicting():
    clock = [0.0]
    cache = ExportSelectionCache(clock=lambda: clock[0])
    first, second = ApprovedSelection((), False), ApprovedSelection((), True)
    assert cache.read("key", lambda: first, checked_after=0) == (first, 60)
    clock[0] = 25.0  # 35초 남음
    assert cache.read("key", lambda: pytest.fail("hit"), checked_after=25,
                      min_deadline=55) == (first, 60)
    # 이 요청은 더 긴 수명이 필요하다: 새로 읽고 교체한다(다른 요청용 항목을 먼저 지우지 않는다).
    assert cache.read("key", lambda: second, checked_after=25, min_deadline=85) == (second, 85)
    assert cache.read("key", lambda: pytest.fail("hit"), checked_after=26) == (second, 85)


def test_selection_cache_token_mismatch_reloads():
    cache = ExportSelectionCache(clock=lambda: 0)
    first, second = ApprovedSelection((), False), ApprovedSelection((), True)
    assert cache.read("key", lambda: first, checked_after=0, token=(1, 1, 1))[0] == first
    assert cache.read("key", lambda: pytest.fail("hit"), checked_after=0,
                      token=(1, 1, 1))[0] == first
    # 사이에 head 삭제(epoch 변화)가 있었다면 그 선택을 잇지 않는다.
    assert cache.read("key", lambda: second, checked_after=0, token=(1, 1, 2))[0] == second


def test_selection_cache_failed_hit_validation_reloads_in_own_transaction():
    """다른 요청의 더 새 snapshot에서 읽힌 선택은 오래된 RR에서 유실처럼 보일 수 있다."""
    cache = ExportSelectionCache(clock=lambda: 0)
    first, second = ApprovedSelection((), False), ApprovedSelection((), True)
    cache.read("key", lambda: first, checked_after=0)

    def newer_than_my_snapshot(selection):
        raise ValueError("이 RR에는 아직 보이지 않는 세대")

    assert cache.read("key", lambda: second, checked_after=0,
                      validate=newer_than_my_snapshot)[0] == second
    assert not cache.inflight


def test_selection_cache_waiter_with_min_deadline_uses_the_owners_fresh_load():
    clock = [10.0]
    cache = ExportSelectionCache(clock=lambda: clock[0])
    entered, release = Event(), Event()
    value = ApprovedSelection((), False)

    def load():
        entered.set()
        assert release.wait(5)
        return value

    with ThreadPoolExecutor(2) as executor:
        owner = executor.submit(cache.read, "key", load, checked_after=10)
        assert entered.wait(5)
        # 대기자는 소유자보다 먼저 시작했다(시작 9). 소유자 결과는 그 뒤 것이므로 쓴다.
        waiter = executor.submit(cache.read, "key", lambda: pytest.fail("중복"),
                                 checked_after=9, min_deadline=69)
        release.set()
        assert owner.result(timeout=5) == (value, 70)
        assert waiter.result(timeout=5) == (value, 70)


def test_selection_cache_key_separates_engine_sources_catalog_and_local_health():
    engine = object()
    connection = SimpleNamespace(engine=engine)
    key = cache_key = ExportSelectionCache.key
    args = ([{"location_id": "l", "point_station_name": None}], [("p", "d")], [], None)
    original = cache_key(connection, *args)
    assert original == key(connection, *args)
    assert original != key(SimpleNamespace(engine=object()), *args)
    assert original != key(
        connection, [{"location_id": "l", "point_station_name": "None"}], *args[1:]
    )
    assert original != key(connection, args[0], [("p", "other")], [], None)
    assert original != key(connection, args[0], args[1], args[1], None)
    assert original != key(connection, *args[:3], {"l"})


def test_selection_cache_capacity_is_bounded_and_oversize_does_not_displace(monkeypatch):
    from kortravelweather import export_selection_cache as module

    monkeypatch.setattr(module, "_MAX_ENTRIES", 2)
    cache = ExportSelectionCache(clock=lambda: 0)
    value = ApprovedSelection((), False)
    for key in ("a", "b", "c"):
        cache.read(key, lambda: value, checked_after=0)
    assert list(cache.entries) == ["b", "c"]
    monkeypatch.setattr(module, "_MAX_ENTRY_BYTES", 1)
    assert cache.read("too-large", lambda: value, checked_after=0)[0] == value
    assert list(cache.entries) == ["b", "c"]
    assert 0 <= cache.bytes <= module._MAX_BYTES


def test_selection_cache_budget_includes_expired_coverage_keys(monkeypatch):
    from kortravelweather import export_selection_cache as module

    monkeypatch.setattr(module, "_MAX_ENTRY_BYTES", 100)
    cache = ExportSelectionCache(clock=lambda: 0)
    value = ApprovedSelection((), True, frozenset({("p", "d", "긴 위치" * 100)}))
    assert cache.read("key", lambda: value, checked_after=0)[0] == value
    assert not cache.entries and cache.bytes == 0


def test_selection_cache_checks_integrity_outside_lock_and_evicts_failed_hit():
    cache = ExportSelectionCache(clock=lambda: 0)
    value = ApprovedSelection((), False)
    cache.read("key", lambda: value, checked_after=0)

    def lost_plan(selection):
        assert selection is value and cache.lock.acquire(blocking=False)
        cache.lock.release()
        raise ValueError("불변 plan 유실")

    with pytest.raises(ValueError, match="plan 유실"):
        cache.read("key", lambda: pytest.fail("hit"), checked_after=0, validate=lost_plan)
    assert not cache.entries and not cache.inflight and cache.bytes == 0
