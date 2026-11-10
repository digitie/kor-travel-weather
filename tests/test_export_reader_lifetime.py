"""공개 page는 metadata(freeze·health) 단계에서 artifact guard를 쥐지 않는다(2026-11-07 계약 변경).

예전 계약은 page 전체(snapshot freeze·DB health·파일 읽기)를 공유 guard 하나로 감쌌다. 운영에서
첫 페이지 freeze가 50~150초 걸리는 동안 writer(collector publish·승격 CAS)가 5초 상한에서 실패해
6시간마다 450~700건의 attempt가 버려졌다. 이제 공유 guard는 artifact 파일 하나를 읽는 동안만
잡는다(``ExportArtifactStore.read_shared``). 읽을 파일은 이미 commit된 snapshot ref·현재 head·
보존 창이 GC에서 지킨다(``test_frozen_service_exports``의 GC 경합 시험).
"""

import multiprocessing
import time
from pathlib import Path

import pytest

from kortravelweather.export_artifacts import ExportArtifactStore, ExportGuardTimeout
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
        if not release.wait(20):
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
def test_long_page_metadata_phase_does_not_starve_writers(tmp_path, kind, finish):
    """freeze·health가 오래 걸려도 writer는 그동안 배타 guard를 얻는다.

    예전에는 writer가 5초 상한에서 실패했다.
    """
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    process = context.Process(
        target=_paused_public_page,
        args=(str(tmp_path), kind, entered, release, finish == "error"),
    )
    process.start()
    store = ExportArtifactStore(tmp_path)
    try:
        assert entered.wait(20), f"page metadata 진입 실패: exit={process.exitcode}"
        with store.guard(timeout_seconds=0.5):
            pass  # 승격 CAS·collector publish가 metadata 단계의 reader를 기다리지 않는다
        with store.guard(shared=True, timeout_seconds=0.5), store.guard(
            shared=True, timeout_seconds=0.5,
        ):
            pass  # 파일 읽기용 공유 slot도 metadata 단계가 차지하지 않는다
        if finish == "kill":
            process.terminate()
        else:
            release.set()
        process.join(20)
        assert not process.is_alive()
        assert (process.exitcode != 0) if finish == "kill" else (process.exitcode == 0)
        with store.guard(timeout_seconds=0.1):
            pass
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)


def test_page_metadata_runs_while_a_writer_holds_the_guard(tmp_path):
    """page 진입은 guard를 기다리지 않는다. 파일을 읽을 때만 공유 guard 상한이 걸린다."""
    reader = object.__new__(FrozenForecastExports)
    reader.artifacts = ExportArtifactStore(tmp_path, reader_guard_seconds=0.5)
    seen = []

    def metadata(**_kwargs):
        seen.append(True)
        return {"metadata": True}

    reader._page_guarded = metadata
    with reader.artifacts.guard():
        started = time.monotonic()
        assert reader.page(limit=1, cursor=None) == {"metadata": True}
        assert seen and time.monotonic() - started < 0.5
        with pytest.raises(ExportGuardTimeout, match="대기 상한"):
            reader.artifacts.read_shared("0" * 64, byte_count=0, point_count=0)
