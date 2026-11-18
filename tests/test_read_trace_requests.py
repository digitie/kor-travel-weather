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


def test_server_timing_has_only_bounded_stage_names_and_own_counts():
    trace = ReadTrace("current", "first")
    trace.timings.extend([("sql", .001), ("sql", .00275), ("freeze", .005),
                          ("SELECT private WHERE key=secret", 99)])
    header = trace.server_timing()
    assert 'sql;dur=3.750;desc="count=2"' in header
    assert 'freeze;dur=5.000;desc="count=1"' in header
    assert 'artifact;dur=0.000;desc="count=0"' in header
    assert len(header.split(", ")) == 7 and len(header) < 1024
    assert "SELECT" not in header and "secret" not in header and "current" not in header


def test_parallel_sql_stage_counts_stay_with_each_request():
    barrier = Barrier(2, timeout=10)

    def worker(count):
        trace = ReadTrace("current", "first")
        with read_trace(trace):
            barrier.wait()
            for _ in range(count):
                with read_stage("sql"):
                    pass
        return trace.server_timing()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, other = pool.submit(worker, 1), pool.submit(worker, 5)
        assert 'desc="count=1"' in first.result().split(", ")[2]
        assert 'desc="count=5"' in other.result().split(", ")[2]
