"""실제 KMA 단일 격자 collector의 명시 opt-in adapter와 bounded publisher.

원본 fact 저장은 그대로 유지하고, export 준비 실패는 원본 수집을 실패시키지 않는다.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kortravelcommon.deadline import call_with_deadline
from sqlalchemy import and_, literal_column, select, text, tuple_, update
from sqlalchemy.exc import DBAPIError, OperationalError

from .export_artifacts import ExportArtifactError, ExportArtifactStore
from .export_heads import ExportHeads, ScopeDescriptor
from .export_receipts import ExportAttempt, ExportCapture
from .models import WeatherValue
from .partitions import is_lock_conflict

logger = logging.getLogger(__name__)

#: 거절 사유로 남기는 PostgreSQL SQLSTATE 분류. 그 밖의 DB 오류는 SQLSTATE만 남긴다.
_SQLSTATE_REASONS = {
    "55P03": "lock_timeout",
    "57014": "statement_timeout",
    "40P01": "deadlock",
    "40001": "serialization",
}

#: export 단계의 잠금 경합 재시도 정책. 원본 ingest의 ``_retry_lock_race``(20회 × 3초
#: 대기 + 3초 sleep, 단계당 약 2분)를 쓰지 않는다. KMA는 다음 격자 준비가 앞 격자
#: ingest와 부딪히므로, 짧게 쉬고(jitter) 다시 시도하되 단계당·run(collector)당 쓸 시간을
#: 묶는다. 다 쓰면 원래 잠금 오류로 거절(``CODE:lock_timeout``)하고 원본 수집은 계속한다.
EXPORT_LOCK_PAUSE_SECONDS = (0.2, 1.0)
EXPORT_LOCK_STEP_SECONDS = 15.0
EXPORT_LOCK_RUN_SECONDS = 120.0


def _sqlstate(exc: BaseException) -> str | None:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if isinstance(sqlstate, str) and sqlstate.isalnum() and len(sqlstate) == 5:
        return sqlstate
    return None


def failure_reason(exc: BaseException) -> str:
    """원인 분류만 돌려준다. 메시지·SQL·파라미터·DSN·원문 payload는 담지 않는다.

    DB 오류는 SQLSTATE로, 승인 증명 실패는 ``proof``로, 그 밖은 예외 클래스 이름으로 나눈다.
    """
    sqlstate = _sqlstate(exc)
    if sqlstate is not None:
        return _SQLSTATE_REASONS.get(sqlstate, f"sqlstate_{sqlstate}")
    if isinstance(exc, ExportArtifactError):
        return "proof"
    return type(exc).__name__[:40]


def _proof_detail(exc: BaseException) -> str:
    # 승인 증명 메시지는 코드에 고정된 문장이다. 다른 예외 메시지는 비밀을 담을 수 있다.
    return str(exc)[:160] if isinstance(exc, ExportArtifactError) else "-"


class _PrepareRejected(Exception):
    """attempt를 만든 뒤 예약·선언에 실패했다. 원인은 ``__cause__``에 있다."""

    def __init__(self, identity: ExportAttempt):
        super().__init__("export attempt 준비가 완료되지 않았습니다.")
        self.identity = identity


@dataclass(frozen=True)
class PreparedGridExport:
    identity: ExportAttempt
    descriptor: ScopeDescriptor


class KmaNowcastExportCollector:
    """명시 호출만 허용한다. 기본 collector/배포 설정은 활성화하지 않는다."""

    def __init__(self, repository, *, run_id: str, artifact_root: Path):
        self.repository = repository
        self.run_id = run_id
        self.heads = ExportHeads(repository)
        self.artifacts = ExportArtifactStore(artifact_root)
        # 이 collector(run)가 잠금 경합 재시도에 쓴 시간. KMA는 격자 준비와 ingest가
        # 서로 다른 thread에서 돌므로 갱신을 묶는다.
        self._lock_spent = 0.0
        self._lock_guard = threading.Lock()

    def _retry_lock(self, step):
        """export 단계 하나를 잠금 경합(lock_timeout·deadlock)에 한해 짧게 다시 시도한다.

        export 단계는 run -> attempt -> scope 순서로 run 행을 ``FOR UPDATE``로 잠근다.
        같은 run의 ingest transaction이 그 행을 잡고 있으면 3초 lock_timeout에 걸린다
        (운영 실측 2026-10-26: 대기 2.7~2.8초 뒤 취소). 각 단계는 실패하면 rollback되는
        한 transaction이라 쉬는 동안 아무것도 잡지 않는다. 단계당
        ``EXPORT_LOCK_STEP_SECONDS``, 이 collector(run) 전체 ``EXPORT_LOCK_RUN_SECONDS``를
        넘기면 원래 잠금 오류를 올려 호출자가 ``CODE:lock_timeout``으로 거절한다.
        재시도·포기마다 SQLSTATE만 남긴다.
        """
        started = time.monotonic()
        retries, contended = 0, False
        try:
            while True:
                try:
                    return step()
                except OperationalError as exc:
                    if not is_lock_conflict(exc):
                        raise
                    contended = True
                    waited = time.monotonic() - started
                    pause = random.uniform(*EXPORT_LOCK_PAUSE_SECONDS)
                    run_spent = self._lock_spent + waited
                    over_run = run_spent + pause > EXPORT_LOCK_RUN_SECONDS
                    if over_run or waited + pause > EXPORT_LOCK_STEP_SECONDS:
                        logger.warning(
                            "weather_export_lock_exhausted run_id=%s limit=%s retries=%d "
                            "sqlstate=%s waited_s=%.1f run_spent_s=%.1f run_budget_s=%.0f",
                            self.run_id, "run_budget" if over_run else "step_cap", retries,
                            _sqlstate(exc), waited, run_spent, EXPORT_LOCK_RUN_SECONDS,
                        )
                        raise
                    retries += 1
                    logger.warning(
                        "weather_export_lock_retry run_id=%s retry=%d sqlstate=%s waited_s=%.1f",
                        self.run_id, retries, _sqlstate(exc), waited,
                    )
                    time.sleep(pause)
        finally:
            if contended:
                with self._lock_guard:
                    self._lock_spent += time.monotonic() - started

    def _reject(self, identity, code, exc=None, *, reason=None):
        """attempt를 거절한다. 사유가 있으면 ``CODE:reason``으로 함께 남긴다(64자 이내)."""
        reason = reason or (failure_reason(exc) if exc is not None else None)
        error_code = f"{code}:{reason}"[:64] if reason else code
        logger.warning(
            "weather_export_rejected attempt_id=%s code=%s reason=%s detail=%s",
            identity.attempt_id, code, reason or "-",
            _proof_detail(exc) if exc is not None else "-",
        )
        try:
            attempts = self.heads.lifecycle.attempts
            with self.heads._transaction() as session:
                session.execute(
                    update(attempts)
                    .where(
                        attempts.c.attempt_id == identity.attempt_id,
                        attempts.c.owner_id == identity.owner_id,
                        attempts.c.status == "preparing",
                    )
                    .values(status="failed", error_code=error_code)
                )
        except Exception as record_exc:
            logger.warning(
                "weather_export_failure_record_deferred attempt_id=%s reason=%s",
                identity.attempt_id, failure_reason(record_exc),
            )

    def prepare(
        self,
        *,
        entity_id: str,
        nx: int,
        ny: int,
        location_ids: Sequence[str],
        dataset_key: str = "kma_ultra_short_nowcast",
    ):
        """HTTP 요청 전 선택/grid/policy를 고정한다. 실패 시 원본 경로만 계속한다."""
        try:
            descriptor = ScopeDescriptor(
                dataset_key=dataset_key,
                forecast_style={
                    "kma_ultra_short_nowcast": "nowcast",
                    "kma_ultra_short_forecast": "ultra_short",
                    "kma_short_forecast": "short",
                }[dataset_key],
                entity_type="weather_response",
                entity_id=entity_id,
                nx=nx,
                ny=ny,
                location_ids=tuple(sorted(location_ids)),
                normalizer_revision="kma-v1",
                winner_policy_revision="kma-base-v1",
                coverage_contract=(
                    "kma-grid-nowcast-v1"
                    if dataset_key == "kma_ultra_short_nowcast"
                    else "kma-grid-forecast-v1"
                ),
            )
            runs = self.heads.metadata.tables["weather_sync_runs"]
            with self.heads._transaction() as session:
                orchestrator_id = session.execute(
                    select(runs.c.orchestrator_run_id).where(runs.c.run_id == self.run_id)
                ).scalar_one()
            identity = self._begin_declared(
                descriptor, execution_mode="dagster" if orchestrator_id else "native"
            )
            return PreparedGridExport(identity, descriptor)
        except _PrepareRejected as rejected:
            self._reject(rejected.identity, "PREPARE_FAILED", rejected.__cause__)
            return None
        except Exception as exc:
            # DSN/원문 provider payload나 경로를 로그에 포함하지 않는다. attempt 생성 전 실패다.
            logger.warning(
                "weather_export_prepare_failed run_id=%s reason=%s",
                self.run_id, failure_reason(exc),
            )
            return None

    def _begin_declared(self, descriptor, *, execution_mode):
        """attempt를 만들고 scope를 예약·선언한다. 각 단계는 잠금 경합을 재시도한다.

        attempt를 만든 뒤의 실패는 ``_PrepareRejected``로 감싸 호출자가 그 attempt를
        사유와 함께 거절하게 한다.
        """
        identity = self._retry_lock(
            lambda: self.repository.begin_export_attempt(
                run_id=self.run_id,
                scope_key=descriptor.namespace,
                planned_sources=[descriptor.source_scope],
                execution_mode=execution_mode,
            )
        )
        try:
            self._retry_lock(lambda: self.heads.lifecycle.claim(identity))
            self._retry_lock(lambda: self.heads.declare(identity, descriptor))
        except Exception as exc:
            logger.warning(
                "weather_export_prepare_failed run_id=%s reason=%s",
                self.run_id, failure_reason(exc),
            )
            raise _PrepareRejected(identity) from exc
        return identity

    def ingest(
        self,
        prepared: PreparedGridExport | None,
        *,
        record: Mapping[str, Any],
        values: Sequence[WeatherValue],
    ) -> int:
        capture = None
        if prepared is not None:
            try:
                self._retry_lock(lambda: self._bind_source(prepared, record))
                self._retry_lock(
                    lambda: self.heads.lifecycle.expect_chunk(
                        prepared.identity, "grid", records=[record], values=values
                    )
                )
                capture = ExportCapture(
                    prepared.identity,
                    "grid",
                    {record["source_record_key"]: record["fetched_at"]},
                    self.artifacts,
                )
            except Exception as exc:
                logger.warning(
                    "weather_export_bind_failed attempt_id=%s reason=%s",
                    prepared.identity.attempt_id, failure_reason(exc),
                )
                self._reject(prepared.identity, "SOURCE_PROOF_FAILED", exc)
        # 원본 DB 오류는 정상적으로 호출자에게 전파한다. 실패 후 중복 ingest하지 않는다.
        loaded = self.repository.ingest_batch(
            source_records=[record],
            values=values,
            **({"export_capture": capture} if capture else {}),
        )
        if capture is not None:
            try:
                self._retry_lock(
                    lambda: self.heads.lifecycle.close_source(
                        prepared.identity, prepared.descriptor.source_scope, ["grid"]
                    )
                )
                self._retry_lock(lambda: self.heads.lifecycle.close_plan(prepared.identity))
            except Exception as exc:
                logger.warning(
                    "weather_export_seal_failed attempt_id=%s reason=%s",
                    prepared.identity.attempt_id, failure_reason(exc),
                )
                self._reject(prepared.identity, "PLAN_SEAL_FAILED", exc)
        return loaded

    def _bind_source(self, prepared, record):
        self.heads.bind_kma_response(prepared.identity, record)

    def ingest_fanout(
        self,
        prepared,
        *,
        record,
        values,
        location_ids,
        max_values=2500,
        max_locations=50,
        heartbeat=lambda: None,
    ):
        """예보 전체를 만들지 않고 기존 위치·fact batch 상한으로 원본/receipt를 저장한다.

        export proof 실패 후에도 원본 ingest는 한 번씩만 수행한다. 마지막 chunk와
        정상 run 종료를 확인하기 전에는 head가 공개되지 않는다.
        """
        if not 1 <= max_values <= 5000 or not 1 <= max_locations <= 50:
            raise ValueError("예보 공개 batch 상한이 올바르지 않습니다.")
        if not values:
            # 모든 값이 결측/이상값이어도 실제 조회한 raw source와 run lineage는
            # 한 번 남긴다. 빈 export는 이전 성공 head를 대체할 수 없다.
            if prepared:
                self._reject(prepared.identity, "NO_NORMALIZED_POINTS")
            heartbeat()
            return self.repository.ingest_batch(source_records=[record], values=[])
        bound = False
        if prepared:
            try:
                self._retry_lock(lambda: self._bind_source(prepared, record))
                bound = True
            except Exception as exc:
                self._reject(prepared.identity, "SOURCE_PROOF_FAILED", exc)
                logger.warning(
                    "weather_forecast_export_bind_failed attempt_id=%s reason=%s",
                    prepared.identity.attempt_id, failure_reason(exc),
                )
        loaded, batch, locations, keys = 0, [], 0, []

        def flush():
            nonlocal loaded, batch, locations, bound
            if not batch:
                return
            heartbeat()
            capture = None
            key = f"grid-{len(keys):04d}"
            if bound:
                try:
                    chunk = batch
                    self._retry_lock(
                        lambda: self.heads.lifecycle.expect_chunk(
                            prepared.identity, key, records=[record], values=chunk
                        )
                    )
                    capture = ExportCapture(
                        prepared.identity,
                        key,
                        {record["source_record_key"]: record["fetched_at"]},
                        self.artifacts,
                    )
                except Exception as exc:
                    bound = False
                    self._reject(prepared.identity, "CHUNK_PLAN_FAILED", exc)
            loaded += self.repository.ingest_batch(
                source_records=[record],
                values=batch,
                **({"export_capture": capture} if capture else {}),
            )
            keys.append(key)
            batch, locations = [], 0

        for location_id in sorted(location_ids):
            if batch and (len(batch) + len(values) > max_values or locations >= max_locations):
                flush()
            batch.extend(v.model_copy(update={"location_id": location_id}) for v in values)
            locations += 1
            if len(batch) >= max_values:
                flush()
        flush()
        if bound:
            try:
                self._retry_lock(
                    lambda: self.heads.lifecycle.close_source(
                        prepared.identity, prepared.descriptor.source_scope, keys
                    )
                )
                self._retry_lock(lambda: self.heads.lifecycle.close_plan(prepared.identity))
            except Exception as exc:
                self._reject(prepared.identity, "PLAN_SEAL_FAILED", exc)
        return loaded


@dataclass(frozen=True)
class ExportPublisherProgress:
    processed: int
    results: Mapping[str, int]
    next_cursor: str | None
    has_more: bool
    #: 그 후보만 미룬 오류의 분류별 개수(``failure_reason``). 원문 메시지는 담지 않는다.
    reasons: Mapping[str, int] = field(default_factory=dict)


_PUBLISHER_PROBE_SLOTS = threading.BoundedSemaphore(1)
_PREFETCH_SLOTS = threading.BoundedSemaphore(1)
_FAILED_NATIVE = frozenset({
    "failed", "partial", "partial_success", "canceled", "cancelled", "skipped", "abandoned",
})
DAILY_GENERATION_BATCH = 4
MAX_DAILY_GENERATIONS = 256
#: 일괄 퇴역 한 문장이 잠그는 attempt 상한. 잠긴 행은 기다리지 않고(SKIP LOCKED) 다음
#: tick에 다시 본다. 문장은 head transaction의 5초 statement 상한 안에서 끝난다.
RETIRE_BATCH = 500
#: 후보와 무관한 DB 장애(연결 끊김 등)가 연속 이만큼이면 이번 tick의 후보 처리를 멈춘다.
MAX_CONSECUTIVE_CANDIDATE_ERRORS = 3
#: 그 후보의 행·문장에만 걸린 일시 오류. 다음 후보는 영향을 받지 않는다.
_PER_CANDIDATE_SQLSTATES = frozenset({"55P03", "57014", "40P01", "40001"})
#: 후보 조회 한 번의 행 수 상한. 작은 식별 열만 읽으므로 RAM과 무관하다.
MAX_CANDIDATE_PAGE = 256
#: publisher transaction의 행 잠금 대기 상한(ms). 승격은 배타 artifact guard를 잡은 채
#: transaction을 이어 가므로 원본 ingest의 3초(3×deadlock_timeout)씩 기다리지 않는다.
#: 넘기면 그 후보만 lock_timeout으로 미뤄지고 다음 tick에 다시 본다.
PUBLISHER_LOCK_TIMEOUT_MS = 500
#: 승격 하나가 배타 guard를 놓은 뒤 쉬는 시간. 같은 guard를 10ms마다 다시 시도하는
#: API reader(2초 상한)와 collector 쓰기(5초 상한)가 연속 승격 사이에 들어오게 한다.
GUARD_YIELD_SECONDS = 0.03
#: 일별 요약 정리 한 문장의 최악 시간(statement 상한). tick 상한이 이보다 가까우면 건너뛴다.
DAILY_PRUNE_WORST_SECONDS = 5.0

#: owner_epochs의 epoch 문자열. 정수가 아니면 NULL이 되어 아래에서 위반으로 센다.
_EPOCH = "(CASE WHEN e.epoch ~ '^[0-9]{1,18}$' THEN CAST(e.epoch AS bigint) END)"


def _ownership_sql(violation: str) -> str:
    """claim한 모든 scope에 대한 owner 조건. ``violation``인 scope가 하나도 없어야 참이다.

    claim한 scope 집합(owner_epochs의 key)이 계획(planned_sources)과 같아야 하고 owner
    행이 빠진 scope도 위반이다. JSON 모양이 다르면 CASE가 먼저 거짓을 돌려 json 함수
    오류가 문장 전체를 실패시키지 않는다. 비교가 NULL(epoch가 정수가 아님 등)이면 위반으로
    센다. 전체 NULL은 COALESCE로 거짓이 되어 NOT도 안전하다.
    """
    return (
        "COALESCE(CASE WHEN json_typeof(weather_export_attempts.owner_epochs) = 'object'"
        " AND json_typeof(weather_export_attempts.planned_sources) = 'array' THEN ("
        "(SELECT array_agg(k ORDER BY k)"
        " FROM json_object_keys(weather_export_attempts.owner_epochs) AS k)"
        " = (SELECT array_agg(DISTINCT s ORDER BY s)"
        " FROM json_array_elements_text(weather_export_attempts.planned_sources) AS s)"
        " AND NOT EXISTS (SELECT 1"
        " FROM json_each_text(weather_export_attempts.owner_epochs) AS e(scope, epoch)"
        " LEFT JOIN weather_export_scope_owners AS o ON o.source_scope = e.scope"
        f" WHERE o.source_scope IS NULL OR COALESCE(({violation}), true))"
        ") ELSE false END, false)"
    )


#: 단건(`_publisher_terminal`)과 일괄 퇴역(`retire_settled_attempts`)이 같이 쓰는
#: SUPERSEDED 판정. 모든 claim scope가 실제 더 큰 epoch와 다른 active attempt로 넘어간
#: 경우만 참이다. 일부 이동·owner 누락·같은 epoch는 현재 scope가 섞인 시도를 실패로
#: 위장하지 않도록 거짓이다.
_SUPERSEDED_SQL = _ownership_sql(
    f"o.owner_epoch <= {_EPOCH}"
    " OR o.active_attempt_id = weather_export_attempts.attempt_id"
)
_SUPERSEDED_OWNERSHIP = text(_SUPERSEDED_SQL)
# text 조건은 SQLAlchemy의 not_()로 뒤집을 수 없다. 같은 SQL을 NOT으로 감싼다.
_NOT_SUPERSEDED_OWNERSHIP = text("NOT " + _SUPERSEDED_SQL)
#: 승격할 수 있는 현재 소유자. ``export_lifecycle._current_owners``와 같은 조건이다.
_CURRENT_OWNERSHIP = text(_ownership_sql(
    f"o.owner_epoch <> {_EPOCH}"
    " OR o.active_attempt_id <> weather_export_attempts.attempt_id"
))


def _publisher_heads(repository) -> ExportHeads:
    heads = ExportHeads(repository)
    heads.lifecycle.lock_timeout_ms = PUBLISHER_LOCK_TIMEOUT_MS
    return heads


def _pending(attempts):
    """partial index ``ix_weather_export_attempts_pending``의 predicate 그대로다.

    고정 상태를 literal로 남겨 generic plan에서도 partial index의 predicate를 증명한다.
    사용자 입력은 여기로 들어오지 않는다.
    """
    return (
        attempts.c.status.in_([literal_column("'preparing'"), literal_column("'sealed'")]),
        attempts.c.published_at.is_(None),
    )


def _no_generation(heads):
    return ~select(heads.generations.c.generation_id).where(
        heads.generations.c.attempt_id == heads.lifecycle.attempts.c.attempt_id
    ).exists()


def _native_failed(heads):
    runs = heads.metadata.tables["weather_sync_runs"]
    return select(runs.c.run_id).where(
        runs.c.run_id == heads.lifecycle.attempts.c.run_id,
        runs.c.status.in_(sorted(_FAILED_NATIVE)),
    ).exists()


def _retire(session, heads, attempt_ids, code, *, owner_id=None):
    """확정 퇴역 기록. 단건·일괄 경로가 같은 열(status/error_code)과 pin 정리를 쓴다."""
    attempts = heads.lifecycle.attempts
    statement = update(attempts).where(
        attempts.c.attempt_id.in_(attempt_ids),
        attempts.c.status.in_(["preparing", "sealed"]),
    )
    if owner_id is not None:
        statement = statement.where(attempts.c.owner_id == owner_id)
    session.execute(statement.values(status="failed", error_code=code))
    session.execute(heads.pins.delete().where(heads.pins.c.attempt_id.in_(attempt_ids)))


def _is_superseded(session, heads, attempt_id) -> bool:
    attempts = heads.lifecycle.attempts
    return session.scalar(
        select(literal_column("1")).where(
            attempts.c.attempt_id == attempt_id, _SUPERSEDED_OWNERSHIP, _no_generation(heads),
        )
    ) is not None


class TickStatusProbe:
    """한 tick 동안만 orchestrator run 상태를 run 단위로 기억한다.

    tick마다 새로 만들고 다음 tick으로 넘기지 않는다. 같은 run의 attempt 수백 개가 상태를
    한 번만 묻고, ``prefetch``는 한 묶음의 run을 조회 한 번으로 채운다. 묶음 조회가
    실패하면 기억하지 않고 후보별 조회로 넘어간다. 후보별 조회의 실패·시간 초과는 그
    tick의 나머지 동안 "모름"(None, 승격하지 않음)으로 기억해 같은 run을 다시 기다리지
    않는다. 다음 tick은 새 probe로 다시 묻는다.
    """

    def __init__(self, lookup, batch=None):
        self._lookup, self._batch = lookup, batch
        self._seen: dict[str, str | None] = {}
        self._lock = threading.Lock()
        self.calls = 0

    def cached(self, run_id: str) -> tuple[bool, str | None]:
        with self._lock:
            return run_id in self._seen, self._seen.get(run_id)

    def remember_unknown(self, run_id: str) -> None:
        with self._lock:
            self._seen.setdefault(run_id, None)

    def __call__(self, run_id: str) -> str | None:
        hit, status = self.cached(run_id)
        if hit:
            return status
        try:
            status = self._lookup(run_id)
        except Exception:
            self.remember_unknown(run_id)
            raise
        finally:
            with self._lock:
                self.calls += 1
        with self._lock:
            return self._seen.setdefault(run_id, status)

    def prefetch(self, run_ids) -> None:
        if self._batch is None:
            return
        with self._lock:
            missing = sorted({key for key in run_ids if key and key not in self._seen})
        if not missing:
            return
        try:
            found = call_with_deadline(
                lambda: self._batch(missing), timeout_seconds=5, slots=_PREFETCH_SLOTS,
            )
        except Exception as exc:
            # 묶음 조회 실패는 기억하지 않는다. 후보별 2초 조회가 이어서 묻는다.
            logger.warning("weather_export_status_prefetch_deferred reason=%s",
                           failure_reason(exc))
            return
        finally:
            with self._lock:
                self.calls += 1
        with self._lock:
            for key in missing:
                self._seen.setdefault(key, found.get(key))


def _probe(dagster_status, orchestrator_id):
    """business DB 연결 없이 orchestrator 상태만 본다. 조회 실패·시간 초과는 None이다."""
    if not orchestrator_id or dagster_status is None:
        return None
    cached = getattr(dagster_status, "cached", None)
    if callable(cached):
        hit, status = cached(orchestrator_id)
        if hit:
            return status
    try:
        return call_with_deadline(
            lambda: dagster_status(orchestrator_id), timeout_seconds=2,
            slots=_PUBLISHER_PROBE_SLOTS,
        )
    except Exception:
        remember = getattr(dagster_status, "remember_unknown", None)
        if callable(remember):
            remember(orchestrator_id)
        return None


def _prefetch(dagster_status, rows) -> None:
    prefetch = getattr(dagster_status, "prefetch", None)
    if callable(prefetch):
        prefetch([
            row.orchestrator_run_id for row in rows
            if row.execution_mode == "dagster" and row.run_status not in _FAILED_NATIVE
        ])


def _publisher_terminal(heads, identity, dagster_status):
    """확정 terminal만 retire한다. orchestration 조회 중 business DB를 반환한다."""
    with heads._transaction() as session:
        run, attempt = heads.lifecycle._locked(session, identity)
        native, mode = run["status"], attempt["execution_mode"]
        orchestrator_id = attempt["orchestrator_run_id"]
        epochs = attempt["owner_epochs"]
        if epochs:
            owners = heads.lifecycle.owners
            # claim과 같은 정렬 순서로 owner 행을 잠근 뒤 일괄 퇴역과 같은 SQL로 판정한다.
            session.execute(select(owners.c.source_scope).where(
                owners.c.source_scope.in_(sorted(epochs)),
            ).order_by(owners.c.source_scope).with_for_update())
            if _is_superseded(session, heads, identity.attempt_id):
                _retire(session, heads, [identity.attempt_id], "SUPERSEDED",
                        owner_id=identity.owner_id)
                return "SUPERSEDED", None
    status = None
    if native not in _FAILED_NATIVE and mode == "dagster":
        if not orchestrator_id or dagster_status is None:
            return "NOT_READY", None
        status = _probe(dagster_status, orchestrator_id)
        if status not in {"SUCCESS", "FAILURE", "CANCELED"}:
            return "NOT_READY", None
    if native not in _FAILED_NATIVE and status not in {"FAILURE", "CANCELED"}:
        return None, status
    with heads._transaction() as session:
        run, attempt = heads.lifecycle._locked(session, identity)
        if session.scalar(select(heads.generations.c.generation_id).where(
            heads.generations.c.attempt_id == identity.attempt_id
        )) is not None:
            return "ALREADY_COMMITTED", status
        # SUCCESS는 native commit보다 먼저 보일 수 있다. 반대로 확정 실패는
        # running CAS만 terminalize하며 기존 partial/success와 fact 개수는 보존한다.
        if run["status"] not in _FAILED_NATIVE and not (
            status in {"FAILURE", "CANCELED"}
            and attempt["orchestrator_run_id"] == orchestrator_id
        ):
            return "NOT_READY", status
        if run["status"] == "running":
            runs = heads.metadata.tables["weather_sync_runs"]
            session.execute(update(runs).where(
                runs.c.run_id == run["run_id"], runs.c.status == "running",
            ).values(
                status="failed", finished_at=datetime.now(UTC),
                error="EXPORT_ORCHESTRATOR_TERMINAL",
                export_status_pending=runs.c.export_status_scopes.is_not(None),
            ))
        _retire(session, heads, [identity.attempt_id], "CONFIRMED_TERMINAL",
                owner_id=identity.owner_id)
    return "REJECTED", status


def retire_settled_attempts(repository, *, limit: int = RETIRE_BATCH) -> dict[str, int]:
    """승격될 수 없는 pending을 `_publisher_terminal`과 같은 판정·기록으로 일괄 퇴역한다.

    - ``SUPERSEDED``: 모든 claim scope가 더 새 epoch·다른 attempt로 넘어갔다(같은 SQL).
    - ``CONFIRMED_TERMINAL``: 원본 native run이 실패·부분·취소로 끝났다(같은 상태 집합).
    generation이 이미 있는 attempt는 건드리지 않는다. 후보는 ``FOR UPDATE SKIP LOCKED``로만
    잡아 수집·단건 publisher가 잡은 행을 기다리지 않는다. 각 문장은 ``limit``개까지다.
    """
    if not 1 <= limit <= 5000:
        raise ValueError("일괄 퇴역 상한이 올바르지 않습니다.")
    if repository.engine.dialect.name != "postgresql":
        return {}
    heads = _publisher_heads(repository)
    attempts = heads.lifecycle.attempts
    retired = {}
    for code, condition in (
        ("SUPERSEDED", _SUPERSEDED_OWNERSHIP),
        # 단건 경로처럼 SUPERSEDED 판정이 먼저다. 둘 다 해당하면 SUPERSEDED로 남는다.
        ("CONFIRMED_TERMINAL", and_(_native_failed(heads), _NOT_SUPERSEDED_OWNERSHIP)),
    ):
        with heads._transaction() as session:
            ids = list(session.scalars(
                select(attempts.c.attempt_id)
                .where(*_pending(attempts), _no_generation(heads), condition)
                .order_by(attempts.c.attempt_id)
                .limit(limit)
                .with_for_update(of=attempts, skip_locked=True)
            ))
            if ids:
                _retire(session, heads, ids, code)
        retired[code] = len(ids)
    return retired


def _lock_free_verdict(row, dagster_status):
    """행 잠금 없이 정하는 1차 판정: ``NOT_READY``·``READY``·``LOCKED``.

    수집 중인 run의 행은 같은 run의 ingest가 잡고 있어 3초 lock_timeout에 걸린다.
    orchestrator가 아직 끝나지 않은 run(한 job이 여러 dataset을 이어 도는 경우)도
    `_publisher_terminal`은 NOT_READY로 끝난다. 둘 다 잠그지 않고 ``NOT_READY``다.
    native success이고 orchestrator가 정확히 SUCCESS(또는 native 실행)면 ``READY``,
    확정 실패 후보나 run을 알 수 없는 후보는 기존 잠금 경로(``LOCKED``)로 간다.
    """
    native = row.run_status
    if native in _FAILED_NATIVE:
        return "LOCKED"
    if row.execution_mode == "dagster":
        status = _probe(dagster_status, row.orchestrator_run_id)
        if status in {"FAILURE", "CANCELED"}:
            return "LOCKED"
        if status != "SUCCESS":
            return "NOT_READY"
    if native == "running":
        return "NOT_READY"
    return "READY" if native == "success" else "LOCKED"


def _promote_and_yield(heads, identity, artifacts, dagster_status):
    """승격은 처음부터 끝까지 배타 artifact guard를 잡는다. 놓은 뒤에는 잠깐 쉰다.

    연속 승격이 guard를 곧바로 다시 잡으면, 10ms마다 다시 시도하는 API reader와
    collector 쓰기가 상한(2초/5초)까지 들어오지 못한다. 성공·실패와 무관하게 쉰다.
    """
    try:
        return heads.promote(identity, artifacts, dagster_status=dagster_status)
    finally:
        time.sleep(GUARD_YIELD_SECONDS)


def _process_candidate(heads, artifacts, row, dagster_status, *, current_owner=False):
    verdict = _lock_free_verdict(row, dagster_status)
    if verdict == "NOT_READY":
        return "NOT_READY"
    identity = ExportAttempt(row.attempt_id, row.owner_id)
    if verdict == "READY" and current_owner and row.plan_id is not None:
        # fresh 조회가 현재 소유자·계획 닫힘·native success를 이미 확인했다. 퇴역 확인
        # transaction을 건너뛴다. 그 뒤 소유권이 넘어가면 승격의 소유권 확인이 거절해
        # NOT_READY가 되고, 다음 tick의 일괄 퇴역이 같은 SQL로 정리한다.
        outcome = _promote_and_yield(heads, identity, artifacts, dagster_status)
    else:
        outcome, _ = _publisher_terminal(heads, identity, dagster_status)
        if outcome is not None:
            return outcome
        # begin/claim 후 declare 이전의 crash도 terminal 복구한다. plan이
        # 없는 성공/미확정 후보를 승격하거나 성공으로 추측하지 않는다.
        if row.plan_id is None:
            return "NOT_READY"
        outcome = _promote_and_yield(heads, identity, artifacts, dagster_status)
    if outcome == "REJECTED":
        _publisher_terminal(heads, identity, dagster_status)
    return outcome


def _systemic(exc: BaseException) -> bool:
    """후보와 무관한 DB 장애인가. 잠금·문장 시간 초과와 코드 오류는 그 후보만의 일이다."""
    if not isinstance(exc, DBAPIError):
        return False
    return bool(exc.connection_invalidated) or _sqlstate(exc) not in _PER_CANDIDATE_SQLSTATES


class PublisherTally:
    """한 tick의 결과·미룬 사유. DB 장애가 연속 상한에 닿으면 ``stopped``가 된다.

    ``seen``은 같은 tick의 phase가 같은 후보를 두 번 처리하지 않게 한다.
    """

    def __init__(self) -> None:
        self.results: Counter[str] = Counter()
        self.reasons: Counter[str] = Counter()
        self.seen: set[str] = set()
        self.consecutive = 0
        self.stopped = False


def _drain(heads, artifacts, rows, *, deadline, dagster_status, tally, current_owner=False):
    """후보를 차례로 처리한다. 한 후보의 잠금 시간 초과·예상 밖 오류는 그 후보만 미룬다.

    반환값은 (처리한 수, 마지막으로 처리한 후보)다. 미룬 후보와 이미 본 후보도 처리 수에
    들어가 keyset이 그 뒤로 넘어간다. 미룬 후보는 다음 sweep에서 다시 본다.
    """
    processed, last = 0, None
    for row in rows:
        if tally.stopped or time.monotonic() >= deadline:
            break
        processed, last = processed + 1, row
        if row.attempt_id in tally.seen:
            continue
        tally.seen.add(row.attempt_id)
        try:
            outcome = _process_candidate(
                heads, artifacts, row, dagster_status, current_owner=current_owner,
            )
        except (ExportArtifactError, OSError):
            # 손상/누락 파일은 이전 head를 그대로 남긴다. 후속 sweep/재수집으로 복구한다.
            outcome = "NOT_READY"
            tally.consecutive = 0
        except Exception as exc:
            # 한 후보의 잠금 시간 초과·statement timeout·예상 밖 오류가 tick 전체를
            # 미루지 않게 한다. 사유는 분류만 남기고 원문 메시지는 남기지 않는다.
            reason = failure_reason(exc)
            logger.warning(
                "weather_export_candidate_deferred attempt_id=%s reason=%s",
                row.attempt_id, reason,
            )
            outcome = "DEFERRED"
            tally.reasons[reason] += 1
            tally.consecutive = tally.consecutive + 1 if _systemic(exc) else 0
            tally.stopped = tally.consecutive >= MAX_CONSECUTIVE_CANDIDATE_ERRORS
        else:
            tally.consecutive = 0
        tally.results[outcome] += 1
    return processed, last


def _candidate_columns(heads):
    attempts = heads.lifecycle.attempts
    runs = heads.metadata.tables["weather_sync_runs"]
    return (
        attempts.c.attempt_id,
        attempts.c.owner_id,
        attempts.c.execution_mode,
        attempts.c.orchestrator_run_id,
        runs.c.status.label("run_status"),
        heads.plans.c.attempt_id.label("plan_id"),
    )


def publish_pending_exports(
    repository,
    artifact_root: Path,
    *,
    cursor: str | None = None,
    limit: int = 4,
    budget_seconds: float = 20,
    dagster_status: Callable[[str], str | None] | None = None,
    daily_summaries: bool = False,
    tally: PublisherTally | None = None,
):
    """keyset sweep을 재개한다. pending 한 건이 뒤 성공 후보를 막지 않는다.

    callback은 exact DagsterRunStatus의 이름만 반환한다. 실행 중/조회 실패는 승격하지 않는다.
    수집 중인 run이나 아직 끝나지 않은 orchestrator의 후보는 행 잠금 없이 NOT_READY다.
    한 후보의 잠금 시간 초과·예상 밖 오류는 그 후보만 ``DEFERRED``로 미루고 계속한다.
    ``limit``은 한 번에 읽는 작은 후보 행 수다. 승격(파일 검증)은 한 후보씩 한다.
    이 함수는 collector job 밖에서 호출한다. daemon/전국 자동활성화는 별도 설정이다.
    """
    if not 1 <= limit <= MAX_CANDIDATE_PAGE or not 0 < budget_seconds <= 60:
        raise ValueError("publisher 실행 예산이 올바르지 않습니다.")
    deadline = time.monotonic() + budget_seconds
    heads, artifacts = _publisher_heads(repository), ExportArtifactStore(artifact_root)
    attempts = heads.lifecycle.attempts
    runs = heads.metadata.tables["weather_sync_runs"]
    own = tally is None
    tally = tally or PublisherTally()
    before_results, before_reasons = Counter(tally.results), Counter(tally.reasons)
    with heads._transaction() as session:
        query = (
            select(*_candidate_columns(heads))
            .select_from(
                attempts.outerjoin(heads.plans, heads.plans.c.attempt_id == attempts.c.attempt_id)
                .outerjoin(runs, runs.c.run_id == attempts.c.run_id)
            )
            .where(*_pending(attempts), _no_generation(heads))
        )
        if cursor:
            query = query.where(attempts.c.attempt_id > cursor)
        rows = session.execute(query.order_by(attempts.c.attempt_id).limit(limit + 1)).all()
    _prefetch(dagster_status, rows[:limit])
    processed, last = _drain(
        heads, artifacts, rows[:limit], deadline=deadline,
        dagster_status=dagster_status, tally=tally,
    )
    more = processed < len(rows)
    if daily_summaries and time.monotonic() < deadline:
        refresh_publisher_daily(repository, artifact_root, deadline=deadline)
    last_id = last.attempt_id if last is not None else cursor
    results = tally.results if own else tally.results - before_results
    reasons = tally.reasons if own else tally.reasons - before_reasons
    return ExportPublisherProgress(
        processed, dict(results), last_id if more else None, more, dict(reasons),
    )


def publish_fresh_exports(
    repository,
    artifact_root: Path,
    *,
    budget_seconds: float,
    dagster_status: Callable[[str], str | None] | None = None,
    limit: int = 32,
    max_candidates: int = 1024,
    tally: PublisherTally | None = None,
) -> ExportPublisherProgress:
    """가장 최근에 SUCCESS로 끝난 run의, 아직 시도하지 않은 현재 소유 attempt부터 승격한다.

    run이 끝난 뒤 몇 분 안에 새 회차가 공개되도록 sweep보다 먼저 돈다. 대상은 계획이 닫혔고
    원본 run이 success이며 모든 claim scope의 현재 owner(같은 scope의 최신 attempt)인
    ``preparing`` attempt다. 한 번 승격을 시도해 ``sealed``가 된 후보(증명 실패·CONFLICT
    등)는 여기서 다시 고르지 않고 sweep이 이어서 본다. 같은 tick 안에서는
    (finished_at, attempt_id) 키셋으로 한 번씩만 본다. cursor는 남기지 않는다.
    """
    if not 1 <= limit <= MAX_CANDIDATE_PAGE or not 0 < budget_seconds <= 60 or max_candidates < 1:
        raise ValueError("publisher 실행 예산이 올바르지 않습니다.")
    deadline = time.monotonic() + budget_seconds
    heads, artifacts = _publisher_heads(repository), ExportArtifactStore(artifact_root)
    attempts = heads.lifecycle.attempts
    runs = heads.metadata.tables["weather_sync_runs"]
    own = tally is None
    tally = tally or PublisherTally()
    before_results, before_reasons = Counter(tally.results), Counter(tally.reasons)
    total, after, more = 0, None, False
    while total < max_candidates and not tally.stopped and time.monotonic() < deadline:
        with heads._transaction() as session:
            query = (
                select(*_candidate_columns(heads), runs.c.finished_at)
                .select_from(
                    attempts.join(runs, runs.c.run_id == attempts.c.run_id)
                    .join(heads.plans, heads.plans.c.attempt_id == attempts.c.attempt_id)
                )
                .where(
                    *_pending(attempts),
                    attempts.c.status == literal_column("'preparing'"),
                    attempts.c.plan_closed_at.is_not(None),
                    runs.c.status == literal_column("'success'"),
                    runs.c.finished_at.is_not(None),
                    _no_generation(heads),
                    _CURRENT_OWNERSHIP,
                )
            )
            if after is not None:
                query = query.where(
                    tuple_(runs.c.finished_at, attempts.c.attempt_id) < tuple_(*after)
                )
            rows = session.execute(
                query.order_by(runs.c.finished_at.desc(), attempts.c.attempt_id.desc())
                .limit(min(limit, max_candidates - total))
            ).all()
        if not rows:
            break
        _prefetch(dagster_status, rows)
        processed, last = _drain(
            heads, artifacts, rows, deadline=deadline,
            dagster_status=dagster_status, tally=tally, current_owner=True,
        )
        total += processed
        if last is not None:
            after = (last.finished_at, last.attempt_id)
        if processed < len(rows):
            more = True
            break
    results = tally.results if own else tally.results - before_results
    reasons = tally.reasons if own else tally.reasons - before_reasons
    return ExportPublisherProgress(total, dict(results), None, more, dict(reasons))


def refresh_publisher_daily(
    repository, artifact_root: Path, *, deadline: float, hard_deadline: float | None = None,
) -> Mapping[str, Any]:
    """작은 원자적 checkpoint batch를 유한하게 drain하고 마지막에 한 번 정리한다.

    ``hard_deadline``(tick 상한)까지 정리 한 문장의 최악 시간이 남지 않으면 정리는
    다음 tick으로 미룬다.
    """
    from .export_daily_store import prune_daily_summaries, refresh_daily_summaries

    total, more = 0, True
    try:
        artifacts = ExportArtifactStore(artifact_root)
        for _ in range(MAX_DAILY_GENERATIONS // DAILY_GENERATION_BATCH):
            if time.monotonic() >= deadline:
                break
            progress = refresh_daily_summaries(repository, artifacts, limit=DAILY_GENERATION_BATCH)
            total += progress["processed"]
            more = progress["has_more"]
            # race/읽기 실패는 checkpoint를 추측해 넘기지 않는다. 다음 tick에 재개한다.
            if progress["processed"] == 0 or not more:
                break
        now = time.monotonic()
        if now < deadline and (
            hard_deadline is None or hard_deadline - now >= DAILY_PRUNE_WORST_SECONDS
        ):
            prune_daily_summaries(repository)
            # snapshot handle 정리는 일별 정리 뒤 남은 tick 상한 안에서만 한다(문장 상한 동일).
            now = time.monotonic()
            if hard_deadline is None or hard_deadline - now >= DAILY_PRUNE_WORST_SECONDS:
                _prune_snapshot_handles(repository)
    except Exception as exc:
        more = True
        logger.warning("weather_daily_summary_refresh_deferred reason=%s", failure_reason(exc))
    return {"processed": total, "has_more": more}


def _prune_snapshot_handles(repository) -> None:
    """만료 뒤 오래된 snapshot handle을 작게 지운다. 실패는 일별 요약과 분리해 미룬다."""
    from .export_snapshots import prune_export_snapshots

    try:
        prune_export_snapshots(repository)
    except Exception as exc:
        logger.warning("weather_export_snapshot_prune_deferred reason=%s", type(exc).__name__)


def dagster_status_lookup(instance) -> Callable[[str], str | None]:
    """실제 instance adapter. is_finished나 bool을 성공으로 바꾸지 않는다."""

    def lookup(run_id: str) -> str | None:
        run = instance.get_run_by_id(run_id)
        return run.status.name if run is not None else None

    return lookup
