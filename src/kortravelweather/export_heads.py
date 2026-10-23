"""성공한 단일 원천의 불변 artifact를 독립 head로 승격한다.

원천 current를 다시 읽지 않는다. 기존 bytes를 참조하며 공개 API/GC 활성화는 별도다.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert

from .export_artifacts import (
    MAX_CHUNK_POINTS,
    ExportArtifactError,
    ExportArtifactStore,
    canonical_export_number,
)
from .export_lifecycle import ExportLifecycle, _current_owners
from .export_receipts import ExportAttempt, source_scope_key

NOWCAST_REQUIRED_CATEGORIES = frozenset({"T1H", "RN1", "UUU", "VVV", "REH", "PTY", "VEC", "WSD"})

MAX_PROMOTION_CHUNKS = 64
MAX_PROMOTION_BYTES = 128 * 1024 * 1024
MAX_FORECAST_POINTS = 100_000


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode()
    ).hexdigest()


def semantic_point_digest(point: Mapping[str, Any]) -> str:
    result = {
        k: v
        for k, v in point.items()
        if k
        not in {
            "value_id",
            "source_record_key",
            "known_at",
            "collected_at",
            "payload",
            "observation_evidence",
        }
    }
    if result.get("value_number") is not None:
        result["value_number"] = canonical_export_number(Decimal(str(result["value_number"])))
    for key in ("issued_at", "valid_at", "valid_from", "valid_until", "observed_at", "target_at"):
        if result.get(key) is not None:
            value = result[key]
            at = datetime.fromisoformat(value) if isinstance(value, str) else value
            if at.tzinfo is None or at.utcoffset() is None:
                raise ExportArtifactError("semantic point 시각에는 timezone이 필요합니다.")
            result[key] = at.astimezone(UTC).isoformat()
    return _digest(result)


class ScopeDescriptor(BaseModel):
    """수집 전에 고정하는 해석 정책. 정책 변경은 별도 head namespace다."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: Literal["python-kma-api"] = "python-kma-api"
    dataset_key: Literal[
        "kma_ultra_short_nowcast", "kma_ultra_short_forecast", "kma_short_forecast"
    ] = "kma_ultra_short_nowcast"
    entity_type: str = Field(min_length=1, max_length=120)
    entity_id: str = Field(min_length=1, max_length=240)
    forecast_style: Literal["nowcast", "ultra_short", "short"] = "nowcast"
    nx: int = Field(ge=1, le=300)
    ny: int = Field(ge=1, le=300)
    location_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)
    normalizer_revision: str = Field(min_length=1, max_length=64)
    winner_policy_revision: str = Field(min_length=1, max_length=64)
    coverage_contract: Literal[
        "kma-grid-nonempty-v1", "kma-grid-nowcast-v1", "kma-grid-forecast-v1"
    ] = "kma-grid-nonempty-v1"

    @model_validator(mode="after")
    def _canonical_selection(self):
        styles = {
            "kma_ultra_short_nowcast": "nowcast",
            "kma_ultra_short_forecast": "ultra_short",
            "kma_short_forecast": "short",
        }
        if styles[self.dataset_key] != self.forecast_style:
            raise ValueError("KMA dataset과 forecast style이 다릅니다.")
        if (self.coverage_contract == "kma-grid-forecast-v1") != (self.forecast_style != "nowcast"):
            raise ValueError("예보는 전용 coverage 계약이 필요합니다.")
        if self.forecast_style != "nowcast" and len(self.location_ids) > 50:
            raise ValueError("예보 공개 원천은 한 번에 50개 위치까지 허용합니다.")
        if tuple(sorted(set(self.location_ids))) != self.location_ids:
            raise ValueError("선택 location은 중복 없는 정렬 목록이어야 합니다.")
        if any(not key or len(key) > 120 for key in self.location_ids):
            raise ValueError("선택 location 식별자가 올바르지 않습니다.")
        return self

    @property
    def source_scope(self) -> str:
        return source_scope_key(self.provider, self.dataset_key, self.entity_type, self.entity_id)

    @property
    def namespace(self) -> str:
        return _digest(self.model_dump(mode="json"))


