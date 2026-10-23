"""별도 OS 프로세스의 공유 읽기와 배타 쓰기 guard를 검증한다."""

import multiprocessing
from contextlib import contextmanager
from pathlib import Path

import pytest

from kortravelweather.export_artifacts import ExportArtifactError, ExportArtifactStore


def _holder(root, shared, ready, release):
    with ExportArtifactStore(Path(root)).guard(shared=shared, timeout_seconds=5):
        ready.set()
        if not release.wait(15):
            raise RuntimeError("시험 guard release가 도착하지 않았습니다.")


@contextmanager
def _holding(root, *, shared):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_holder, args=(str(root), shared, ready, release))
    process.start()
    try:
        assert ready.wait(10), f"guard 취득 실패: exit={process.exitcode}"
        yield
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
        assert process.exitcode == 0


def test_two_readers_share_guard_and_writer_waits_until_both_release(tmp_path):
    store = ExportArtifactStore(tmp_path)
    with _holding(tmp_path, shared=True):
        with (
            _holding(tmp_path, shared=True),
            pytest.raises(ExportArtifactError, match="대기 상한"),
            store.guard(timeout_seconds=0.05),
        ):
            pytest.fail("읽는 중 배타 guard를 얻으면 안 됩니다.")
        # read slot도 프로세스 간 공유되므로 세 번째 reader를 무제한 추가하지 않는다.
        with (
            _holding(tmp_path, shared=True),
            pytest.raises(ExportArtifactError, match="동시 읽기 대기 상한"),
            store.guard(shared=True, timeout_seconds=0.05),
        ):
            pytest.fail("동시에 세 번의 파일 읽기를 허용하면 안 됩니다.")
        # 두 번째 reader가 종료되어도 첫 reader가 남아 있으면 writer를 차단한다.
        with store.guard(shared=True, timeout_seconds=0.05):
            pass
        with (
            pytest.raises(ExportArtifactError, match="대기 상한"),
            store.guard(timeout_seconds=0.05),
        ):
            pytest.fail("마지막 reader가 남아 있습니다.")
    with store.guard(timeout_seconds=0.05):
        pass


def test_exclusive_writer_blocks_shared_and_default_exclusive_reader(tmp_path):
    store = ExportArtifactStore(tmp_path)
    with _holding(tmp_path, shared=False):
        for shared in (False, True):
            with (
                pytest.raises(ExportArtifactError, match="대기 상한"),
                store.guard(shared=shared, timeout_seconds=0.05),
            ):
                pytest.fail("writer가 있는 동안 다른 guard를 얻으면 안 됩니다.")
    with store.guard(shared=True, timeout_seconds=0.05):
        pass


def test_terminated_reader_releases_main_guard_and_read_capacity(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_holder, args=(str(tmp_path), True, ready, release))
    process.start()
    try:
        assert ready.wait(10)
        process.terminate()
        process.join(5)
        assert process.exitcode is not None and process.exitcode != 0
        store = ExportArtifactStore(tmp_path)
        with store.guard(timeout_seconds=0.05):
            pass
        # lock 파일이 남아 있어도 죽은 프로세스의 두 fd 잠금은 남지 않는다.
        with store.guard(shared=True), store.guard(shared=True):
            pass
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
