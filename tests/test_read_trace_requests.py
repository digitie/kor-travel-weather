"""Artifact read attribution remains per request even while another worker reads."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from kortravelweather.read_performance import ReadTrace, read_stage, read_trace


def test_count_records_entry_before_artifact_generator_finishes():
    trace = ReadTrace("forecasts", "first")
    with read_trace(trace), read_stage("artifact"):
        assert trace.artifact_read_calls == 1
        assert trace.timings == []
    assert len(trace.timings) == 1


def test_parallel_worker_read_is_not_attributed_to_conditional_request():
    conditional = ReadTrace("current", "first")
    other = ReadTrace("current", "first")
    barrier = Barrier(2, timeout=10)

    def worker(trace, stage):
        with read_trace(trace):
            barrier.wait()
            with read_stage(stage):
                barrier.wait()
        return trace.artifact_read_calls

    with ThreadPoolExecutor(max_workers=2) as pool:
        own = pool.submit(worker, conditional, "metadata")
        concurrent = pool.submit(worker, other, "artifact")
        assert own.result() == 0
        assert concurrent.result() == 1
    assert conditional.artifact_read_calls == 0
    assert other.artifact_read_calls == 1


def test_trace_context_does_not_leak_to_later_work():
    trace = ReadTrace("current", "first")
    with read_trace(trace), read_stage("artifact"):
        pass
    with read_stage("artifact"):
        pass
    assert trace.artifact_read_calls == 1