def export_head_tables(metadata: MetaData) -> tuple[Table, Table, Table, Table, Table]:
    names = (
        "weather_export_scope_plans",
        "weather_export_generations",
        "weather_export_heads",
        "weather_export_generation_chunks",
        "weather_export_pending_refs",
    )
    if names[0] in metadata.tables:
        return tuple(metadata.tables[name] for name in names)
    plans = Table(
        names[0],
        metadata,
        Column("attempt_id", String(36), primary_key=True),
        Column("source_scope", String(64), nullable=False),
        Column("namespace", String(64), nullable=False),
        Column("descriptor", JSON, nullable=False),
        Column("bound_at", DateTime(timezone=True), nullable=False),
        Column("data_revision_at", DateTime(timezone=True)),
        Column("source_input_digest", String(64)),
        Column("source_collected_at", DateTime(timezone=True)),
        Column("expected_point_count", Integer),
        Column("normalization_digest", String(64)),
        Column("normalization_evidence", JSON),
    )
    generations = Table(
        names[1],
        metadata,
        Column("generation_id", String(36), primary_key=True),
        # run/attempt 이력 정리가 공개 성공 증거를 cascade 삭제하면 안 된다.
        Column("attempt_id", String(36), nullable=False, unique=True),
        Column("owner_id", String(36), nullable=False),
        Column("seal_digest", String(64), nullable=False),
        Column("namespace", String(64), nullable=False, index=True),
        Column("parent_generation_id", String(36)),
        Column("data_revision_at", DateTime(timezone=True), nullable=False),
        Column("source_collected_at", DateTime(timezone=True), nullable=False),
        Column("content_digest", String(64), nullable=False),
        Column("point_count", Integer, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        CheckConstraint("point_count > 0", name="ck_weather_generation_nonempty"),
    )
    heads = Table(
        names[2],
        metadata,
        Column("namespace", String(64), primary_key=True),
        Column("source_scope", String(64), nullable=False, index=True),
        Column(
            "generation_id", String(36), ForeignKey(names[1] + ".generation_id"), nullable=False
        ),
        Column("version", BigInteger, nullable=False),
        Column("data_revision_at", DateTime(timezone=True), nullable=False),
        Column("content_digest", String(64), nullable=False),
        Column("source_collected_at", DateTime(timezone=True), nullable=False),
        CheckConstraint("version > 0", name="ck_weather_head_version"),
    )
    chunks = Table(
        names[3],
        metadata,
        Column(
            "generation_id", String(36), ForeignKey(names[1] + ".generation_id"), primary_key=True
        ),
        Column("ordinal", Integer, primary_key=True),
        Column("artifact_digest", String(64), nullable=False, index=True),
        Column("byte_count", Integer, nullable=False),
        Column("point_count", Integer, nullable=False),
        UniqueConstraint("generation_id", "artifact_digest", name="uq_weather_generation_artifact"),
    )
    pins = Table(
        names[4],
        metadata,
        Column("attempt_id", String(36), primary_key=True),
        Column("chunk_key", String(64), primary_key=True),
        Column("artifact_digest", String(64), nullable=False, index=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    return plans, generations, heads, chunks, pins


def source_input_digest(record: Mapping[str, Any]) -> str:
    return _digest(
        [
            record[k]
            for k in (
                "provider",
                "dataset_key",
                "source_entity_type",
                "source_entity_id",
                "payload",
            )
        ]
    )


def _normalize(descriptor, record, locations, *, skipped=None):
    """기존 normalizer만 사용하며 한 위치의 값만 메모리에 유지한다."""
    from .providers.kma import (
        short_forecast_to_weather_values,
        ultra_short_forecast_to_weather_values,
        ultra_short_nowcast_to_weather_values,
    )

    normalizer = {
        "nowcast": ultra_short_nowcast_to_weather_values,
        "ultra_short": ultra_short_forecast_to_weather_values,
        "short": short_forecast_to_weather_values,
    }[descriptor.forecast_style]
    for location in locations:
        yield from normalizer(
            record["payload"]["rows"],
            location_id=location,
            source_record_key=record["source_record_key"],
            known_at=record["fetched_at"],
            skipped=skipped,
        )


def _forecast_coverage(descriptor, rows, revision):
    """public pagination에 더해 최소 예보 시간대/필수 category를 확인한다.

    발표 시각 선택과 grid 계산은 SDK에 맡긴다. 확장 기간의 3시간 간격과
    정성형 강수/적설을 1시간 숫자로 재해석하지 않는다.
    """
    from kma.time_utils import parse_kma_datetime

    groups = {}
    for row in rows:
        at = parse_kma_datetime(str(row["fcstDate"]), str(row["fcstTime"])).astimezone(UTC)
        if at <= revision:
            raise ExportArtifactError("예보 대상 시각이 발표 이후가 아닙니다.")
        groups.setdefault(at, set()).add(str(row["category"]))
    required = (
        {"T1H", "RN1", "REH", "PTY", "SKY", "VEC", "WSD"}
        if descriptor.forecast_style == "ultra_short"
        else {"TMP", "PCP", "REH", "PTY", "POP", "SKY", "VEC", "WSD"}
    )
    minimum = 6 if descriptor.forecast_style == "ultra_short" else 72
    complete = sorted(at for at, categories in groups.items() if required.issubset(categories))
    # metadata의 SDK 선택 base를 그대로 사용한다. 이미 선택된 회차의 첫 예보
    # 정시를 묶는 검사이며 발표 회차 선택/지연시간 계산을 재구현하지 않는다.
    first_target = revision.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    if (
        not complete
        or complete[0] != first_target
        or len(complete) < minimum
        or any(
            (right - left).total_seconds() != 3600
            for left, right in zip(complete[:minimum], complete[1:minimum], strict=False)
        )
    ):
        raise ExportArtifactError("예보의 최소 연속 시간대·필수 category 증거가 없습니다.")


def validate_head_capture(session, metadata, attempt, records, values, observed) -> None:
    """3B를 선택한 capture만 추가 검증한다. 기존 receipt 계약은 보존한다."""
    plans, *_ = export_head_tables(metadata)
    plan = (
        session.execute(
            select(plans).where(
                plans.c.attempt_id == attempt.attempt_id,
            )
        )
        .mappings()
        .one_or_none()
    )
    if plan is None:
        return
    descriptor = ScopeDescriptor.model_validate(plan["descriptor"])
    if (
        plan["data_revision_at"] is None
        or len(records) != 1
        or source_input_digest(records[0]) != plan["source_input_digest"]
        or plan["source_collected_at"] is None
        or records[0].get("fetched_at") != plan["source_collected_at"]
        or observed != {str(records[0]["source_record_key"]): plan["source_collected_at"]}
        or any(
            v.provider != descriptor.provider
            or v.dataset_key != descriptor.dataset_key
            or v.forecast_style.value != descriptor.forecast_style
            or v.location_id not in descriptor.location_ids
            or v.weather_domain != descriptor.dataset_key
            or v.normalization_version != descriptor.normalizer_revision
            or (descriptor.forecast_style == "nowcast" and v.target_at != plan["data_revision_at"])
            for v in values
        )
    ):
        raise ExportArtifactError("승격 계획의 원천·정책과 실제 capture가 다릅니다.")
    if descriptor.forecast_style != "nowcast":
        # 위치별 bounded capture를 허용한다. 빠진 위치/metric, 중복 chunk는 승격 시
        # 전체 정규화 digest와 대조하므로 부분 성공 head가 될 수 없다.
        wanted = {semantic_point_digest(v.model_dump(mode="json")) for v in values}
        expected = {
            semantic_point_digest(v.model_dump(mode="json"))
            for v in _normalize(descriptor, records[0], sorted({v.location_id for v in values}))
        }
        if not wanted or len(wanted) != len(values) or not wanted.issubset(expected):
            raise ExportArtifactError("예보 capture가 원천 normalizer 결과와 다릅니다.")
        return
    if (
        len(values) != plan["expected_point_count"]
        or _digest(sorted(semantic_point_digest(v.model_dump(mode="json")) for v in values))
        != plan["normalization_digest"]
    ):
        raise ExportArtifactError("공개 normalizer가 검증한 전체 point와 capture가 다릅니다.")


def seal_head_plan(session, metadata, attempt_id):
    plans, *_ = export_head_tables(metadata)
    row = (
        session.execute(select(plans).where(plans.c.attempt_id == attempt_id))
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    if (
        row["data_revision_at"] is None
        or row["source_input_digest"] is None
        or row["source_collected_at"] is None
    ):
        raise ExportArtifactError("원천 publication 계획이 완결되지 않았습니다.")
    return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in row.items()}


class ExportHeads:
    def __init__(self, repository):
        self.lifecycle = ExportLifecycle(repository)
        self.repository = repository
        self.metadata = self.lifecycle.metadata
        self.plans, self.generations, self.heads, self.refs, self.pins = export_head_tables(
            self.metadata
        )

    @contextmanager
    def _transaction(self):
        with self.lifecycle._transaction() as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            yield session

    def declare(self, identity: ExportAttempt, descriptor: ScopeDescriptor) -> None:
        """실제 요청 전에 선언한다. 응답을 본 뒤 새 ownership을 붙이지 않는다."""
        with self._transaction() as session:
            run, attempt = self.lifecycle._locked(session, identity)
            _current_owners(session, self.metadata, attempt)
            if (
                run["status"] != "running"
                or attempt["status"] != "preparing"
                or (attempt["planned_sources"] != [descriptor.source_scope])
            ):
                raise ExportArtifactError("단일 source의 진행 중 계획만 선언할 수 있습니다.")
            existing = (
                session.execute(
                    select(self.plans).where(
                        self.plans.c.attempt_id == identity.attempt_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["descriptor"] != descriptor.model_dump(mode="json"):
                    raise ExportArtifactError("원천 해석 정책은 변경할 수 없습니다.")
                return
            if (
                session.scalar(
                    select(self.lifecycle.expected.c.chunk_key)
                    .where(
                        self.lifecycle.expected.c.attempt_id == identity.attempt_id,
                    )
                    .limit(1)
                )
                is not None
            ):
                raise ExportArtifactError("응답 계획 전에 descriptor를 선언해야 합니다.")
            catalog = self.metadata.tables["weather_locations"]
            targets = (
                session.execute(
                    select(catalog.c.location_id).where(
                        catalog.c.location_id.in_(descriptor.location_ids),
                        catalog.c.enabled.is_(True),
                        catalog.c.nx == descriptor.nx,
                        catalog.c.ny == descriptor.ny,
                    )
                )
                .scalars()
                .all()
            )
            if set(targets) != set(descriptor.location_ids):
                raise ExportArtifactError("선택 위치의 catalog/grid가 선언과 다릅니다.")
            session.execute(
                self.plans.insert().values(
                    attempt_id=identity.attempt_id,
                    source_scope=descriptor.source_scope,
                    namespace=descriptor.namespace,
                    descriptor=descriptor.model_dump(mode="json"),
                    bound_at=datetime.now(UTC),
                )
            )

    def bind_kma_response(self, identity: ExportAttempt, record: Mapping[str, Any]) -> None:
        """public KMA metadata와 실제 rows의 동일 grid/base·완결을 확인한다."""
        from kma import PaginationReceipt
        from kma.time_utils import parse_kma_datetime

        payload = record["payload"]
        rows, meta = payload["rows"], payload["response_metadata"]
        receipt = PaginationReceipt.model_validate(meta["pagination"], strict=True)
        if not receipt.complete or receipt.received_item_count != len(rows) or not rows:
            raise ExportArtifactError("KMA source 전체 페이지의 nonempty 증거가 필요합니다.")
        revision = parse_kma_datetime(meta["base_date"], meta["base_time"]).astimezone(UTC)
        if any(
            str(r.get("baseDate")) != meta["base_date"]
            or str(r.get("baseTime")) != meta["base_time"]
            for r in rows
        ):
            raise ExportArtifactError("KMA 원천의 발표 base가 metadata와 다릅니다.")
        request = meta["request_params"]
        if any(
            str(r.get("nx")) != str(request["nx"]) or str(r.get("ny")) != str(request["ny"])
            for r in rows
        ):
            raise ExportArtifactError("KMA 원천 grid가 요청과 다릅니다.")
        semantic = [(r.get("category"), r.get("fcstDate"), r.get("fcstTime")) for r in rows]
        if len(set(semantic)) != len(semantic):
            raise ExportArtifactError("KMA 원천 semantic identity가 중복됐습니다.")
        with self._transaction() as session:
            run, attempt = self.lifecycle._locked(session, identity)
            _current_owners(session, self.metadata, attempt)
            plan = (
                session.execute(
                    select(self.plans).where(
                        self.plans.c.attempt_id == identity.attempt_id,
                    )
                )
                .mappings()
                .one()
            )
            descriptor = ScopeDescriptor.model_validate(plan["descriptor"])
            fetched = record["fetched_at"]
            if (
                run["status"] != "running"
                or attempt["status"] != "preparing"
                or (
                    fetched.tzinfo is None
                    or fetched < plan["bound_at"]
                    or fetched > datetime.now(UTC)
                    or str(request["nx"]) != str(descriptor.nx)
                    or str(request["ny"]) != str(descriptor.ny)
                    or record["dataset_key"] != descriptor.dataset_key
                    or descriptor.source_scope
                    != source_scope_key(
                        record["provider"],
                        record["dataset_key"],
                        record["source_entity_type"],
                        record["source_entity_id"],
                    )
                )
            ):
                raise ExportArtifactError("선언 이전의 응답이나 다른 scope를 연결할 수 없습니다.")
            if (
                descriptor.coverage_contract == "kma-grid-nowcast-v1"
                and not NOWCAST_REQUIRED_CATEGORIES.issubset({str(r.get("category")) for r in rows})
            ):
                raise ExportArtifactError("공개 실황 source의 필수 category 전체 증거가 없습니다.")
            if descriptor.forecast_style != "nowcast":
                _forecast_coverage(descriptor, rows, revision)
            limit = (
                MAX_CHUNK_POINTS if descriptor.forecast_style == "nowcast" else MAX_FORECAST_POINTS
            )
            if len(rows) * len(descriptor.location_ids) > limit:
                raise ExportArtifactError("단일 원천의 정규화 point 상한을 넘었습니다.")
            # 원천 normalizer의 명시 결측/이상값 분류도 함께 보존한다.
            expected, skipped = [], Counter()
            expected.extend(
                semantic_point_digest(v.model_dump(mode="json"))
                for v in _normalize(descriptor, record, descriptor.location_ids, skipped=skipped)
            )
            if not expected:
                raise ExportArtifactError("nonempty 정상 point 증거가 없습니다.")
            previous_count = session.scalar(
                select(self.generations.c.point_count)
                .join(self.heads, self.heads.c.generation_id == self.generations.c.generation_id)
                .where(self.heads.c.namespace == plan["namespace"])
            )
            if previous_count and len(expected) * 2 < previous_count:
                raise ExportArtifactError(
                    "이전 성공 대비 절반 미만으로 축소된 원천은 재확인이 필요합니다."
                )
            normalization_digest = _digest(sorted(expected))
            digest = source_input_digest(record)
            if revision > fetched.astimezone(UTC):
                raise ExportArtifactError("발표 revision이 실제 조회 이후 시각입니다.")
            if plan["source_input_digest"] is not None:
                if (
                    plan["source_input_digest"] != digest
                    or plan["data_revision_at"] != revision
                    or plan["source_collected_at"] != fetched
                ):
                    raise ExportArtifactError("완결 응답 계획은 변경할 수 없습니다.")
                return
            if (
                session.scalar(
                    select(self.lifecycle.expected.c.chunk_key)
                    .where(
                        self.lifecycle.expected.c.attempt_id == identity.attempt_id,
                    )
                    .limit(1)
                )
                is not None
            ):
                raise ExportArtifactError("예상 chunk 전에 원천 완결 증거를 고정해야 합니다.")
            session.execute(
                update(self.plans)
                .where(
                    self.plans.c.attempt_id == identity.attempt_id,
                )
                .values(
                    data_revision_at=revision,
                    source_input_digest=digest,
                    source_collected_at=fetched,
                    expected_point_count=len(expected),
                    normalization_digest=normalization_digest,
                    normalization_evidence={str(key): count for key, count in skipped.items()},
                )
            )

    def promote(self, identity, artifacts, *, dagster_status=None) -> str:
        with artifacts.guard(timeout_seconds=5):
            return self._promote(identity, artifacts, dagster_status=dagster_status)

    def _promote(
        self,
        identity: ExportAttempt,
        artifacts: ExportArtifactStore,
        *,
        dagster_status: Callable[[str], str | None] | None = None,
    ) -> str:
        """파일 검증은 DB 반환 후, 최종 run/owner/head CAS는 한 transaction이다."""
        with self._transaction() as session:
            committed = (
                session.execute(
                    select(self.generations).where(
                        self.generations.c.attempt_id == identity.attempt_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if committed is not None:
                if committed["owner_id"] != identity.owner_id:
                    raise ExportArtifactError("이미 확정된 generation의 소유권이 다릅니다.")
                return "ALREADY_COMMITTED"
        if (
            self.lifecycle.reconcile(
                identity, artifacts, dagster_status=dagster_status, max_chunks=8
            )
            != "ELIGIBLE"
        ):
            return "NOT_READY"
        with self._transaction() as session:
            run, attempt = self.lifecycle._locked(session, identity)
            _current_owners(session, self.metadata, attempt)
            plan = (
                session.execute(
                    select(self.plans).where(
                        self.plans.c.attempt_id == identity.attempt_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                plan is None
                or plan["data_revision_at"] is None
                or plan["source_collected_at"] is None
            ):
                raise ExportArtifactError("승격할 원천 revision 계획이 없습니다.")
            receipts = [
                dict(r)
                for r in session.execute(
                    select(self.lifecycle.chunks)
                    .where(
                        self.lifecycle.chunks.c.attempt_id == identity.attempt_id,
                    )
                    .order_by(self.lifecycle.chunks.c.chunk_key)
                    .limit(MAX_PROMOTION_CHUNKS + 1)
                ).mappings()
            ]
            if (
                len(receipts) > MAX_PROMOTION_CHUNKS
                or sum(r["byte_count"] for r in receipts) > MAX_PROMOTION_BYTES
            ):
                raise ExportArtifactError("성공 head 승격의 chunk/byte 상한을 넘었습니다.")
            if not receipts or any(
                r["covered_sources"] != [plan["source_scope"]]
                or r["source_collected_at"] != plan["source_collected_at"]
                for r in receipts
            ):
                raise ExportArtifactError("혼합 source chunk를 승격할 수 없습니다.")
            parent = (
                session.execute(
                    select(self.heads).where(
                        self.heads.c.namespace == plan["namespace"],
                    )
                )
                .mappings()
                .one_or_none()
            )
            token = attempt["seal_digest"]
            parent_refs = (
                [
                    dict(r)
                    for r in session.execute(
                        select(self.refs)
                        .where(
                            self.refs.c.generation_id == parent["generation_id"],
                        )
                        .order_by(self.refs.c.ordinal)
                    ).mappings()
                ]
                if parent
                else []
            )
            for kind, rows in [("candidate", receipts), ("parent", parent_refs)]:
                for i, r in enumerate(rows):
                    session.execute(
                        insert(self.pins)
                        .values(
                            attempt_id=identity.attempt_id,
                            chunk_key=_digest([kind, i]),
                            artifact_digest=r["artifact_digest"],
                            created_at=datetime.now(UTC),
                        )
                        .on_conflict_do_nothing()
                    )
        semantic, seen, count, locations = [], set(), 0, set()
        descriptor = ScopeDescriptor.model_validate(plan["descriptor"])
        for r in receipts:
            for point in artifacts.read(
                r["artifact_digest"], byte_count=r["byte_count"], point_count=r["point_count"]
            ):
                if (
                    point.get("provider") != descriptor.provider
                    or point.get("dataset_key") != descriptor.dataset_key
                    or point.get("forecast_style") != descriptor.forecast_style
                ):
                    raise ExportArtifactError("artifact의 해석 정책이 선언과 다릅니다.")
                logical = [
                    point.get(k)
                    for k in (
                        "location_id",
                        "provider",
                        "dataset_key",
                        "weather_domain",
                        "forecast_style",
                        "metric_key",
                        "target_at",
                    )
                ]
                key = _digest(logical)
                if key in seen:
                    raise ExportArtifactError("성공 snapshot의 logical point가 중복됐습니다.")
                seen.add(key)
                semantic.append(semantic_point_digest(point))
                locations.add(point["location_id"])
                count += 1
        if (
            count != plan["expected_point_count"]
            or not count
            or locations != set(descriptor.location_ids)
        ):
            raise ExportArtifactError("선택한 위치의 nonempty 전체 집합이 아닙니다.")
        content = _digest(sorted(semantic))
        if content != plan["normalization_digest"]:
            raise ExportArtifactError("모든 chunk의 합집합이 검증한 전체 원천과 다릅니다.")
        # 내용이 같은 직전 성공 파일만 재사용한다. 기존 known_at/PK를 변조하지 않는다.
        reuse = parent is not None and parent["content_digest"] == content
        if reuse:
            if (
                len(parent_refs) > MAX_PROMOTION_CHUNKS
                or sum(r["byte_count"] for r in parent_refs) > MAX_PROMOTION_BYTES
            ):
                raise ExportArtifactError("직전 성공 manifest 상한이 다릅니다.")
            for r in parent_refs:
                for _ in artifacts.read(
                    r["artifact_digest"], byte_count=r["byte_count"], point_count=r["point_count"]
                ):
                    pass
        with self._transaction() as session:
            committed = (
                session.execute(
                    select(self.generations).where(
                        self.generations.c.attempt_id == identity.attempt_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if committed is not None:
                if committed["owner_id"] != identity.owner_id or committed["seal_digest"] != token:
                    raise ExportArtifactError("확정 원장의 실행·봉인 증거가 다릅니다.")
                return "ALREADY_COMMITTED"
            run, attempt = self.lifecycle._locked(session, identity)
            _current_owners(session, self.metadata, attempt)
            if (
                run["status"] != "success"
                or attempt["status"] != "sealed"
                or attempt["seal_digest"] != token
            ):
                return "CONFLICT"
            old = (
                session.execute(
                    select(self.heads)
                    .where(
                        self.heads.c.namespace == plan["namespace"],
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if (old or {}).get("generation_id") != (parent or {}).get("generation_id"):
                return "CONFLICT"
            revision = plan["data_revision_at"]
            collected = plan["source_collected_at"]
            if old is not None and revision < old["data_revision_at"]:
                return "SUPERSEDED"
            if (
                old is not None
                and revision == old["data_revision_at"]
                and old["content_digest"] != content
            ):
                return "CONFLICT"
            generation = str(uuid4())
            session.execute(
                self.generations.insert().values(
                    generation_id=generation,
                    attempt_id=identity.attempt_id,
                    owner_id=identity.owner_id,
                    seal_digest=token,
                    namespace=plan["namespace"],
                    parent_generation_id=old["generation_id"] if old else None,
                    data_revision_at=revision,
                    source_collected_at=collected,
                    content_digest=content,
                    point_count=count,
                    created_at=datetime.now(UTC),
                )
            )
            session.execute(
                self.refs.insert(),
                [
                    dict(
                        generation_id=generation,
                        ordinal=i,
                        artifact_digest=r["artifact_digest"],
                        byte_count=r["byte_count"],
                        point_count=r["point_count"],
                    )
                    for i, r in enumerate(parent_refs if reuse else receipts)
                ],
            )
            values = dict(
                generation_id=generation,
                version=old["version"] + 1 if old else 1,
                data_revision_at=revision,
                content_digest=content,
                source_collected_at=max(collected, old["source_collected_at"])
                if old
                else collected,
            )
            if old is None:
                result = session.execute(
                    insert(self.heads)
                    .values(
                        namespace=plan["namespace"],
                        source_scope=plan["source_scope"],
                        **values,
                    )
                    .on_conflict_do_nothing()
                    .returning(self.heads.c.namespace)
                )
                if result.scalar_one_or_none() is None:
                    raise ExportArtifactError(
                        "동시에 최초 성공 head가 확정됐습니다. 다시 확인하세요."
                    )
            else:
                session.execute(
                    update(self.heads)
                    .where(self.heads.c.namespace == plan["namespace"])
                    .values(**values)
                )
            session.execute(self.pins.delete().where(self.pins.c.attempt_id == identity.attempt_id))
            return "PUBLISHED"
