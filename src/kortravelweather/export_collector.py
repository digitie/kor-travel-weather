"""실제 KMA 단일 격자 collector의 명시 opt-in adapter와 bounded publisher.

원본 fact 저장은 그대로 유지하고, export 준비 실패는 원본 수집을 실패시키지 않는다.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kortravelcommon.deadline import call_with_deadline
from sqlalchemy import literal_column, select, update
from sqlalchemy.exc import OperationalError

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


_PUBLISHER_PROBE_SLOTS = threading.BoundedSemaphore(1)
_FAILED_NATIVE = frozenset({
    "failed", "partial", "partial_success", "canceled", "cancelled", "skipped", "abandoned",
})
DAILY_GENERATION_BATCH = 4
MAX_DAILY_GENERATIONS = 256


def _publisher_terminal(heads, identity, dagster_status):
    """확정 terminal만 retire한다. orchestration 조회 중 business DB를 반환한다."""
    with heads._transaction() as session:
        run, attempt = heads.lifecycle._locked(session, identity)
        native, mode = run["status"], attempt["execution_mode"]
        orchestrator_id = attempt["orchestrator_run_id"]
        epochs = attempt["owner_epochs"]
        if epochs and set(epochs) == set(attempt["planned_sources"]):
            owners = heads.lifecycle.owners
            rows = session.execute(select(owners).where(
                owners.c.source_scope.in_(sorted(epochs)),
            ).order_by(owners.c.source_scope).with_for_update()).mappings().all()
            # 모든 scope가 실제 더 새 epoch로 넘어간 것만 퇴역한다. 일부 충돌,
            # 누락/미확정 owner는 현재 scope가 섞인 시도를 실패로 위장하지 않는다.
            superseded = len(rows) == len(epochs) and all(
                row["owner_epoch"] > epochs[row["source_scope"]]
                and row["active_attempt_id"] != identity.attempt_id for row in rows
            )
            if superseded and session.scalar(select(heads.generations.c.generation_id).where(
                heads.generations.c.attempt_id == identity.attempt_id,
            )) is None:
                session.execute(update(heads.lifecycle.attempts).where(
                    heads.lifecycle.attempts.c.attempt_id == identity.attempt_id,
                    heads.lifecycle.attempts.c.owner_id == identity.owner_id,
                    heads.lifecycle.attempts.c.status.in_(["preparing", "sealed"]),
                ).values(status="failed", error_code="SUPERSEDED"))
                session.execute(heads.pins.delete().where(
                    heads.pins.c.attempt_id == identity.attempt_id,
                ))
                return "SUPERSEDED", None
    status = None
    if native not in _FAILED_NATIVE and mode == "dagster":
        if not orchestrator_id or dagster_status is None:
            return "NOT_READY", None
        try:
            status = call_with_deadline(
                lambda: dagster_status(orchestrator_id), timeout_seconds=2,
                slots=_PUBLISHER_PROBE_SLOTS,
            )
        except Exception:
            return "NOT_READY", None
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
        session.execute(update(heads.lifecycle.attempts).where(
            heads.lifecycle.attempts.c.attempt_id == identity.attempt_id,
            heads.lifecycle.attempts.c.owner_id == identity.owner_id,
            heads.lifecycle.attempts.c.status.in_(["preparing", "sealed"]),
        ).values(status="failed", error_code="CONFIRMED_TERMINAL"))
        session.execute(heads.pins.delete().where(heads.pins.c.attempt_id == identity.attempt_id))
    return "REJECTED", status


def publish_pending_exports(
    repository,
    artifact_root: Path,
    *,
    cursor: str | None = None,
    limit: int = 4,
    budget_seconds: float = 20,
    dagster_status: Callable[[str], str | None] | None = None,
    daily_summaries: bool = False,
):
    """keyset sweep을 재개한다. pending 한 건이 뒤 성공 후보를 막지 않는다.

    callback은 exact DagsterRunStatus의 이름만 반환한다. 실행 중/조회 실패는 승격하지 않는다.
    이 함수는 collector job 밖에서 호출한다. daemon/전국 자동활성화는 별도 설정이다.
    """
    if not 1 <= limit <= 32 or not 0 < budget_seconds <= 60:
        raise ValueError("publisher 실행 예산이 올바르지 않습니다.")
    deadline = time.monotonic() + budget_seconds
    heads, artifacts = ExportHeads(repository), ExportArtifactStore(artifact_root)
    attempts = heads.lifecycle.attempts
    with heads._transaction() as session:
        query = (
            select(attempts.c.attempt_id, attempts.c.owner_id,
                   heads.plans.c.attempt_id.label("plan_id"))
            .outerjoin(heads.plans, heads.plans.c.attempt_id == attempts.c.attempt_id)
            .where(
                # 고정 상태를 literal로 남겨 generic plan에서도 partial index의
                # predicate를 증명한다. 사용자 입력은 여기로 들어오지 않는다.
                attempts.c.status.in_([
                    literal_column("'preparing'"), literal_column("'sealed'"),
                ]),
                attempts.c.published_at.is_(None),
                ~select(heads.generations.c.generation_id)
                .where(heads.generations.c.attempt_id == attempts.c.attempt_id)
                .exists(),
            )
        )
        if cursor:
            query = query.where(attempts.c.attempt_id > cursor)
        rows = session.execute(query.order_by(attempts.c.attempt_id).limit(limit + 1)).all()
    results, processed, last = {}, 0, cursor
    for row in rows[:limit]:
        if time.monotonic() >= deadline:
            break
        identity = ExportAttempt(row.attempt_id, row.owner_id)
        try:
            outcome, _ = _publisher_terminal(heads, identity, dagster_status)
            if outcome is None:
                # begin/claim 후 declare 이전의 crash도 terminal 복구한다. plan이
                # 없는 성공/미확정 후보를 승격하거나 성공으로 추측하지 않는다.
                outcome = (
                    heads.promote(identity, artifacts, dagster_status=dagster_status)
                    if row.plan_id is not None else "NOT_READY"
                )
                if outcome == "REJECTED":
                    _publisher_terminal(heads, identity, dagster_status)
        except (ExportArtifactError, OSError):
            # 손상/누락 파일은 이전 head를 그대로 남긴다. 후속 sweep/재수집으로 복구한다.
            outcome = "NOT_READY"
        results[outcome] = results.get(outcome, 0) + 1
        processed += 1
        last = row.attempt_id
    more = processed < len(rows)
    if daily_summaries and time.monotonic() < deadline:
        refresh_publisher_daily(repository, artifact_root, deadline=deadline)
    return ExportPublisherProgress(processed, results, last if more else None, more)


def refresh_publisher_daily(
    repository, artifact_root: Path, *, deadline: float,
) -> Mapping[str, Any]:
    """작은 원자적 checkpoint batch를 유한하게 drain하고 마지막에 한 번 정리한다."""
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
        if time.monotonic() < deadline:
            prune_daily_summaries(repository)
    except Exception:
        more = True
        logger.warning("weather_daily_summary_refresh_deferred")
    return {"processed": total, "has_more": more}


def dagster_status_lookup(instance) -> Callable[[str], str | None]:
    """실제 instance adapter. is_finished나 bool을 성공으로 바꾸지 않는다."""

    def lookup(run_id: str) -> str | None:
        run = instance.get_run_by_id(run_id)
        return run.status.name if run is not None else None

    return lookup
