"""실제 KMA 단일 격자 collector의 명시 opt-in adapter와 bounded publisher.

원본 fact 저장은 그대로 유지하고, export 준비 실패는 원본 수집을 실패시키지 않는다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kortravelcommon.deadline import call_with_deadline
from sqlalchemy import select, update

from .export_artifacts import ExportArtifactError, ExportArtifactStore
from .export_heads import ExportHeads, ScopeDescriptor
from .export_receipts import ExportAttempt, ExportCapture
from .models import WeatherValue

logger = logging.getLogger(__name__)


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

    def _reject(self, identity, code):
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
                    .values(status="failed", error_code=code)
                )
        except Exception:
            logger.warning(
                "weather_export_failure_record_deferred attempt_id=%s", identity.attempt_id
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
        identity = None
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
            identity = self.repository.begin_export_attempt(
                run_id=self.run_id,
                scope_key=descriptor.namespace,
                planned_sources=[descriptor.source_scope],
                execution_mode="dagster" if orchestrator_id else "native",
            )
            self.heads.lifecycle.claim(identity)
            self.heads.declare(identity, descriptor)
            return PreparedGridExport(identity, descriptor)
        except Exception:
            # DSN/원문 provider payload나 경로를 로그에 포함하지 않는다.
            logger.warning("weather_export_prepare_failed run_id=%s", self.run_id)
            if identity is not None:
                self._reject(identity, "PREPARE_FAILED")
            return None

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
                self._bind_source(prepared, record)
                self.heads.lifecycle.expect_chunk(
                    prepared.identity, "grid", records=[record], values=values
                )
                capture = ExportCapture(
                    prepared.identity,
                    "grid",
                    {record["source_record_key"]: record["fetched_at"]},
                    self.artifacts,
                )
            except Exception:
                logger.warning(
                    "weather_export_bind_failed attempt_id=%s", prepared.identity.attempt_id
                )
                self._reject(prepared.identity, "SOURCE_PROOF_FAILED")
        # 원본 DB 오류는 정상적으로 호출자에게 전파한다. 실패 후 중복 ingest하지 않는다.
        loaded = self.repository.ingest_batch(
            source_records=[record],
            values=values,
            **({"export_capture": capture} if capture else {}),
        )
        if capture is not None:
            try:
                self.heads.lifecycle.close_source(
                    prepared.identity, prepared.descriptor.source_scope, ["grid"]
                )
                self.heads.lifecycle.close_plan(prepared.identity)
            except Exception:
                logger.warning(
                    "weather_export_seal_failed attempt_id=%s", prepared.identity.attempt_id
                )
                self._reject(prepared.identity, "PLAN_SEAL_FAILED")
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
                self.heads.bind_kma_response(prepared.identity, record)
                bound = True
            except Exception:
                self._reject(prepared.identity, "SOURCE_PROOF_FAILED")
                logger.warning(
                    "weather_forecast_export_bind_failed attempt_id=%s",
                    prepared.identity.attempt_id,
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
                    self.heads.lifecycle.expect_chunk(
                        prepared.identity, key, records=[record], values=batch
                    )
                    capture = ExportCapture(
                        prepared.identity,
                        key,
                        {record["source_record_key"]: record["fetched_at"]},
                        self.artifacts,
                    )
                except Exception:
                    bound = False
                    self._reject(prepared.identity, "CHUNK_PLAN_FAILED")
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
                self.heads.lifecycle.close_source(
                    prepared.identity, prepared.descriptor.source_scope, keys
                )
                self.heads.lifecycle.close_plan(prepared.identity)
            except Exception:
                self._reject(prepared.identity, "PLAN_SEAL_FAILED")
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


def _publisher_terminal(heads, identity, dagster_status):
    """확정 terminal만 retire한다. orchestration 조회 중 business DB를 반환한다."""
    with heads._transaction() as session:
        run, attempt = heads.lifecycle._locked(session, identity)
        native, mode = run["status"], attempt["execution_mode"]
        orchestrator_id = attempt["orchestrator_run_id"]
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
    heads, artifacts = ExportHeads(repository), ExportArtifactStore(artifact_root)
    attempts = heads.lifecycle.attempts
    with heads._transaction() as session:
        query = (
            select(attempts.c.attempt_id, attempts.c.owner_id)
            .join(heads.plans, heads.plans.c.attempt_id == attempts.c.attempt_id)
            .where(
                attempts.c.status.in_(["preparing", "sealed"]),
                ~select(heads.generations.c.generation_id)
                .where(heads.generations.c.attempt_id == attempts.c.attempt_id)
                .exists(),
            )
        )
        if cursor:
            query = query.where(attempts.c.attempt_id > cursor)
        rows = session.execute(query.order_by(attempts.c.attempt_id).limit(limit + 1)).all()
    deadline = time.monotonic() + budget_seconds
    results, processed, last = {}, 0, cursor
    for row in rows[:limit]:
        if time.monotonic() >= deadline:
            break
        identity = ExportAttempt(*row)
        try:
            outcome, _ = _publisher_terminal(heads, identity, dagster_status)
            if outcome is None:
                outcome = heads.promote(identity, artifacts, dagster_status=dagster_status)
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
        from .export_daily_store import prune_daily_summaries, refresh_daily_summaries

        try:
            refresh_daily_summaries(repository, artifacts, limit=1)
            if time.monotonic() < deadline:
                prune_daily_summaries(repository)
        except Exception:
            logger.warning("weather_daily_summary_refresh_deferred")
    return ExportPublisherProgress(processed, results, last if more else None, more)


def dagster_status_lookup(instance) -> Callable[[str], str | None]:
    """실제 instance adapter. is_finished나 bool을 성공으로 바꾸지 않는다."""

    def lookup(run_id: str) -> str | None:
        run = instance.get_run_by_id(run_id)
        return run.status.name if run is not None else None

    return lookup
