"""KMA 단기예보의 대상시각 기준 보존정책과 유한 정리 예산."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any, NamedTuple, Protocol
from uuid import uuid4

from .models import WeatherValue

SHORT_FORECAST_HOURS = 6
SHORT_FORECAST_PREDICATE = (
    "provider = 'python-kma-api' AND dataset_key = 'kma_short_forecast' "
    "AND weather_domain = 'kma_short_forecast' AND forecast_style = 'short' "
    "AND valid_at IS NOT NULL"
)
_EXPIRED_ROWS = (
    "SELECT value_id, known_at, valid_at, tableoid, ctid::text FROM weather_values WHERE "
    + SHORT_FORECAST_PREDICATE
    + " AND valid_at < :cutoff"
)
#: 정리 순서는 부분 인덱스 ``(valid_at, known_at, value_id)`` 순서와 같다. 지운 행은
#: VACUUM 전까지 인덱스 머리에 죽은 항목으로 남으므로, 다음 batch와 다음 실행은
#: 머리부터 다시 읽지 않고 이미 지운 마지막 키 뒤에서 index scan을 시작한다.
SHORT_FORECAST_AFTER = (
    " AND (valid_at, known_at, value_id) > (:after_valid_at, :after_known_at, :after_value_id)"
)
_CANDIDATE_ORDER = " ORDER BY valid_at, known_at, value_id LIMIT :batch_size"
SHORT_FORECAST_CANDIDATES_SQL = _EXPIRED_ROWS + _CANDIDATE_ORDER
SHORT_FORECAST_CANDIDATES_AFTER_SQL = _EXPIRED_ROWS + SHORT_FORECAST_AFTER + _CANDIDATE_ORDER
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
SHORT_FORECAST_EXISTS_AFTER_SQL = (
    "SELECT EXISTS (SELECT 1 FROM weather_values WHERE "
    + SHORT_FORECAST_PREDICATE
    + " AND valid_at < :cutoff"
    + SHORT_FORECAST_AFTER
    + ")"
)
#: 같은 transaction에서 FOR UPDATE로 잠근 행을 그 물리 위치(ctid)로 지운다.
#: 잠긴 행은 갱신(불변 trigger)·이동(VACUUM FULL 등)이 막혀 위치가 바뀌지 않는다.
#: known_at 범위가 그 partition 하나로 pruning하고 tableoid가 다른 partition의 같은
#: 위치를 배제한다. pair 조인 삭제가 행마다 PK를 찾던 무작위 읽기(운영 1000행에
#: 1525 page·2.5초)를 없애며, RETURNING으로 고른 키와 정확히 같은지 확인한다.
SHORT_FORECAST_DELETE_SQL = (
    "DELETE FROM weather_values WHERE tableoid = CAST(:relation AS oid) "
    "AND ctid = ANY(CAST(:tids AS tid[])) AND known_at >= :low AND known_at <= :high "
    "RETURNING value_id, known_at"
)
#: 정상 문장의 예산. 이어 읽기·삭제·남은행 확인은 모두 이 안에 끝난다.
STATEMENT_TIMEOUT_MS = 5000
#: 이어 읽을 위치가 없을 때 첫 선택이 인덱스 머리의 죽은 항목을 지나는 상한.
#: 비용은 마지막 VACUUM 이후 지운 행 수에 비례하며 위치를 잃었을 때만 한 번 낸다.
HEAD_STATEMENT_TIMEOUT_MS = 45000
#: 한 batch 상한. 삭제 두 문장이 각각 정상 문장 예산 안에 끝나는 크기다.
MAX_BATCH_SIZE = 1000
RESUME_METADATA_KEYS = ("resume_valid_at", "resume_known_at", "resume_value_id")
#: 저장한 위치가 뜻을 갖는 정책. 경계 시간이나 대상 predicate가 바뀌면 옛 위치를 버린다.
RETENTION_POLICY = (
    f"{SHORT_FORECAST_HOURS}h:"
    + hashlib.sha256(SHORT_FORECAST_PREDICATE.encode()).hexdigest()[:12]
)
#: 위치를 따라가더라도 이 간격마다 한 번은 인덱스 머리부터 다시 훑는다.
#: 위치 앞에 늦게 들어온 행(복원·늦은 재수집·시계 역행 등)을 영구히 놓치지 않게 한다.
HEAD_SWEEP_INTERVAL = timedelta(hours=24)
#: 남은 backlog가 경계보다 이만큼 넘게 뒤처지면 진척이 있어도 실패로 알린다.
BACKLOG_LAG_ALERT = timedelta(hours=24)


class ShortForecastPartitionRetained(RuntimeError):
    """지난 known_at partition에도 보존할 단기예보가 있어 DDL을 보류한다."""


class ShortForecastKey(NamedTuple):
    """부분 인덱스 순서의 정리 위치. 이 실행이 이 키까지 순서대로 지웠다는 기록이다.

    정상 수집은 batch 시작 경계보다 오래된 단기예보를 저장하지 않지만, 위치 앞에 만료 행이
    다시 생길 수는 있다(DB 복원·이동, 늦게 commit한 수집, 시계 역행, 정책 변경 등).
    그래서 위치는 ``resume_position``이 정책·DB·경계·주기 머리 훑기로 검사한 뒤에만 쓴다.
    """

    valid_at: datetime
    known_at: datetime
    value_id: str

    def params(self) -> dict[str, Any]:
        return {
            "after_valid_at": self.valid_at,
            "after_known_at": self.known_at,
            "after_value_id": self.value_id,
        }

    def as_metadata(self) -> dict[str, str]:
        return dict(zip(
            RESUME_METADATA_KEYS,
            (self.valid_at.isoformat(), self.known_at.isoformat(), self.value_id),
            strict=True,
        ))

    @classmethod
    def from_metadata(cls, values: Mapping[str, object]) -> ShortForecastKey | None:
        """앞 실행 metadata의 위치. 없거나 모양이 다르면 머리부터 시작하도록 None."""
        raw = [values.get(key) for key in RESUME_METADATA_KEYS]
        if not all(isinstance(item, str) and item for item in raw):
            return None
        valid_raw, known_raw, value_id = (str(item) for item in raw)
        try:
            valid_at = datetime.fromisoformat(valid_raw)
            known_at = datetime.fromisoformat(known_raw)
        except ValueError:
            return None
        if valid_at.tzinfo is None or known_at.tzinfo is None or len(value_id) > 64:
            return None
        return cls(valid_at.astimezone(UTC), known_at.astimezone(UTC), value_id)


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
        after: ShortForecastKey | None, statement_timeout_ms: int,
    ) -> dict[str, Any]: ...


def _select_timeout_ms(position: ShortForecastKey | None, remaining_seconds: float) -> int:
    """이어 읽을 위치가 있으면 정상 예산, 없으면 남은 작업 예산 안의 머리 탐색 상한."""
    if position is not None:
        return STATEMENT_TIMEOUT_MS
    budget_ms = int(remaining_seconds * 1000)
    return max(STATEMENT_TIMEOUT_MS, min(HEAD_STATEMENT_TIMEOUT_MS, budget_ms))


def _position_metadata(position: ShortForecastKey | None) -> dict[str, str | None]:
    if position is None:
        return dict.fromkeys(RESUME_METADATA_KEYS)
    return dict(position.as_metadata())


def _aware(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def resume_position(
    previous: Mapping[str, object], *, cutoff: datetime, database: str | None, now: datetime,
) -> tuple[ShortForecastKey | None, datetime | None, str | None]:
    """앞 실행 metadata에서 이어 읽을 위치·마지막 머리 훑기 시각·버린 이유를 고른다.

    위치는 같은 정책·같은 DB에서 남긴 것이고, 경계보다 앞서 있지 않으며, 마지막 머리 훑기가
    ``HEAD_SWEEP_INTERVAL`` 안일 때만 쓴다. 하나라도 어긋나면 머리부터 시작한다.
    """
    key = ShortForecastKey.from_metadata(previous)
    if key is None:
        return None, None, None
    if previous.get("resume_policy") != RETENTION_POLICY:
        return None, None, "policy_changed"
    if database is None or previous.get("resume_database") != database:
        return None, None, "database_changed"
    if key.valid_at >= cutoff:
        return None, None, "ahead_of_cutoff"
    swept = _aware(previous.get("head_scan_at"))
    if swept is None or now.astimezone(UTC) - swept >= HEAD_SWEEP_INTERVAL:
        return None, None, "periodic_head_sweep"
    return key, swept, None


def run_short_forecast_retention(
    repository: _ShortForecastRepository, *, cutoff: datetime,
    dry_run: bool = True, batch_size: int = 1000,
    max_batches: int = 100, budget_seconds: float = 60,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    after: ShortForecastKey | None = None,
    database: str | None = None,
    head_scan_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """각 transaction은 독립 commit하며 실패·예산 소진을 완료로 숨기지 않는다.

    ``after``는 앞 실행이 지운 마지막 키다(``resume_position``으로 검사한 것). 각 batch는 직전
    batch의 마지막 키 뒤에서 이어 고르므로 같은 실행 안에서 지운 행의 죽은 인덱스 항목을
    다시 지나지 않는다. 경계보다 앞선 위치는 버리고 머리부터 시작한다.
    결과의 ``resume_*``·``resume_policy``·``resume_database``·``head_scan_at``은 다음 실행이
    위치를 검사·승계할 metadata다. ``backlog_lag_seconds``는 경계 − 마지막으로 지운 키의
    대상시각이며, 남은 가장 오래된 행은 그 뒤에 있으므로 실제 지연의 **상한**이다.
    """
    if not 1 <= max_batches <= 100 or not 0 < budget_seconds <= 60:
        raise ValueError("정리 예산은 최대100 batch/60초입니다.")
    started = monotonic()
    discarded = None
    if after is not None and after.valid_at >= cutoff:
        after, discarded = None, "ahead_of_cutoff"
    position = after
    scanned = head_scan_at if after is not None else (now or datetime.now(UTC))
    result: dict[str, Any] = {
        "operation_id": str(uuid4()), "cutoff": cutoff.isoformat(),
        "dry_run": dry_run, "batches": 0, "matched_in_batches": 0,
        "pointers_deleted": 0, "facts_deleted": 0, "remaining": None,
        "status": "budget_exhausted", "resumed": after is not None,
        "resume_discarded": discarded,
        "error_class": None, "backlog_lag_seconds": None,
        "resume_policy": RETENTION_POLICY, "resume_database": database,
        "head_scan_at": scanned.astimezone(UTC).isoformat() if scanned is not None else None,
        **_position_metadata(position),
    }
    for _ in range(1 if dry_run else max_batches):
        elapsed = monotonic() - started
        if elapsed >= budget_seconds:
            break
        result["elapsed_seconds"] = elapsed
        result["remaining_budget_seconds"] = max(0, budget_seconds - elapsed)
        if on_progress is not None:
            on_progress({**result, "phase": "BATCH_INTENT"})
        report = repository.purge_expired_short_forecast(
            cutoff=cutoff, batch_size=batch_size, dry_run=dry_run, after=position,
            statement_timeout_ms=_select_timeout_ms(position, budget_seconds - elapsed),
        )
        result["batches"] += 1
        for key in ("pointers_deleted", "facts_deleted"):
            result[key] += report[key]
        result["matched_in_batches"] += report["matched_in_batch"]
        if report["remaining"] is not None or result["remaining"] is None:
            # 연기된 batch의 알 수 없음(None)이 앞 batch가 확인한 남은행을 지우지 않는다.
            result["remaining"] = report["remaining"]
        result["last_batch_elapsed_seconds"] = report.get("elapsed_seconds")
        if report.get("error_class"):
            result["error_class"] = report["error_class"]
        if not dry_run and report.get("last_key") is not None:
            # dry-run은 지우지 않으므로 다음 실행의 위치를 앞당기지 않는다.
            position = report["last_key"]
            result.update(_position_metadata(position))
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
            # 남은 행이 있는데 고른 행이 없으면 같은 행을100회 즉시 재조회하지 않는다.
            # 남은 행은 다음 실행으로 연기하며 완료로 표시하지 않는다.
            result["status"] = "deferred"
            break
    if result["remaining"] is False:
        result["backlog_lag_seconds"] = 0.0
    elif result["remaining"] and position is not None:
        # 위치 뒤의 가장 오래된 행은 위치보다 늦으므로 이 값은 실제 지연의 상한이다.
        result["backlog_lag_seconds"] = max(0.0, (cutoff - position.valid_at).total_seconds())
    result["elapsed_seconds"] = monotonic() - started
    return result


def made_progress(result: Mapping[str, Any]) -> bool:
    """정리 완료이거나 이번 실행에서 실제로 지운 행이 있으면 진척이다. dry-run은 진척이 아니다."""
    if result.get("dry_run"):
        return False
    return result.get("status") == "complete_snapshot" or bool(result.get("facts_deleted"))


def backlog_alert(result: Mapping[str, Any]) -> bool:
    """남은 backlog의 지연 상한이 ``BACKLOG_LAG_ALERT``를 넘으면 진척이 있어도 알린다."""
    lag = result.get("backlog_lag_seconds")
    return (
        bool(result.get("remaining")) and isinstance(lag, int | float)
        and lag > BACKLOG_LAG_ALERT.total_seconds()
    )
