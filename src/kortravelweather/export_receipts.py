"""Map export attempt·chunk의 원자적 검증 기록. 성공 head 승격은 여기서 하지 않는다."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    select,
)
from sqlalchemy.orm import Session

from .export_artifacts import ExportArtifactError, ExportArtifactStore, PreparedExportChunk


@dataclass(frozen=True)
class ExportAttempt:
    attempt_id: str
    owner_id: str


@dataclass(frozen=True)
class ExportCapture:
    attempt: ExportAttempt
    chunk_key: str
    # 이번 실제 fetch 시각. canonical fact의 최초 known_at과 별개다.
    source_observed_at: Mapping[str, datetime]
    artifacts: ExportArtifactStore


def source_scope_key(provider: str, dataset: str, entity_type: str, entity_id: str) -> str:
    data = json.dumps(
        [provider, dataset, entity_type, entity_id], ensure_ascii=False, separators=(",", ":")
    ).encode()
    return hashlib.sha256(data).hexdigest()


def export_receipt_tables(metadata: MetaData) -> tuple[Table, Table]:
    if "weather_export_attempts" in metadata.tables:
        return metadata.tables["weather_export_attempts"], metadata.tables["weather_export_chunks"]
    attempts = Table(
        "weather_export_attempts",
        metadata,
        Column("attempt_id", String(36), primary_key=True),
        # 기존 sync-run 이력 보존/삭제 정책을 FK로 묶지 않는다.
        Column("run_id", String(64), nullable=False, index=True),
        Column("owner_id", String(36), nullable=False),
        Column("scope_key", String(240), nullable=False),
        Column("execution_mode", String(16), nullable=False),
        Column("orchestrator_run_id", String(64)),
        Column("planned_sources", JSON, nullable=False),
        Column("plan_digest", String(64), nullable=False),
        Column("owner_epochs", JSON),
        Column("plan_reference_count", Integer, nullable=False, default=0, server_default="0"),
        Column("plan_closed_at", DateTime(timezone=True)),
        Column("seal_digest", String(64)),
        Column("sealed_at", DateTime(timezone=True)),
        Column("published_at", DateTime(timezone=True)),
        Column("status", String(16), nullable=False),
        Column("error_code", String(64)),
        Column("created_at", DateTime(timezone=True), nullable=False),
        CheckConstraint("execution_mode IN ('native','dagster')", name="ck_weather_export_mode"),
        CheckConstraint(
            "status IN ('preparing','sealed','failed')", name="ck_weather_export_status"
        ),
        CheckConstraint(
            "execution_mode != 'dagster' OR orchestrator_run_id IS NOT NULL",
            name="ck_weather_export_dagster_id",
        ),
    )
    Index(
        "ix_weather_export_attempts_pending", attempts.c.attempt_id,
        postgresql_where=(attempts.c.status.in_(["preparing", "sealed"])
                          & attempts.c.published_at.is_(None)),
    )
    chunks = Table(
        "weather_export_chunks",
        metadata,
        Column(
            "attempt_id",
            String(36),
            ForeignKey("weather_export_attempts.attempt_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        Column("chunk_key", String(64), primary_key=True),
        Column("artifact_digest", String(64), nullable=False),
        Column("byte_count", Integer, nullable=False),
        Column("point_count", Integer, nullable=False),
        Column("covered_sources", JSON, nullable=False),
        Column("source_point_counts", JSON),
        Column("source_collected_at", DateTime(timezone=True), nullable=False),
        Column("recorded_at", DateTime(timezone=True), nullable=False),
        CheckConstraint("byte_count BETWEEN 0 AND 8388608", name="ck_weather_export_chunk_bytes"),
        CheckConstraint("point_count BETWEEN 0 AND 5000", name="ck_weather_export_chunk_points"),
    )
    return attempts, chunks


def create_export_attempt(
    session: Session,
    *,
    metadata: MetaData,
    run_id: str,
    scope_key: str,
    planned_sources: Sequence[str],
    execution_mode: Literal["native", "dagster"],
    orchestrator_run_id: str | None,
) -> ExportAttempt:
    if (
        not scope_key
        or len(scope_key) > 240
        or not planned_sources
        or len(planned_sources) > 10000
        or execution_mode not in {"native", "dagster"}
    ):
        raise ExportArtifactError("export attempt 계획이 올바르지 않습니다.")
    planned = sorted(set(planned_sources))
    if any(len(key) != 64 or any(c not in "0123456789abcdef" for c in key) for key in planned):
        raise ExportArtifactError("export scope identity가 올바르지 않습니다.")
    run_table = metadata.tables["weather_sync_runs"]
    run = (
        session.execute(select(run_table).where(run_table.c.run_id == run_id).with_for_update())
        .mappings()
        .one_or_none()
    )
    if run is None or run["status"] != "running":
        raise ExportArtifactError("진행 중인 원천 sync run이 필요합니다.")
    recorded_id = run["orchestrator_run_id"]
    if execution_mode == "dagster":
        if not orchestrator_run_id or recorded_id != orchestrator_run_id:
            raise ExportArtifactError("Dagster 실행 소유권이 일치하지 않습니다.")
    elif recorded_id is not None or orchestrator_run_id is not None:
        raise ExportArtifactError("Dagster 실행을 native로 강등할 수 없습니다.")
    attempts, _ = export_receipt_tables(metadata)
    attempt = ExportAttempt(str(uuid4()), str(uuid4()))
    digest = hashlib.sha256(json.dumps(planned, separators=(",", ":")).encode()).hexdigest()
    session.execute(
        attempts.insert().values(
            attempt_id=attempt.attempt_id,
            owner_id=attempt.owner_id,
            run_id=run_id,
            scope_key=scope_key,
            execution_mode=execution_mode,
            orchestrator_run_id=recorded_id,
            planned_sources=planned,
            plan_digest=digest,
            status="preparing",
            created_at=datetime.now(UTC),
        )
    )
    return attempt


def record_export_chunk(
    session: Session,
    *,
    metadata: MetaData,
    capture: ExportCapture,
    run_id: str,
    covered_sources: Sequence[str],
    chunk: PreparedExportChunk,
    input_point_count: int | None = None,
    input_digest: str | None = None,
    source_point_counts: Mapping[str, int] | None = None,
) -> None:
    if (
        not capture.chunk_key
        or len(capture.chunk_key) > 64
        or not capture.chunk_key.isascii()
        or any(not (c.isalnum() or c in "-_") for c in capture.chunk_key)
    ):
        raise ExportArtifactError("export chunk identity가 올바르지 않습니다.")
    attempts, chunks = export_receipt_tables(metadata)
    attempt = (
        session.execute(
            select(attempts)
            .where(
                attempts.c.attempt_id == capture.attempt.attempt_id,
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if (
        attempt is None
        or attempt["owner_id"] != capture.attempt.owner_id
        or attempt["run_id"] != run_id
        or attempt["status"] == "failed"
    ):
        raise ExportArtifactError("export attempt 소유권이 일치하지 않습니다.")
    covered = sorted(set(covered_sources))
    if attempt["owner_epochs"] is not None:
        from .export_lifecycle import validate_planned_capture

        validate_planned_capture(
            session,
            metadata,
            attempt,
            capture.chunk_key,
            covered,
            input_point_count=input_point_count,
            input_digest=input_digest,
        )
    if not covered or not set(covered).issubset(attempt["planned_sources"]):
        raise ExportArtifactError("export chunk가 예정 scope를 벗어났습니다.")
    if source_point_counts is not None and (
        set(source_point_counts) != set(covered)
        or any(type(count) is not int or count < 0 for count in source_point_counts.values())
        or sum(source_point_counts.values()) != chunk.point_count
    ):
        raise ExportArtifactError("source별 canonical point 수가 chunk와 다릅니다.")
    if attempt["owner_epochs"] is not None and source_point_counts is None:
        raise ExportArtifactError("계획된 source의 canonical point 증거가 없습니다.")
    expected = dict(
        artifact_digest=chunk.digest,
        byte_count=len(chunk.payload),
        point_count=chunk.point_count,
        covered_sources=covered,
        source_point_counts=dict(source_point_counts) if source_point_counts is not None else None,
        source_collected_at=chunk.source_collected_at,
    )
    existing = (
        session.execute(
            select(chunks).where(
                chunks.c.attempt_id == capture.attempt.attempt_id,
                chunks.c.chunk_key == capture.chunk_key,
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        if any(
            existing[name] != value
            for name, value in expected.items()
            if not (
                name == "source_point_counts"
                and existing[name] is None
                and attempt["owner_epochs"] is None
            )
        ):
            raise ExportArtifactError("export chunk 재시도의 내용이 다릅니다.")
        return
    if attempt["status"] != "preparing" or attempt["plan_closed_at"] is not None:
        raise ExportArtifactError("봉인된 export attempt에는 chunk를 추가할 수 없습니다.")
    session.execute(
        chunks.insert().values(
            attempt_id=capture.attempt.attempt_id,
            chunk_key=capture.chunk_key,
            recorded_at=datetime.now(UTC),
            **expected,
        )
    )
