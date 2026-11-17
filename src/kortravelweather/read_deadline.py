"""공개 API 문맥에서만 pool 대기를 남은 요청 시간으로 줄인다."""

from contextvars import ContextVar
from time import monotonic

from sqlalchemy.pool import QueuePool

from .read_performance import read_stage

PUBLIC_READ_DEADLINE = ContextVar("weather_public_read_deadline", default=None)


class ReadDeadlineQueuePool(QueuePool):
    # QueuePool의 표준 대기 구현을 그대로 쓰고 timeout 값만 요청 문맥에서 줄인다.
    # 공유 pool의 baseline 값은 변경하지 않으므로 collector/admin과 요청끼리 간섭하지 않는다.
    @property
    def _timeout(self):
        deadline = PUBLIC_READ_DEADLINE.get()
        if deadline is None:
            return self._weather_baseline_timeout
        return min(self._weather_baseline_timeout, max(0, deadline - monotonic()))

    @_timeout.setter
    def _timeout(self, value):
        self._weather_baseline_timeout = value

    def connect(self):
        with read_stage("pool_admission"):
            return super().connect()
