"""유한 라벨의 요청 단계 계측. 공유 mutable trace만 worker 문맥으로 전달한다."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from time import monotonic

from .metrics import observe_read_stage

_TRACE = ContextVar("weather_read_trace", default=None)


@dataclass
class ReadTrace:
    resource: str
    page: str
    timings: list = field(default_factory=list)
    artifact_read_calls: int = 0
    forecast_input_bytes: int = 0
    forecast_input_refs: int = 0

    def server_timing(self) -> str:
        """이 요청의 완료 단계만 고정7종으로 낸다. 중첩 시간은 전체 wall로 합산하지 않는다."""
        totals = {stage: [0, 0.0] for stage in (
            "worker_queue", "pool_admission", "sql", "freeze", "metadata", "artifact", "render",
        )}
        for stage, duration in self.timings:
            if stage in totals:
                totals[stage][0] += 1
                totals[stage][1] += max(0.0, duration) * 1000
        return ", ".join(
            f'{stage};dur={duration:.3f};desc="count={count}"'
            for stage, (count, duration) in totals.items()
        )

    def finish(self, *, not_modified=False, outcome="ok"):
        page = "304" if not_modified else self.page
        for stage, duration in self.timings:
            observe_read_stage(self.resource, page, stage, outcome, duration)


def record_verified_forecast_input(byte_count: int) -> None:
    trace = _TRACE.get()
    if trace is not None and trace.resource == "forecasts":
        trace.forecast_input_bytes += byte_count
        trace.forecast_input_refs += 1


@contextmanager
def read_trace(trace):
    token = _TRACE.set(trace)
    try:
        yield
    finally:
        _TRACE.reset(token)


@contextmanager
def read_stage(stage):
    trace = _TRACE.get()
    if trace is not None and stage == "artifact":
        trace.artifact_read_calls += 1
    started = monotonic()
    try:
        yield
    finally:
        if trace is not None:
            trace.timings.append((stage, monotonic() - started))


def measured_stage(stage):
    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            with read_stage(stage):
                return function(*args, **kwargs)
        return measured
    return decorate


class MeasuredArtifacts:
    def __init__(self, store):
        self.store = store

    def __getattr__(self, name):
        value = getattr(self.store, name)
        if name == "read_many":
            @wraps(value)
            def measured(*args, **kwargs):
                with read_stage("artifact"):
                    return value(*args, **kwargs)
            return measured
        return value

    def read(self, *args, **kwargs):
        with read_stage("artifact"):
            yield from self.store.read(*args, **kwargs)

    def read_shared(self, *args, **kwargs):
        with read_stage("artifact"):
            return self.store.read_shared(*args, **kwargs)

