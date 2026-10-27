"""worker를 띄우지 않는 export publisher sensor. 등록만으로 활성화하지 않는다.

한 tick의 순서는 다음과 같다. 각 단계의 오류는 그 단계만 미루고 사유(SQLSTATE 분류·예외
클래스 이름)를 남긴다. 이미 처리한 진행(cursor)은 보존한다.

1. 승격될 수 없는 pending을 일괄 퇴역(SUPERSEDED·CONFIRMED_TERMINAL, SKIP LOCKED)
2. 가장 최근에 SUCCESS로 끝난 run의 현재 소유 attempt부터 승격(fresh phase)
3. 남은 예산으로 attempt_id keyset sweep(재시도·죽은 worker 복구·나머지 후보)
4. 일별 집계(별도 예산)
5. 참조 없는 artifact GC(프로세스당 10분마다, 별도 예산)
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID

from dagster import DefaultSensorStatus, RunsFilter, SensorDefinition, SkipReason, sensor
from sqlalchemy import text

from kortravelweather.export_collector import (
    PublisherTally,
    TickStatusProbe,
    dagster_status_lookup,
    failure_reason,
    publish_fresh_exports,
    publish_pending_exports,
    refresh_publisher_daily,
    retire_settled_attempts,
)
from kortravelweather.export_gc import collect_export_artifacts
from kortravelweather.metrics import observe_export_artifact_gc
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings

_TICK_SLOT = threading.BoundedSemaphore(1)
_LEASE_KEY = "kortravelweather:export-publisher:v1"
MINIMUM_INTERVAL_SECONDS = 60
#: 한 tick이 보는 후보 총량(fresh + sweep). 행 잠금 없는 NOT_READY 판정은 매우 싸다.
MAX_TICK_ATTEMPTS = 4096
#: 후보 조회 한 번에 가져오는 행 수. 처리는 한 후보씩 하므로 RAM 상한과 무관하다.
CANDIDATE_PAGE = 128
#: source 예산 중 fresh phase가 먼저 쓸 수 있는 비율. 나머지는 sweep 몫이다.
FRESH_SHARE = 0.75
DAILY_BUDGET_SECONDS = 5
GC_BUDGET_SECONDS = 3
GC_INTERVAL_SECONDS = 600
#: 이 프로세스에서 다음 GC를 시작할 monotonic 시각. 재시작하면 첫 tick에 한 번 돈다.
_GC_NEXT = [0.0]


def _cursor(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) > 256:
        raise ValueError("publisher cursor가 올바르지 않습니다.")
    decoded = json.loads(value)
    if not isinstance(decoded, dict) or set(decoded) != {"v", "after"} or decoded["v"] != 1:
        raise ValueError("publisher cursor가 올바르지 않습니다.")
    after = decoded["after"]
    if after is not None and (not isinstance(after, str) or str(UUID(after)) != after):
        raise ValueError("publisher cursor가 올바르지 않습니다.")
    return after


@contextmanager
def _publisher_lease(repository: WeatherRepository) -> Iterator[bool]:
    """전용 connection 하나만 lease로 유지한다. business transaction은 보유하지 않는다.

    수동 publisher는 기존 CAS/file guard로 보호된다. PostgreSQL 이외 DB에서는
    자동 publisher를 실행하지 않으며 lease 연결 소실도 다음 tick에서 재시도한다.
    """
    if repository.engine.dialect.name != "postgresql":
        yield False
        return
    with repository.engine.connect() as connection:
        acquired = False
        try:
            with connection.begin():
                connection.execute(text("SET LOCAL statement_timeout = '2s'"))
                acquired = bool(connection.scalar(text(
                    "SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"
                ), {"key": _LEASE_KEY}))
            yield acquired
        finally:
            if acquired:
                try:
                    with connection.begin():
                        connection.execute(text("SET LOCAL statement_timeout = '2s'"))
                        unlocked = connection.scalar(text(
                            "SELECT pg_advisory_unlock(hashtextextended(:key, 0))"
                        ), {"key": _LEASE_KEY})
                        if unlocked is not True:
                            connection.invalidate()
                except Exception:
                    # session lock가 pool에 남지 않도록 물리 연결까지 폐기한다.
                    connection.invalidate()
                    raise


def _repository(context, settings):
    resource = context.resources.weather_repository
    return WeatherRepository(
        resource.database_url or settings.database_url,
        export_status_enabled=settings.service_exports_enabled,
    )


def dagster_status_batch(instance) -> Callable[[Sequence[str]], Mapping[str, str]]:
    """여러 run의 정확한 상태 이름을 조회 한 번으로 읽는다. 없는 run은 결과에 없다."""

    def batch(run_ids: Sequence[str]) -> Mapping[str, str]:
        runs = instance.get_runs(filters=RunsFilter(run_ids=list(run_ids)))
        return {run.run_id: run.status.name for run in runs}

    return batch


class _Tick:
    """한 tick의 진행과 결과. 메시지에는 분류된 사유만 남긴다."""

    def __init__(self, context, after: str | None):
        self.context = context
        self.after = after
        self.tally = PublisherTally()
        self.retired: Counter[str] = Counter()
        self.stage_reasons: Counter[str] = Counter()
        self.fresh = 0
        self.swept = 0
        self.sweep_ok = True
        self.daily: Mapping[str, Any] = {"processed": 0}
        self.gc: Mapping[str, Any] | None = None

    def defer(self, stage: str, exc: BaseException) -> None:
        reason = failure_reason(exc)
        self.stage_reasons[f"{stage}:{reason}"] += 1
        self.context.log.warning(
            f"weather_export_publisher_deferred stage={stage} reason={reason}"
        )

    def message(self) -> str:
        results = self.tally.results
        detail = [
            f"fresh={self.fresh}",
            f"published={results.get('PUBLISHED', 0)}",
            f"not_ready={results.get('NOT_READY', 0)}",
            f"deferred={results.get('DEFERRED', 0)}",
            f"retired={sum(self.retired.values())}",
        ]
        reasons = self.tally.reasons + self.stage_reasons
        if reasons:
            detail.append("reasons=" + ",".join(
                f"{key}:{count}" for key, count in sorted(reasons.items())
            ))
        if self.gc is not None:
            done = self.gc["deleted"] if self.gc["mode"] == "delete" else self.gc["eligible"]
            size = (
                self.gc["deleted_bytes"] if self.gc["mode"] == "delete"
                else self.gc["eligible_bytes"]
            )
            detail.append(f"gc={self.gc['mode']}:{done}files/{size}B")
        # 같은 후보를 fresh와 sweep이 둘 다 지나가도 한 번만 센다.
        return (
            f"export publisher: {len(self.tally.seen)}개 후보 확인, "
            f"일별 {self.daily['processed']}세대 확인 ({' '.join(detail)})"
        )


def _run_tick(tick: _Tick, repository, root, settings) -> None:
    started = time.monotonic()
    budget = float(settings.service_export_publisher_budget_seconds)
    source_deadline = started + budget
    probe = TickStatusProbe(
        dagster_status_lookup(tick.context.instance),
        dagster_status_batch(tick.context.instance),
    )
    try:
        tick.retired.update(retire_settled_attempts(repository))
    except Exception as exc:
        tick.defer("retire", exc)
    fresh_budget = min(budget * FRESH_SHARE, source_deadline - time.monotonic())
    if fresh_budget > 0:
        try:
            tick.fresh = publish_fresh_exports(
                repository, root, budget_seconds=min(fresh_budget, 60), dagster_status=probe,
                limit=CANDIDATE_PAGE, max_candidates=MAX_TICK_ATTEMPTS, tally=tick.tally,
            ).processed
        except Exception as exc:
            tick.defer("fresh", exc)
    while tick.fresh + tick.swept < MAX_TICK_ATTEMPTS and not tick.tally.stopped:
        remaining = source_deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            progress = publish_pending_exports(
                repository, root, cursor=tick.after,
                limit=min(CANDIDATE_PAGE, MAX_TICK_ATTEMPTS - tick.fresh - tick.swept),
                budget_seconds=min(remaining, 60), dagster_status=probe, tally=tick.tally,
            )
        except Exception as exc:
            tick.sweep_ok = False
            tick.defer("sweep", exc)
            break
        tick.swept += progress.processed
        tick.after = progress.next_cursor
        if not progress.has_more or progress.processed == 0:
            break
    # source의 마지막 SQL/IO가 늦어져도 daily 기회를 빼앗지 않는다.
    tick.daily = refresh_publisher_daily(
        repository, root, deadline=time.monotonic() + DAILY_BUDGET_SECONDS,
    )
    mode = settings.service_export_artifact_gc
    if mode != "off" and time.monotonic() >= _GC_NEXT[0]:
        _GC_NEXT[0] = time.monotonic() + GC_INTERVAL_SECONDS
        try:
            tick.gc = collect_export_artifacts(
                repository, root, mode=mode,
                retention=timedelta(hours=settings.service_export_artifact_retention_hours),
                deadline=time.monotonic() + GC_BUDGET_SECONDS,
            )
            observe_export_artifact_gc(tick.gc)
        except Exception as exc:
            tick.defer("gc", exc)


def build_export_publisher_sensor(
    *,
    settings_factory: Callable = WeatherSettings,
    repository_factory: Callable = _repository,
) -> SensorDefinition:
    """factory는 새 repository 소유권을 넘긴다. 모든 경로에서 dispose한다.

    source 예산(설정, 기본 24초)과 daily 5초·GC 3초는 협력 예산이다. 마지막 SQL/파일
    대기까지 포함한 wall-time 보장은 아니며 SQL은 기존 5초/잠금 상한을 유지한다. 합계가
    sensor 평가 gRPC 상한(운영 60초) 안에 들도록 source 예산은 30초를 넘지 못한다.
    """

    @sensor(
        name="weather_export_publisher",
        minimum_interval_seconds=MINIMUM_INTERVAL_SECONDS,
        default_status=DefaultSensorStatus.STOPPED,
        required_resource_keys={"weather_repository"},
    )
    def publisher(context):
        repository = None
        acquired = False
        try:
            settings = settings_factory()
            root = settings.service_export_artifact_root
            if not settings.service_exports_enabled or root is None:
                return SkipReason("export publisher는 명시 활성화와 artifact root가 필요합니다.")
            if not root.is_absolute():
                return SkipReason("export artifact root는 절대 경로여야 합니다.")
            acquired = _TICK_SLOT.acquire(blocking=False)
            if not acquired:
                return SkipReason("이 프로세스에서 export publisher를 평가 중입니다.")
            try:
                after = _cursor(context.cursor)
            except (ValueError, TypeError):
                context.log.warning("weather_export_publisher_cursor_reset")
                after = None
            repository = repository_factory(context, settings)
            with _publisher_lease(repository) as leased:
                if not leased:
                    return SkipReason("다른 publisher가 실행 중이거나 lease가 불명확합니다.")
                tick = _Tick(context, after)
                _run_tick(tick, repository, root, settings)
            # sweep이 정상으로 끝났거나 실패 전에 진행했다면 그 위치를 남긴다.
            if tick.sweep_ok or tick.after != after:
                context.update_cursor(json.dumps(
                    {"v": 1, "after": tick.after}, separators=(",", ":"),
                ))
            return SkipReason(tick.message())
        except Exception as exc:
            # DB URL/provider payload/token이 포함된 exception 원문을 남기지 않는다.
            # 정해진 분류(lock_timeout, statement_timeout, 클래스 이름 등)만 남긴다.
            reason = failure_reason(exc)
            context.log.warning(f"weather_export_publisher_deferred reason={reason}")
            return SkipReason(f"export publisher 평가를 다음 tick에 재시도합니다. ({reason})")
        finally:
            if repository is not None:
                try:
                    repository.engine.dispose()
                except Exception:
                    context.log.warning("weather_export_publisher_dispose_deferred")
            if acquired:
                _TICK_SLOT.release()

    return publisher


weather_export_publisher_sensor = build_export_publisher_sensor()
