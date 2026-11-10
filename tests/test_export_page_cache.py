"""검증 완료 결과의 상한·동일 키 경합·갱신 실패 격리."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_page_cache import ExportPageCache


def reader(deadline=60):
    return SimpleNamespace(
        selection_deadline=deadline, selection_checked_after=0,
        reusable_page_size=lambda: 1, validate_reusable_page=lambda: None,
    )


def test_limit_expiry_and_validation_outside_lock():
    clock = [0]
    cache = ExportPageCache(clock=lambda: clock[0], max_bytes=40, max_entry_bytes=40)
    count = []

    def load():
        count.append(1)
        return ({"a": "b"}, "etag")

    old = reader()
    cache.read("a", old, load)
    def validate():
        assert cache.lock.acquire(False)
        cache.lock.release()

    old.validate_reusable_page = validate
    assert cache.read("a", reader(), load) == ({"a": "b"}, "etag")
    assert len(count) == 1
    clock[0] = 60
    fresh = reader(120)
    fresh.selection_checked_after = 60
    cache.read("a", fresh, load)
    assert len(count) == 2 and cache.bytes <= 40


def test_validation_failure_evicts_without_old_success():
    cache = ExportPageCache(clock=lambda: 0)
    old = reader()
    cache.read("a", old, lambda: ({}, "etag"))

    def broken():
        raise ExportArtifactError("검증용 파일 유실")

    old.validate_reusable_page = broken
    with pytest.raises(ExportArtifactError):
        cache.read("a", reader(), lambda: pytest.fail("실패를 성공으로 대체했습니다."))
    assert not cache.entries and cache.bytes == 0


def test_two_requests_only_build_once_and_large_result_not_cached():
    cache = ExportPageCache(clock=lambda: 0)
    entered, release = Event(), Event()
    count = []

    def load():
        count.append(1)
        entered.set()
        assert release.wait(5)
        return ({}, "etag")

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(cache.read, "a", reader(), load)
        assert entered.wait(5)
        second = pool.submit(cache.read, "a", reader(), load)
        release.set()
        assert first.result() == second.result()
    assert len(count) == 1
    tiny = ExportPageCache(clock=lambda: 0, max_entry_bytes=2)
    assert tiny.read("a", reader(), lambda: ({"large": 1}, "etag"))
    assert not tiny.entries and not tiny.inflight


def test_expired_page_failure_never_serves_previous_success():
    clock = [0]
    cache = ExportPageCache(clock=lambda: clock[0])
    cache.read("a", reader(), lambda: ({}, "old"))
    clock[0] = 60

    def broken():
        raise ExportArtifactError("검증용 새 생성 실패")

    with pytest.raises(ExportArtifactError):
        cache.read("a", reader(120), broken)
    assert not cache.entries and not cache.inflight


def test_unused_keys_have_no_refresh_worker_and_key_budget_is_bounded():
    cache = ExportPageCache(clock=lambda: 0)
    calls = []
    for key in range(20):
        cache.read(key, reader(), lambda: (calls.append(1) or {}, "etag"))
    assert len(calls) == 20
    assert len(cache.entries) == 16
    assert not hasattr(cache, "refresh_one")


def test_waiter_reads_itself_when_owner_fails_instead_of_masking_it_as_busy():
    """소유자가 실패하면 대기자는 자기 읽기의 실제 결과를 받는다(일시 503으로 바꾸지 않는다)."""
    cache = ExportPageCache(clock=lambda: 0)
    entered, release = Event(), Event()

    def failing_owner():
        entered.set()
        assert release.wait(5)
        raise ExportArtifactError("소유자 실패")

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(cache.read, "a", reader(), failing_owner)
        assert entered.wait(5)
        second = pool.submit(cache.read, "a", reader(), lambda: ({"own": 1}, "etag"))
        time.sleep(0.3)  # 두 번째 요청이 소유자의 inflight를 기다리는 상태로 들어간다.
        assert not second.done()
        release.set()
        with pytest.raises(ExportArtifactError):
            first.result()
        assert second.result() == ({"own": 1}, "etag")
    assert not cache.inflight
