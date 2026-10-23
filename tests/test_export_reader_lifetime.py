"""공개 page의 metadata 진입 전 reader 수명과 실제 프로세스 잠금 복구."""

import multiprocessing
import time
from pathlib import Path

import pytest

from kortravelweather.export_artifacts import ExportArtifactError, ExportArtifactStore
from kortravelweather.export_daily_reader import DailySummaryExports
from kortravelweather.export_forecasts import FrozenForecastExports
from kortravelweather.export_locations import FrozenLocationExports
from kortravelweather.export_mid_reader import FrozenMidForecastExports
from kortravelweather.export_snapshots import FrozenCurrentExports

READERS = {
    "current": FrozenCurrentExports,
    "forecast": FrozenForecastExports,
    "mid": FrozenMidForecastExports,
    "reference": FrozenLocationExports,
    "daily": DailySummaryExports,
}


def _paused_public_page(root, kind, entered, release, fail):
    reader = object.__new__(READERS[kind])
    reader.artifacts = ExportArtifactStore(Path(root))

    def metadata_body(*_args, **_kwargs):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("page barrier의 해제가 도착하지 않았습니다.")
        if fail:
            raise ValueError("시험 metadata 오류")
        return {"done": True}

    reader._page_guarded = metadata_body
    try:
        args = ["current"] if kind == "current" else ["locations"] if kind == "reference" else []
        reader.page(*args, limit=1, cursor=None)
    except ValueError:
        if not fail:
            raise


@pytest.mark.parametrize("kind", READERS)
@pytest.mark.parametrize("finish", ["return", "error", "kill"])
def test_public_page_protects_metadata_and_releases_after_return_error_or_kill(
    tmp_path, kind, finish,
):
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    process = context.Process(
        target=_paused_public_page,
        args=(str(tmp_path), kind, entered, release, finish == "error"),
    )
    process.start()
    store = ExportArtifactStore(tmp_path)
    try:
        assert entered.wait(10), f"page metadata 진입 실패: exit={process.exitcode}"
        with pytest.raises(ExportArtifactError, match="대기 상한"), store.guard(
            timeout_seconds=0.05,
        ):
            pytest.fail("metadata 단계의 reader를 EX writer가 추월했습니다.")
        if finish == "kill":
            process.terminate()
        else:
            release.set()
        process.join(10)
        assert not process.is_alive()
        assert (process.exitcode != 0) if finish == "kill" else (process.exitcode == 0)
        with store.guard(timeout_seconds=0.1):
            pass
        with store.guard(shared=True, timeout_seconds=0.1), store.guard(
            shared=True, timeout_seconds=0.1,
        ):
            pass
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)


def test_two_public_pages_do_not_nest_reader_slots(tmp_path):
    context = multiprocessing.get_context("spawn")
    release = context.Event()
    children = []
    try:
        for kind in ("forecast", "mid"):
            entered = context.Event()
            process = context.Process(
                target=_paused_public_page, args=(str(tmp_path), kind, entered, release, False),
            )
            process.start()
            children.append(process)
            assert entered.wait(10)
        store = ExportArtifactStore(tmp_path)
        with pytest.raises(ExportArtifactError, match="동시 읽기 대기 상한"), store.guard(
            shared=True, timeout_seconds=0.05,
        ):
            pytest.fail("두 공개 page 외에 세 번째 reader가 입장했습니다.")
        release.set()
        for process in children:
            process.join(10)
            assert process.exitcode == 0
    finally:
        for process in children:
            if process.is_alive():
                process.terminate()
                process.join(5)


def test_public_page_admission_timeout_precedes_metadata(tmp_path):
    reader = object.__new__(FrozenForecastExports)
    reader.artifacts = ExportArtifactStore(tmp_path)

    def forbidden_metadata(**_kwargs):
        pytest.fail("guard 취득 전 metadata에 접근했습니다.")

    reader._page_guarded = forbidden_metadata
    with reader.artifacts.guard():
        started = time.monotonic()
        with pytest.raises(ExportArtifactError, match="대기 상한"):
            reader.page(limit=1, cursor=None)
        assert time.monotonic() - started < 3
