"""KMA 단기예보의 대상시각 기준 보존정책과 유한 정리 예산."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

from .models import WeatherValue

SHORT_FORECAST_HOURS = 6
SHORT_FORECAST_PREDICATE = (
    "provider = 'python-kma-api' AND dataset_key = 'kma_short_forecast' "
    "AND weather_domain = 'kma_short_forecast' AND forecast_style = 'short' "
    "AND valid_at IS NOT NULL"
)
SHORT_FORECAST_CANDIDATES_SQL = (
    "SELECT value_id, known_at FROM weather_values WHERE "
    + SHORT_FORECAST_PREDICATE
    + " AND valid_at < :cutoff ORDER BY valid_at, known_at, value_id LIMIT :batch_size"
)
# 운영 사전 count는 별도 읽기 계획/5초 예산 검토 후 root만 실행한다.
SHORT_FORECAST_COUNT_SQL = (
    "SELECT COUNT(*) FROM weather_values WHERE "
    + SHORT_FORECAST_PREDICATE + " AND valid_at < :cutoff"
)
SHORT_FORECAST_EXISTS_SQL = (
    "SELECT EXISTS (SELECT 1 FROM weather_values WHERE "
    + SHORT_FORECAST_PREDICATE
    + " AND valid_at < :cutoff)"
)


class ShortForecastPartitionRetained(RuntimeError):
    """지난 known_at partition에도 보존할 단기예보가 있어 DDL을 보류한다."""


def short_forecast_cutoff(now: datetime) -> datetime:
    """현재시각은 한 batch에 한 번만 받고 UTC의 엄격한6시간 경계를 계산한다."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("보존 기준시각은 timezone-aware여야 합니다.")
    return now.astimezone(UTC) - timedelta(hours=SHORT_FORECAST_HOURS)


def _short_forecast(value: WeatherValue) -> bool:
    return (
        value.provider == "python-kma-api"
        and value.dataset_key == "kma_short_forecast"
        and value.weather_domain == "kma_short_forecast"
        and value.forecast_style.value == "short"
    )


def expired_short_forecast(value: WeatherValue, cutoff: datetime) -> bool:
    """정확한 KMA 단기예보만 검사하며 미래/NULL·다른 원천은 보존한다."""
    return _short_forecast(value) and value.valid_at is not None and value.valid_at < cutoff


def retained_short_forecast(value: WeatherValue, cutoff: datetime) -> bool:
    """최근6시간과 미래 대상시각은 잊힌 수신 clock 때문에 제외하지 않는다."""
    return _short_forecast(value) and value.valid_at is not None and value.valid_at >= cutoff


class _ShortForecastRepository(Protocol):
    def purge_expired_short_forecast(
        self, *, cutoff: datetime, batch_size: int, dry_run: bool,
    ) -> dict[str, Any]: ...


def run_short_forecast_retention(
    repository: _ShortForecastRepository, *, cutoff: datetime,
    dry_run: bool = True, batch_size: int = 1000,
    max_batches: int = 100, budget_seconds: float = 60,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """각 transaction은 독립 commit하며 실패·예산 소진을 완료로 숨기지 않는다."""
    if not 1 <= max_batches <= 100 or not 0 < budget_seconds <= 60:
        raise ValueError("정리 예산은 최대100 batch/60초입니다.")
    started = monotonic()
    result: dict[str, Any] = {
        "operation_id": str(uuid4()), "cutoff": cutoff.isoformat(),
        "dry_run": dry_run, "batches": 0, "matched_in_batches": 0,
        "pointers_deleted": 0, "facts_deleted": 0, "remaining": None,
        "status": "budget_exhausted",
    }
    for _ in range(1 if dry_run else max_batches):
        if monotonic() - started >= budget_seconds:
            break
        result["elapsed_seconds"] = monotonic() - started
        result["remaining_budget_seconds"] = max(0, budget_seconds - result["elapsed_seconds"])
        if on_progress is not None:
            on_progress({**result, "phase": "BATCH_INTENT"})
        report = repository.purge_expired_short_forecast(
            cutoff=cutoff, batch_size=batch_size, dry_run=dry_run,
        )
        result["batches"] += 1
        for key in ("pointers_deleted", "facts_deleted"):
            result[key] += report[key]
        result["matched_in_batches"] += report["matched_in_batch"]
        result["remaining"] = report["remaining"]
        result["last_batch_elapsed_seconds"] = report.get("elapsed_seconds")
        result["elapsed_seconds"] = monotonic() - started
        result["remaining_budget_seconds"] = max(0, budget_seconds - result["elapsed_seconds"])
        if on_progress is not None:
            on_progress({
                **result, "phase": "DRY_RUN_SNAPSHOT" if dry_run else "BATCH_COMMITTED",
            })
        if report["deferred"]:
            result["status"] = "deferred"
            break
        if dry_run:
            result["status"] = "dry_run"
            break
        if not report["remaining"]:
            result["status"] = "complete_snapshot"
            break
        if not report["matched_in_batch"]:
            # SKIP LOCKED로 전부 건너뛰었으면 같은 행을100회 즉시 재조회하지 않는다.
            # 남은 행은 다음 실행으로 연기하며 완료로 표시하지 않는다.
            result["status"] = "deferred"
            break
    result["elapsed_seconds"] = monotonic() - started
    return result
