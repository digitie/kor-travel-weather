"""실제 Dagster tick의 다음 예정 시각을 작은 상태표에 best-effort로 기록한다.

definition import 때 repository를 만들지 않는다. public ``evaluate_tick``의 원래
RunRequest/config/tags/중복 억제를 보존하며 KMA 발표 규칙은 계산하지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from time import monotonic
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from croniter import croniter
from dagster import ScheduleDefinition, ScheduleEvaluationContext, SkipReason
from kortravelcommon.dagster import coalescing_schedule

from kortravelweather.export_status import (
    MAX_STATUS_SCOPES,
    ExportSourceStatuses,
    SourceKey,
    status_transaction,
)

if TYPE_CHECKING:
    from kortravelweather.repository import WeatherRepository

ScopeKeys = tuple[SourceKey, ...]


def next_schedule_time(
    cron_schedule: str | Sequence[str], execution_timezone: str | None, *, after: datetime
) -> datetime:
    """등록 cron의 다음 tick을 UTC로 반환한다. source 발표 시각과는 별개다."""
    if after.tzinfo is None or after.utcoffset() is None:
        raise ValueError("일정 평가 시각에는 timezone이 필요합니다.")
    expressions = (cron_schedule,) if isinstance(cron_schedule, str) else tuple(cron_schedule)
    if not expressions or len(expressions) > 32:
        raise ValueError("일정 cron 개수가 올바르지 않습니다.")
    local = after.astimezone(ZoneInfo(execution_timezone or "UTC"))
    return min(
        croniter(expression, local, max_years_between_matches=5).get_next(datetime).astimezone(UTC)
        for expression in expressions
    )


def schedule_with_export_status(
    *,
    project: str,
    location_name: str,
    source_keys: ScopeKeys | Callable[[ScheduleEvaluationContext], ScopeKeys],
    repository_factory: Callable[[ScheduleEvaluationContext], WeatherRepository],
    schedule_running: Callable[[ScheduleEvaluationContext], bool | None] | None = None,
    **kwargs: Any,
) -> ScheduleDefinition:
    """기존 coalescing schedule을 public hook으로 감싸 metadata만 추가한다.

    factory는 평가 시 새 repository를 반환하며 이 helper가 engine을 dispose한다.
    callback이 없으면 scheduled_execution_time이 있는 실제 tick 평가만 일정의
    근거로 쓴다. False/None을 반환하는 callback이나 시각 없는 수동 평가는 다음
    일정을 null로 기록한다. default_status로 현재 실행 상태를 추측하지 않는다.
    중지 후 새 tick이 없을 때는 마지막 예정 시각이 지나면 상태 reader가 null로
    보여 준다. 운영 중지 여부를 API에서 Dagster에 다시 묻지 않는다.
    """
    original = coalescing_schedule(project=project, location_name=location_name, **kwargs)

    def record(context: ScheduleEvaluationContext) -> None:
        repository = None
        try:
            keys = tuple(source_keys(context) if callable(source_keys) else source_keys)
            if not keys:
                return
            if len(keys) > MAX_STATUS_SCOPES or len(set(keys)) != len(keys):
                raise ValueError("일정 상태 scope가 올바르지 않습니다.")
            evaluated = datetime.now(UTC)
            if schedule_running is not None:
                running = schedule_running(context)
            else:
                try:
                    running = context.scheduled_execution_time is not None
                except Exception:
                    # public property는 수동 context에 tick 시각이 없으면 예외를 낸다.
                    running = None
            upcoming = (
                next_schedule_time(
                    original.cron_schedule, original.execution_timezone, after=evaluated
                )
                if running is True
                else None
            )
            repository = repository_factory(context)
            from kortravelweather.repository import Base

            statuses = ExportSourceStatuses(Base.metadata)
            with repository.engine.begin() as connection, status_transaction(connection):
                deadline = monotonic() + 5
                for key in keys:
                    # 문장 상한만으로 scope마다 긴 대기를 반복하지 않는다. 단일 SQL의
                    # 최종 대기는 status_transaction의 statement/lock 상한이 맡는다.
                    if monotonic() >= deadline:
                        raise TimeoutError("일정 상태 기록 예산을 넘었습니다.")
                    statuses.record_schedule(
                        connection, key, evaluated_at=evaluated, next_scheduled_at=upcoming
                    )
        except Exception:
            # DSN/provider 원문/credential을 예외 문자열과 함께 기록하지 않는다.
            context.log.warning("weather_export_schedule_status_deferred")
        finally:
            if repository is not None:
                try:
                    repository.engine.dispose()
                except Exception:
                    context.log.warning("weather_export_schedule_status_dispose_deferred")

    def evaluate(context: ScheduleEvaluationContext):
        result = original.evaluate_tick(context)
        record(context)
        if result.skip_message is not None:
            yield SkipReason(result.skip_message)
        else:
            yield from result.run_requests

    # 원래 public evaluator가 config/tag callbacks를 이미 수행했다. 다시 호출하면
    # 부작용/두 번 계산이 생기므로 바깥 definition에는 실행 wrapper만 전달한다.
    outer = dict(kwargs)
    for name in ("run_config", "run_config_fn", "tags_fn", "should_execute", "execution_fn"):
        outer.pop(name, None)
    outer.setdefault("required_resource_keys", original.required_resource_keys)
    return ScheduleDefinition(execution_fn=evaluate, **outer)
