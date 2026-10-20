"""`weather_sync_recovery` sensor: tick 예산 안의 회수와 수집 정체 경보.

common `reconciliation_sensor`의 `budget_seconds`·`on_stall`(common
`docs/runbooks/dagster-adoption.md` §19)을 쓴다.

- 예산: tick마다 ``BUDGET_SECONDS`` 안에서만 회수한다. lease 회수(set 기반 1문장) 뒤
  orchestrator 소유 행을 keyset으로 순회하고, 예산이 다하면 멈춘 위치를 code-server 프로세스
  메모리에 두었다가 다음 tick에 이어 간다. 끝에 닿으면 처음으로 돌아간다. 위치는 최적화일
  뿐이다: 프로세스가 바뀌어 위치를 잃으면 처음부터 다시 돌 뿐이고, 위치 앞에 늦게 들어온
  행도 다음 바퀴에 보므로 빠지는 행은 없다. sensor cursor는 이 모드에서 정체 시계가 쓰므로
  쓰지 않고, 이 위치 하나 때문에 DB table을 늘리지도 않는다.
- engine: WeatherRepository(=engine·연결 pool)를 DSN마다 한 번 만들어 tick 사이에 재사용한다.
  예전에는 tick마다 engine을 만들고 버렸다(비운영 환경에서는 create_schema까지).
- 정체: 두 회수 뒤에도 heartbeat가 lease보다 오래된 running 행이 남으면 `SyncRunsStuck`을
  올린다. 그 행은 orchestrator가 아직 끝나지 않았다고 답하는 실행, 즉 프로세스는 살아 있는데
  멈춘 worker다(`deploy/dagster.yaml`의 18시간 정체: run_monitoring은 프로세스가 사라진 run만
  실패시킨다). 콜백 예외·예산 초과(`DeadlineExceeded`)·DB 장애가 ``STALL_AFTER_SECONDS``
  동안 이어지면 `record_stall`이 ``STALL_EVENT`` 오류 로그를 남기고 gauge
  ``ktw_sync_recovery_stalled``를 1로 둔다(Prometheus 규칙 `KorTravelWeatherSyncRecoveryStalled`).
  로그는 ``REALERT_AFTER_SECONDS``마다 다시 남기고, gauge는 다음 성공 tick이 0으로 되돌린다.

예산을 넘겨 끊긴 콜백 thread는 끝까지 돈다. 그래서 `context.resources`는 콜백 첫머리에서만
읽고, orchestrator 조회(`context.instance`)는 행 예비 시간이 남아 있을 때만 한다. 모든 DB
트랜잭션은 repository가 statement_timeout 10초·lock_timeout 5초로 묶는다.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from kortravelcommon.dagster import reconciliation_sensor
from kortravelcommon.stall import StallClock

from kortravelweather.metrics import set_sync_recovery_stalled
from kortravelweather.repository import (
    SYNC_RUN_LEASE_MINUTES,
    StuckSyncRun,
    WeatherRepository,
)

SENSOR_NAME = "weather_sync_recovery"
#: 정체 경보 로그의 고정 접두. 운영 검색어다(`docs/observability.md`).
STALL_EVENT = "weather_sync_recovery.stalled"
#: Dagster sensor 평가 상한(기본 60초)보다 작게, 경보 기록 시간까지 들어가도록 둔다.
BUDGET_SECONDS = 40.0
STALL_AFTER_SECONDS = 900.0
REALERT_AFTER_SECONDS = 3600.0
_STUCK_SAMPLE = 5
#: 경보 문구의 경과 시간용 시계. 시험이 common의 정체 시계와 같은 시각으로 바꾼다.
_now = time.time


class SyncRunsStuck(RuntimeError):
    """회수 뒤에도 heartbeat가 lease보다 오래된 running 행이 남았다."""

    def __init__(self, stuck: list[StuckSyncRun], *, recovered: int) -> None:
        self.stuck = stuck
        sample = ", ".join(
            f"{row.provider}/{row.dataset_key} run={row.run_id} "
            f"orchestrator={row.orchestrator_run_id} heartbeat={row.heartbeat_at.isoformat()}"
            for row in stuck[:_STUCK_SAMPLE]
        )
        super().__init__(
            f"lease({SYNC_RUN_LEASE_MINUTES}분)가 지난 running sync 행 {len(stuck)}건 이상이 "
            f"회수 뒤에도 남음(이번 tick 회수 {recovered}건): {sample}"
        )


def terminal_worker(instance: Any, run_id: str) -> bool | None:
    run = instance.get_run_by_id(run_id)
    # metadata DB 누락/조회 장애를 worker 사망으로 해석하지 않는다.
    return run.is_finished if run is not None else None


@dataclass
class _RecoveryState:
    repository: WeatherRepository
    resume_after: tuple[datetime, str] | None = None


_STATES: dict[str, _RecoveryState] = {}
_STATES_LOCK = threading.Lock()


def _recovery_state(context: Any) -> _RecoveryState:
    resource = context.resources.weather_repository
    # 빈 database_url은 환경 설정을 뜻한다. 환경은 프로세스 수명 동안 바뀌지 않는다.
    key = str(getattr(resource, "database_url", ""))
    with _STATES_LOCK:
        state = _STATES.get(key)
        if state is None:
            state = _RecoveryState(resource.create_repository())
            _STATES[key] = state
        return state


def reconcile_sync_runs(context: Any, remaining: Any) -> int:
    state = _recovery_state(context)
    instance = context.instance
    repository = state.repository
    recovered = repository.reconcile_stale_sync_runs()
    progress = repository.reconcile_interrupted_sync_runs_within(
        lambda run_id: terminal_worker(instance, run_id),
        remaining=remaining,
        after=state.resume_after,
    )
    state.resume_after = progress.resume_after
    recovered += progress.recovered
    stuck = repository.stuck_sync_runs()
    if stuck:
        raise SyncRunsStuck(stuck, recovered=recovered)
    set_sync_recovery_stalled(False)
    return recovered


def record_stall(context: Any, clock: StallClock) -> None:
    """정체 경보를 남긴다. 실패하면 common이 다음 tick에 다시 부른다."""
    set_sync_recovery_stalled(True)
    context.log.error(
        "%s key=%s stalled_seconds=%d last_failure=%s first_failed_at=%s previous_alert_at=%s",
        STALL_EVENT,
        clock.key,
        max(0, int(_now() - clock.first_failed_at)),
        clock.last_failure,
        datetime.fromtimestamp(clock.first_failed_at).astimezone().isoformat(),
        clock.alerted_at,
    )


weather_sync_recovery_sensor = reconciliation_sensor(
    name=SENSOR_NAME,
    reconcile=reconcile_sync_runs,
    required_resource_keys={"weather_repository"},
    budget_seconds=BUDGET_SECONDS,
    on_stall=record_stall,
    stall_after_seconds=STALL_AFTER_SECONDS,
    realert_after_seconds=REALERT_AFTER_SECONDS,
)
