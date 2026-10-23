"""성공 export의 예상 계획·완결·terminal 증거. 공개 head/collector 활성화는 후속이다."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from kortravelcommon.deadline import call_with_deadline
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .export_artifacts import (
    MAX_CHUNK_BYTES,
    MAX_CHUNK_POINTS,
    ExportArtifactError,
    ExportArtifactStore,
)
from .export_receipts import ExportAttempt, export_receipt_tables, source_scope_key

if TYPE_CHECKING:
    from .models import WeatherValue
    from .repository import WeatherRepository

MAX_EXPECTED_CHUNKS = 10000
MAX_PLAN_REFERENCES = 50000
MAX_CHUNK_SOURCES = 256
# timeout 뒤 아직 끝나지 않은 조회가 있어도 다음 tick에서 thread를 더 만들지 않는다.
_TERMINAL_PROBE_SLOTS = threading.BoundedSemaphore(1)


def normalization_input_digest(values: Sequence[WeatherValue]) -> str:
    # 입력의 typed normalization 증거다. canonical persisted artifact hash와는 별개다.
    points = []
    byte_count = 0
    for value in values:
        point = json.dumps(
            value.model_dump(mode="json", exclude={"payload", "source_record_key"}),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        byte_count += len(point.encode())
        if byte_count > MAX_CHUNK_BYTES or len(points) >= MAX_CHUNK_POINTS:
            raise ExportArtifactError("normalization 계획의 point/byte 상한을 넘었습니다.")
        points.append(point)
    return hashlib.sha256(json.dumps(sorted(points), separators=(",", ":")).encode()).hexdigest()


def export_lifecycle_tables(metadata: MetaData) -> tuple[Table, Table, Table, Table, Table]:
    names = (
        "weather_export_scope_owners",
        "weather_export_expected_chunks",
        "weather_export_source_closures",
        "weather_export_validated_chunks",
        "weather_export_terminal_evidence",
    )
    if names[0] in metadata.tables:
        return tuple(metadata.tables[name] for name in names)
    owners = Table(
        names[0],
        metadata,
        Column("source_scope", String(64), primary_key=True),
        Column("owner_epoch", BigInteger, nullable=False),
        Column("active_attempt_id", String(36), nullable=False),
        CheckConstraint("owner_epoch > 0", name="ck_weather_export_owner_epoch"),
    )
    expected = Table(
        names[1],
        metadata,
        Column(
            "attempt_id",
            String(36),
            ForeignKey("weather_export_attempts.attempt_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        Column("chunk_key", String(64), primary_key=True),
        Column("source_scopes", JSON, nullable=False),
        Column("input_point_count", Integer, nullable=False),
        Column("input_digest", String(64), nullable=False),
        CheckConstraint(
            "input_point_count BETWEEN 0 AND 5000", name="ck_weather_export_input_points"
        ),
    )
    closures = Table(
        names[2],
        metadata,
        Column(
            "attempt_id",
            String(36),
            ForeignKey("weather_export_attempts.attempt_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        Column("source_scope", String(64), primary_key=True),
        Column("chunk_keys", JSON, nullable=False),
    )
    validated = Table(
        names[3],
        metadata,
        Column("attempt_id", String(36), primary_key=True),
        Column("chunk_key", String(64), primary_key=True),
        Column("artifact_digest", String(64), nullable=False),
        Column("validated_at", DateTime(timezone=True), nullable=False),
        ForeignKeyConstraint(
            ["attempt_id", "chunk_key"],
            ["weather_export_chunks.attempt_id", "weather_export_chunks.chunk_key"],
            ondelete="CASCADE",
        ),
    )
    evidence = Table(
        names[4],
        metadata,
        Column(
            "attempt_id",
            String(36),
            ForeignKey("weather_export_attempts.attempt_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        Column("run_id", String(64), nullable=False),
        Column("execution_mode", String(16), nullable=False),
        Column("orchestrator_run_id", String(64)),
        Column("seal_digest", String(64), nullable=False),
        Column("checked_at", DateTime(timezone=True), nullable=False),
    )
    return owners, expected, closures, validated, evidence


def _current_owners(
    session: Session,
    metadata: MetaData,
    attempt: Mapping[str, Any],
    selected_sources: Sequence[str] | None = None,
) -> None:
    epochs = attempt["owner_epochs"]
    if not epochs or (selected_sources is None and set(epochs) != set(attempt["planned_sources"])):
        raise ExportArtifactError("export scope 소유권 계획이 없습니다.")
    keys = set(epochs) if selected_sources is None else set(selected_sources)
    if not keys or not keys.issubset(epochs):
        raise ExportArtifactError("선택한 export source 소유권이 없습니다.")
    owners, *_ = export_lifecycle_tables(metadata)
    rows = (
        session.execute(
            select(owners)
            .where(
                owners.c.source_scope.in_(sorted(keys)),
            )
            .order_by(owners.c.source_scope)
            .with_for_update()
        )
        .mappings()
        .all()
    )
    if len(rows) != len(keys) or any(
        row["active_attempt_id"] != attempt["attempt_id"]
        or row["owner_epoch"] != epochs[row["source_scope"]]
        for row in rows
    ):
        raise ExportArtifactError("export scope 소유권이 바뀌었습니다.")


def validate_planned_capture(
    session: Session,
    metadata: MetaData,
    attempt: Mapping[str, Any],
    chunk_key: str,
    covered: Sequence[str],
    *,
    input_point_count: int | None,
    input_digest: str | None,
) -> None:
    _current_owners(session, metadata, attempt, covered)
    _, expected, *_ = export_lifecycle_tables(metadata)
    plan = (
        session.execute(
            select(expected).where(
                expected.c.attempt_id == attempt["attempt_id"],
                expected.c.chunk_key == chunk_key,
            )
        )
        .mappings()
        .one_or_none()
    )
    if plan is None or (
        plan["source_scopes"] != list(covered)
        or plan["input_point_count"] != input_point_count
        or plan["input_digest"] != input_digest
    ):
        raise ExportArtifactError("export 입력이 receipt 이전에 확정한 chunk 계획과 다릅니다.")


class ExportLifecycle:
    """수집 job과 분리해서 명시 호출하는 내부 lifecycle. 어떤 API/head도 공개하지 않는다."""

    def __init__(self, repository: WeatherRepository):
        from .repository import Base

        self.repository = repository
        self.metadata = Base.metadata
        self.attempts, self.chunks = export_receipt_tables(self.metadata)
        self.owners, self.expected, self.closures, self.validated, self.evidence = (
            export_lifecycle_tables(self.metadata)
        )

    @contextmanager
    def _transaction(self):
        with self.repository._session_factory.begin() as session:
            self.repository._set_ingest_lock_timeout(session)
            yield session

    def _locked(self, session: Session, identity: ExportAttempt):
        # 모든 경로는 원본 ingest와 같은 run -> attempt -> 정렬 scope 잠금 순서다.
        run_id = session.scalar(
            select(self.attempts.c.run_id).where(
                self.attempts.c.attempt_id == identity.attempt_id,
            )
        )
        run_table = self.metadata.tables["weather_sync_runs"]
        run = (
            session.execute(select(run_table).where(run_table.c.run_id == run_id).with_for_update())
            .mappings()
            .one_or_none()
        )
        attempt = (
            session.execute(
                select(self.attempts)
                .where(
                    self.attempts.c.attempt_id == identity.attempt_id,
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if run is None or attempt is None or attempt["owner_id"] != identity.owner_id:
            raise ExportArtifactError("export 실행 소유권을 확인할 수 없습니다.")
        if attempt["orchestrator_run_id"] != run["orchestrator_run_id"]:
            raise ExportArtifactError("export 원본 실행 identity가 달라졌습니다.")
        return run, attempt

    def claim(self, identity: ExportAttempt) -> None:
        with self._transaction() as session:
            run, attempt = self._locked(session, identity)
            if attempt["owner_epochs"] is not None:
                _current_owners(session, self.metadata, attempt)
                return
            if (
                run["status"] != "running"
                or attempt["status"] != "preparing"
                or session.scalar(
                    select(self.chunks.c.chunk_key)
                    .where(self.chunks.c.attempt_id == identity.attempt_id)
                    .limit(1)
                )
                is not None
            ):
                raise ExportArtifactError("원천 receipt 저장 전에 scope를 예약해야 합니다.")
            statement = insert(self.owners).values(
                [
                    {
                        "source_scope": key,
                        "owner_epoch": 1,
                        "active_attempt_id": identity.attempt_id,
                    }
                    for key in sorted(attempt["planned_sources"])
                ]
            )
            statement = statement.on_conflict_do_update(
                index_elements=["source_scope"],
                set_={
                    "owner_epoch": self.owners.c.owner_epoch + 1,
                    "active_attempt_id": identity.attempt_id,
                },
            ).returning(self.owners.c.source_scope, self.owners.c.owner_epoch)
            epochs = dict(session.execute(statement).all())
            session.execute(
                update(self.attempts)
                .where(self.attempts.c.attempt_id == identity.attempt_id)
                .values(owner_epochs=epochs)
            )

    def expect_chunk(
        self,
        identity: ExportAttempt,
        chunk_key: str,
        *,
        records: Sequence[Mapping[str, Any]],
        values: Sequence[WeatherValue],
    ) -> None:
        if (
            not chunk_key
            or len(chunk_key) > 64
            or not chunk_key.isascii()
            or any(not (c.isalnum() or c in "-_") for c in chunk_key)
            or not records
            or len(values) > MAX_CHUNK_POINTS
        ):
            raise ExportArtifactError("예상 chunk 범위가 잘못됐습니다.")
        scopes = sorted(
            {
                source_scope_key(
                    str(r["provider"]),
                    str(r["dataset_key"]),
                    str(r["source_entity_type"]),
                    str(r["source_entity_id"]),
                )
                for r in records
            }
        )
        if len(scopes) > MAX_CHUNK_SOURCES:
            raise ExportArtifactError("chunk 원천 scope 상한을 넘었습니다.")
        plan = {
            "source_scopes": scopes,
            "input_point_count": len(values),
            "input_digest": normalization_input_digest(values),
        }
        with self._transaction() as session:
            run, attempt = self._locked(session, identity)
            _current_owners(session, self.metadata, attempt, scopes)
            if (
                run["status"] != "running"
                or attempt["status"] != "preparing"
                or attempt["plan_closed_at"] is not None
            ):
                raise ExportArtifactError("예상 계획이 닫혔습니다.")
            if not set(scopes).issubset(attempt["planned_sources"]) or any(
                r.get("run_id") != attempt["run_id"] for r in records
            ):
                raise ExportArtifactError("예상 원천이 선택한 실행·scope와 다릅니다.")
            existing = (
                session.execute(
                    select(self.expected).where(
                        self.expected.c.attempt_id == identity.attempt_id,
                        self.expected.c.chunk_key == chunk_key,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if any(existing[key] != value for key, value in plan.items()):
                    raise ExportArtifactError("예상 chunk 계획을 바꿀 수 없습니다.")
                return
            count = session.scalar(
                select(func.count())
                .select_from(self.expected)
                .where(
                    self.expected.c.attempt_id == identity.attempt_id,
                )
            )
            if (
                count >= MAX_EXPECTED_CHUNKS
                or attempt["plan_reference_count"] + len(scopes) > MAX_PLAN_REFERENCES
            ):
                raise ExportArtifactError("예상 manifest 상한을 넘었습니다.")
            if (
                session.scalar(
                    select(self.closures.c.source_scope)
                    .where(
                        self.closures.c.attempt_id == identity.attempt_id,
                        self.closures.c.source_scope.in_(scopes),
                    )
                    .limit(1)
                )
                is not None
            ):
                raise ExportArtifactError("완결 선언 뒤에는 source 계획을 늘릴 수 없습니다.")
            session.execute(
                self.expected.insert().values(
                    attempt_id=identity.attempt_id, chunk_key=chunk_key, **plan
                )
            )
            session.execute(
                update(self.attempts)
                .where(self.attempts.c.attempt_id == identity.attempt_id)
                .values(
                    plan_reference_count=attempt["plan_reference_count"] + len(scopes),
                )
            )

    def close_source(
        self, identity: ExportAttempt, source_scope: str, chunk_keys: Sequence[str]
    ) -> None:
        keys = sorted(set(chunk_keys))
        if not keys or len(keys) != len(chunk_keys) or len(keys) > MAX_EXPECTED_CHUNKS:
            raise ExportArtifactError("완결 source의 예상 chunk가 잘못됐습니다.")
        with self._transaction() as session:
            run, attempt = self._locked(session, identity)
            _current_owners(session, self.metadata, attempt, [source_scope])
            if (
                run["status"] != "running"
                or attempt["status"] != "preparing"
                or attempt["plan_closed_at"] is not None
                or source_scope not in attempt["planned_sources"]
            ):
                raise ExportArtifactError("source 계획을 닫을 수 없습니다.")
            plans = (
                session.execute(
                    select(self.expected).where(
                        self.expected.c.attempt_id == identity.attempt_id,
                        self.expected.c.chunk_key.in_(keys),
                    )
                )
                .mappings()
                .all()
            )
            if len(plans) != len(keys) or any(
                source_scope not in row["source_scopes"] for row in plans
            ):
                raise ExportArtifactError("선행 예상 chunk 계획이 없습니다.")
            existing = (
                session.execute(
                    select(self.closures).where(
                        self.closures.c.attempt_id == identity.attempt_id,
                        self.closures.c.source_scope == source_scope,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["chunk_keys"] != keys:
                    raise ExportArtifactError("source 완결 선언을 바꿀 수 없습니다.")
                return
            session.execute(
                self.closures.insert().values(
                    attempt_id=identity.attempt_id, source_scope=source_scope, chunk_keys=keys
                )
            )

    def close_plan(self, identity: ExportAttempt) -> str:
        with self._transaction() as session:
            run, attempt = self._locked(session, identity)
            _current_owners(session, self.metadata, attempt)
            if attempt["plan_closed_at"] is not None:
                return attempt["seal_digest"]
            if run["status"] != "running" or attempt["status"] != "preparing":
                raise ExportArtifactError("진행 중인 원천의 완결 계획만 닫을 수 있습니다.")
            plans = (
                session.execute(
                    select(self.expected)
                    .where(self.expected.c.attempt_id == identity.attempt_id)
                    .order_by(self.expected.c.chunk_key)
                )
                .mappings()
                .all()
            )
            chunks = (
                session.execute(
                    select(self.chunks)
                    .where(self.chunks.c.attempt_id == identity.attempt_id)
                    .order_by(self.chunks.c.chunk_key)
                )
                .mappings()
                .all()
            )
            closures = (
                session.execute(
                    select(self.closures).where(self.closures.c.attempt_id == identity.attempt_id)
                )
                .mappings()
                .all()
            )
            by_source = {key: [] for key in attempt["planned_sources"]}
            for row in plans:
                for source in row["source_scopes"]:
                    by_source[source].append(row["chunk_key"])
            admitted = dict.fromkeys(attempt["planned_sources"], 0)
            for row in chunks:
                for source, count in (row["source_point_counts"] or {}).items():
                    if source not in admitted:
                        raise ExportArtifactError("canonical point source가 계획을 벗어났습니다.")
                    admitted[source] += count
            if (
                not plans
                or {row["source_scope"]: row["chunk_keys"] for row in closures} != by_source
                or [row["chunk_key"] for row in plans] != [row["chunk_key"] for row in chunks]
                or any(count == 0 for count in admitted.values())
            ):
                raise ExportArtifactError(
                    "예상 source·segment의 완결 barrier가 충족되지 않았습니다."
                )
            parts = [
                [
                    row["chunk_key"],
                    row["artifact_digest"],
                    row["byte_count"],
                    row["point_count"],
                    row["source_collected_at"].isoformat(),
                    row["covered_sources"],
                    row["source_point_counts"],
                ]
                for row in chunks
            ]
            digest = hashlib.sha256(
                json.dumps(
                    [
                        attempt["plan_digest"],
                        attempt["owner_epochs"],
                        [
                            [
                                row["chunk_key"],
                                row["source_scopes"],
                                row["input_point_count"],
                                row["input_digest"],
                            ]
                            for row in plans
                        ],
                        parts,
                    ],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            session.execute(
                update(self.attempts)
                .where(self.attempts.c.attempt_id == identity.attempt_id)
                .values(plan_closed_at=datetime.now(UTC), seal_digest=digest)
            )
            return digest

    def reconcile(
        self,
        identity: ExportAttempt,
        artifacts: ExportArtifactStore,
        *,
        dagster_status: Callable[[str], str | None] | None = None,
        max_chunks: int = 2,
    ) -> Literal["NOT_READY", "ELIGIBLE", "REJECTED"]:
        if not 1 <= max_chunks <= 8:
            raise ExportArtifactError("검증 tick chunk 상한은1~8입니다.")
        with self._transaction() as session:
            run, attempt = self._locked(session, identity)
            _current_owners(session, self.metadata, attempt)
            if attempt["status"] == "failed" or run["status"] in {
                "failed",
                "partial",
                "partial_success",
                "canceled",
                "cancelled",
                "skipped",
            }:
                return "REJECTED"
            if run["status"] != "success" or attempt["plan_closed_at"] is None:
                return "NOT_READY"
            if attempt["status"] == "sealed":
                return "ELIGIBLE"
            receipts = [
                dict(row)
                for row in session.execute(
                    select(self.chunks)
                    .outerjoin(
                        self.validated,
                        (self.chunks.c.attempt_id == self.validated.c.attempt_id)
                        & (self.chunks.c.chunk_key == self.validated.c.chunk_key),
                    )
                    .where(
                        self.chunks.c.attempt_id == identity.attempt_id,
                        self.validated.c.chunk_key.is_(None),
                    )
                    .order_by(self.chunks.c.chunk_key)
                    .limit(max_chunks)
                ).mappings()
            ]
            token = attempt["seal_digest"]
            mode, orchestrator_id = attempt["execution_mode"], attempt["orchestrator_run_id"]
        # file/Dagster I/O 중 원본 DB transaction과 pool connection을 보유하지 않는다.
        if mode == "dagster":
            if not orchestrator_id or dagster_status is None:
                return "NOT_READY"
            try:
                status = call_with_deadline(
                    lambda: dagster_status(orchestrator_id),
                    timeout_seconds=2,
                    slots=_TERMINAL_PROBE_SLOTS,
                )
            except Exception:
                return "NOT_READY"
            if status in {"FAILURE", "CANCELED"}:
                return "REJECTED"
            if status != "SUCCESS":
                return "NOT_READY"
        bounded = []
        byte_count = 0
        for receipt in receipts:
            if byte_count + receipt["byte_count"] > 2 * MAX_CHUNK_BYTES:
                break
            bounded.append(receipt)
            byte_count += receipt["byte_count"]
        receipts = bounded
        for receipt in receipts:
            try:
                for _ in artifacts.read(
                    receipt["artifact_digest"],
                    byte_count=receipt["byte_count"],
                    point_count=receipt["point_count"],
                ):
                    pass
            except (OSError, ExportArtifactError, ValueError):
                return "NOT_READY"
        with self._transaction() as session:
            run, attempt = self._locked(session, identity)
            _current_owners(session, self.metadata, attempt)
            if (
                run["status"] != "success"
                or attempt["status"] == "failed"
                or attempt["seal_digest"] != token
            ):
                return "REJECTED"
            for receipt in receipts:
                session.execute(
                    insert(self.validated)
                    .values(
                        attempt_id=identity.attempt_id,
                        chunk_key=receipt["chunk_key"],
                        artifact_digest=receipt["artifact_digest"],
                        validated_at=datetime.now(UTC),
                    )
                    .on_conflict_do_nothing()
                )
            missing = session.scalar(
                select(self.chunks.c.chunk_key)
                .outerjoin(
                    self.validated,
                    (self.chunks.c.attempt_id == self.validated.c.attempt_id)
                    & (self.chunks.c.chunk_key == self.validated.c.chunk_key),
                )
                .where(
                    self.chunks.c.attempt_id == identity.attempt_id,
                    self.validated.c.chunk_key.is_(None),
                )
                .limit(1)
            )
            if missing is not None:
                return "NOT_READY"
            session.execute(
                insert(self.evidence)
                .values(
                    attempt_id=identity.attempt_id,
                    run_id=run["run_id"],
                    execution_mode=mode,
                    orchestrator_run_id=orchestrator_id,
                    seal_digest=token,
                    checked_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing()
            )
            session.execute(
                update(self.attempts)
                .where(
                    self.attempts.c.attempt_id == identity.attempt_id,
                    self.attempts.c.status == "preparing",
                )
                .values(status="sealed", sealed_at=datetime.now(UTC))
            )
            return "ELIGIBLE"
