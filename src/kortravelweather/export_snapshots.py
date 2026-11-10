"""승인된 KMA 실황 generation vector를 고정하는 bounded REST reader.

mutable current/fact/원천 API를 다시 읽지 않는다. provider 미지원은 빈 성공 대신 실패한다.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import re
from collections import defaultdict
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

from kortravelcommon.address_providers import VworldKakaoAddressResolver
from kortravelcommon.reverse_address import AddressTarget
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Computed,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    delete,
    select,
    update,
)
from sqlalchemy.exc import SQLAlchemyError

from .export_artifacts import ExportArtifactError, ExportArtifactStore, ExportGuardTimeout
from .export_capacity import (
    MANIFEST_STATE_RESERVE,
    MAX_MANIFEST_BYTES,
    MAX_SOURCE_SCOPES,
    ExportCapacityExceeded,
    MetadataBudget,
    any_of,
    bounded_manifest_digest,
    read_generation_refs,
)
from .export_heads import ExportHeads, ScopeDescriptor, _digest
from .export_snapshot_refs import insert_snapshot_refs

MAX_SNAPSHOT_LOCATIONS = 5000
MAX_PAGE_BYTES = 4 * 1024 * 1024
PAGE_META_RESERVE = 8192
SNAPSHOT_TTL = timedelta(minutes=15)


def snapshot_expires_at(now):
    """첫 요청 뒤 최소15분을 보장하며 같은 분의 재사용 UPDATE를 합친다."""
    return now.replace(second=0, microsecond=0) + timedelta(minutes=1) + SNAPSHOT_TTL


SOURCE_MAX_AGE = timedelta(hours=3)
# 만료 뒤 이만큼 지난 handle만 publisher tick이 지운다. 유효 cursor(만료 전)는 대상이 아니다.
SNAPSHOT_PRUNE_RETAIN = timedelta(hours=6)
# current handle 하나에 scope ref가 수천 행이라 CASCADE까지 5초 statement 상한 안에 끝나도록
# 작게 둔다. tick이 handle 발급 속도(selection당 15분마다 1개 안팎)보다 충분히 많이 지운다.
SNAPSHOT_PRUNE_LIMIT = 20

logger = logging.getLogger(__name__)


class ExportSnapshotUnavailable(ExportArtifactError):
    pass


class ExportCursorInvalid(ExportArtifactError):
    pass


class AtomicPinMissing(ExportGuardTimeout):
    """원자 pin 직전 ref 파일이 없다. 일시 경합(503 재시도)이며 재발급 대상이다."""


class ExportSnapshotExpired(ExportArtifactError):
    pass


def export_snapshot_tables(metadata: MetaData):
    if "weather_export_snapshots" in metadata.tables:
        return metadata.tables["weather_export_snapshots"], metadata.tables[
            "weather_export_snapshot_refs"
        ]
    snapshots = Table(
        "weather_export_snapshots",
        metadata,
        Column("snapshot_id", String(36), primary_key=True),
        Column("issue_order", BigInteger, Identity(), nullable=False, index=True),
        Column("manifest_digest", String(64), nullable=False, index=True),
        Column("manifest", JSON, nullable=False),
        # JSON/TOAST 해석은 발급 때 한 번만 한다. 작은 snapshot 표에서는 expression
        # index가 있어도 planner가 full scan을 골라 매 요청 큰 JSON을 다시 해석했다.
        Column("selection_kind", String, Computed(
            "(manifest -> 'selection') ->> 'kind'", persisted=True,
        )),
        *[Column(name, String, Computed(expression, persisted=True)) for name, expression in (
            ("selection_signature", "(manifest -> 'selection') ->> 'signature'"),
            ("forecast_style", "(manifest -> 'selection') ->> 'style'"),
            ("forecast_since", "(manifest -> 'selection') ->> 'since'"),
            ("forecast_provider_signature", "(manifest -> 'selection') ->> 'provider_signature'"),
            ("daily_resource", "manifest ->> 'daily_resource'"),
        )],
        Column("source_collected_at", DateTime(timezone=True)),
        Column("data_revision_at", DateTime(timezone=True), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    )
    Index(
        "ix_weather_export_snapshots_kind_issue",
        snapshots.c.selection_kind, snapshots.c.issue_order.desc(),
    )
    Index("ix_weather_export_snapshots_signature_issue",
          snapshots.c.selection_signature, snapshots.c.issue_order.desc())
    Index("ix_weather_export_snapshots_forecast_issue", snapshots.c.selection_kind,
          snapshots.c.forecast_style, snapshots.c.forecast_since,
          snapshots.c.forecast_provider_signature, snapshots.c.issue_order.desc())
    Index("ix_weather_export_snapshots_daily_issue",
          snapshots.c.daily_resource, snapshots.c.issue_order.desc())
    refs = Table(
        "weather_export_snapshot_refs",
        metadata,
        Column(
            "snapshot_id",
            String(36),
            ForeignKey("weather_export_snapshots.snapshot_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        Column("ordinal", Integer, primary_key=True),
        Column(
            "generation_id",
            String(36),
            ForeignKey("weather_export_generations.generation_id"),
            nullable=False,
            index=True,
        ),
    )
    return snapshots, refs


def prune_export_snapshots(
    repository, *, retain: timedelta = SNAPSHOT_PRUNE_RETAIN, limit: int = SNAPSHOT_PRUNE_LIMIT,
) -> int:
    """만료 뒤 retain이 지난 snapshot handle만 작게 지운다.

    expires_at 인덱스로만 고르고 manifest는 읽지 않는다. refs는 ON DELETE CASCADE로
    함께 지워진다. 요청이 계속되는 selection은 재사용 연장·새 발급으로 최신 handle이
    살아 있으므로, fallback handle은 retain 안에 요청된 selection에만 보장된다.
    """
    if not 1 <= limit <= 1000 or retain < timedelta(0):
        raise ValueError("snapshot 정리 상한이 올바르지 않습니다.")
    heads = ExportHeads(repository)
    snapshots, _ = export_snapshot_tables(heads.metadata)
    victims = (
        select(snapshots.c.snapshot_id)
        .where(snapshots.c.expires_at < datetime.now(UTC) - retain)
        .order_by(snapshots.c.expires_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    with heads._transaction() as session:
        return session.execute(
            delete(snapshots).where(snapshots.c.snapshot_id.in_(victims))
        ).rowcount



def _freshness(heads, now):
    """snapshot meta의 수집·revision 시각. 신선도 창 안 승인만 센다(없으면 전체)."""
    from .export_inventory_selection import counts_for_freshness

    heads = list(heads)
    fresh = [h for h in heads if counts_for_freshness(h["source_collected_at"], now)] or heads
    return {
        "source_collected_at": min(h["source_collected_at"] for h in fresh),
        "data_revision_at": min(h["data_revision_at"] for h in fresh),
    }


def ensure_snapshot_files(artifacts, issue):
    """새로 발급(또는 재사용)한 snapshot의 파일이 모두 남아 있는지 공유 guard 안에서 확인한다.

    reader는 freeze transaction 동안 guard를 쥐지 않는다. freeze가 commit하기 전에 head가 바뀌고
    GC가 이전 generation 파일을 지울 수 있다(24시간 넘게 승격되지 않은 scope + 최근 snapshot에도
    없는 드문 경우). 그러면 한 번 다시 발급하고(이제 새 head를 본다), 그래도 없으면 일시 경합
    503으로 돌려 같은 요청을 다시 받게 한다.
    """
    for _ in range(3):
        try:
            frozen = issue()
        except AtomicPinMissing:
            continue  # pin 직전 GC가 이전 generation 파일을 지웠다. 새 head로 다시 발급한다.
        # API freeze는 전체 파일의 존재/크기를 GC guard 안에서 검사하고 ref와 함께
        # commit한다. 같은 발급의 DB pin이 이미 파일을 지키므로 다시 stat하지 않는다.
        # 재사용 handle·이전 성공 fallback·SDK 발급은 기존 all_present/재시도를 유지한다.
        if frozen.pop("_atomic_files_pinned", False):
            return frozen
        refs = [ref for scope in frozen["manifest"].get("scopes", []) for ref in scope["refs"]]
        if artifacts.all_present(refs):
            return frozen
    raise ExportGuardTimeout("snapshot 발급과 artifact 정리가 겹쳤습니다. 다시 요청하세요.")

class FrozenCurrentExports:
    def __init__(
        self, repository, artifacts: ExportArtifactStore, *, signing_key: str, providers=None,
        source_inventory=False, expected_sources=(), optional_sources=(), selection_cache=None,
        immutable_reads=False, page_liveness=False,
    ):
        if not signing_key:
            raise ValueError("기존 service token이 필요합니다.")
        self.selection_cache = selection_cache
        # 앱 공용 선택은 지금부터 이 초 이상 남은 것만 이어받는다(route가 요청 종류별로 정한다).
        # None이면 이 요청이 시작한 뒤 읽힌 선택만 쓴다(긴 첫 페이지가 60초 전체를 쓰도록).
        self.selection_min_remaining: float | None = 0.0
        self.selection_checked_after = monotonic()
        self.selection_deadline = None
        self.reuse_wall_started = datetime.now(UTC)
        self.reuse_generations = set()
        self.reuse_namespaces = set()
        self.reuse_artifacts = set()
        self.reuse_snapshot = None
        self.immutable_reads = immutable_reads
        self.page_liveness = page_liveness
        self.reuse_liveness = None
        self.repository = repository
        self.heads = ExportHeads(repository)
        self.snapshots, self.refs = export_snapshot_tables(self.heads.metadata)
        self.artifacts = artifacts
        self.current_providers = tuple(sorted(set(providers or ["python-kma-api"])))
        self.source_inventory = source_inventory
        self.expected_sources = tuple(expected_sources)
        self.optional_sources = tuple(optional_sources)
        self.current_selection = (
            None
            if self.current_providers == ("python-kma-api",)
            else {
                "kind": "current",
                "signature": _digest(self.current_providers),
                "providers": list(self.current_providers),
            }
        )
        if source_inventory:
            self.current_selection = {
                "kind": "current", "providers": list(self.current_providers),
                "source_inventory": 1,
                "signature": _digest([
                    self.current_providers, sorted(self.expected_sources),
                    sorted(self.optional_sources), 1,
                ]),
            }
        # 인증 체계는 기존 service header 그대로다. 이것은 cursor 변조 방지 전용이다.
        self.key = hmac.digest(signing_key.encode(), b"weather-export-cursor-v1", "sha256")

    def _encode(self, value):
        raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
        return (
            base64.urlsafe_b64encode(raw + hmac.digest(self.key, raw, "sha256"))
            .decode()
            .rstrip("=")
        )

    def _decode(self, cursor):
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096:
                raise ValueError
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            payload, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(signature, hmac.digest(self.key, payload, "sha256")):
                raise ValueError
            value = json.loads(payload)
            if (
                not isinstance(value, dict)
                or set(value) != {"v", "s", "r", "l", "g", "p"}
                or type(value["v"]) is not int
                or value["v"] != 1
                or not isinstance(value["s"], str)
                or len(value["s"]) != 36
                or (
                    value["r"] not in {"locations", "current"}
                    and re.fullmatch(r"current:[0-9a-f]{64}", value["r"]) is None
                    and re.fullmatch(
                        r"forecasts:(ultra_short|short|mid|external):[0-9a-f]{64}", value["r"]
                    )
                    is None
                    and re.fullmatch(r"daily-summaries:[0-9a-f]{64}", value["r"]) is None
                )
                or type(value["l"]) is not int
                or not 1 <= value["l"] <= 5000
            ):
                raise ValueError
            if any(type(value[k]) is not int or value[k] < 0 for k in ("g", "p")):
                raise ValueError
            return value
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ExportCursorInvalid("cursor 형식/서명이 올바르지 않습니다.") from exc

    def _approved_selection(self, connection, locations, *, health_location_ids=None):
        from .export_inventory_selection import approved_source_selection

        if self.page_liveness and self.reuse_liveness is None:
            from .export_page_liveness import capture_liveness

            # 전체 승인 검산과 같은 RR의 삭제 변경 번호다. 수집 INSERT/UPDATE는
            # 번호를 바꾸지 않으며, 아직 전체를 검산하지 않은 결과의 수명은 늘리지 않는다.
            self.reuse_liveness = capture_liveness(connection, self.heads.metadata)

        def read():
            return approved_source_selection(
                connection, self.heads.metadata, locations,
                expected=self.expected_sources, optional=self.optional_sources,
                health_location_ids=health_location_ids,
            )

        if self.selection_cache is None:
            return read()
        key = self.selection_cache.key(
            connection, locations, self.expected_sources, self.optional_sources,
            health_location_ids,
        )
        result, deadline = self.selection_cache.read(
            key, read, checked_after=self.selection_checked_after,
            validate=lambda selection: self._validate_cached_selection(connection, selection),
            min_deadline=(
                self.selection_checked_after + 60
                if self.selection_min_remaining is None
                else self.selection_cache.clock() + self.selection_min_remaining
            ),
            # 같은 삭제 변경 번호(page liveness)에서 읽힌 선택만 잇는다. 사이에 head·plan 삭제가
            # 있었다면 다시 읽어 stale/유실 판정을 이 요청의 transaction에서 한다.
            token=None if self.reuse_liveness is None else tuple(self.reuse_liveness[:3]),
        )
        # 이 요청의 다음 호출(health 재확인 등)은 방금 읽은 선택을 그대로 쓴다.
        self.selection_min_remaining = 0.0
        self.selection_deadline = min(self.selection_deadline or deadline, deadline)
        self.reuse_generations.update(scope.generation_id for scope in result.scopes)
        return result

    def reusable_page_size(self):
        return (len(self.reuse_generations) * 96 + len(self.reuse_artifacts) * 128
                + sum(len(value.encode()) for value in self.reuse_namespaces) + 512)

    def begin_page_reuse(self):
        """API worker가 실제 miss 읽기를 시작할 때만 호출한다. 기존 lease는 연장하지 않는다."""
        if self.selection_deadline is not None or self.reuse_snapshot is not None:
            raise RuntimeError("이미 읽은 페이지의 승인 수명을 다시 시작할 수 없습니다.")
        self.selection_checked_after = monotonic()
        self.reuse_wall_started = datetime.now(UTC)

    def validate_reusable_page(self):
        """검증한 결과의 작은 DB proof와 실제 사용 파일만 검사한다. 전체 freeze는 하지 않는다."""
        from .export_page_cache import PageReuseExpired

        now = datetime.now(UTC)
        if now - self.reuse_wall_started >= timedelta(seconds=60) or self.reuse_snapshot is None:
            raise PageReuseExpired()
        with (self.artifacts.guard(shared=True)
              if self.reuse_artifacts and not self.immutable_reads else nullcontext()):
            with self.repository.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection, connection.begin():
                connection.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
                row = connection.execute(select(
                    self.snapshots.c.snapshot_id, self.snapshots.c.manifest_digest,
                    self.snapshots.c.expires_at,
                ).where(
                    self.snapshots.c.snapshot_id == self.reuse_snapshot[0]
                )).mappings().one_or_none()
                if row is None or row["expires_at"] <= now:
                    raise PageReuseExpired()
                if row["manifest_digest"] != self.reuse_snapshot[1]:
                    raise ExportArtifactError("고정 export manifest의 무결성이 바뀌었습니다.")
                if self.reuse_liveness is not None:
                    from .export_page_liveness import capture_liveness

                    live = capture_liveness(connection, self.heads.metadata)
                    if live[:2] != self.reuse_liveness[:2]:
                        raise ExportArtifactError(
                            "검증 뒤 공개 세대 또는 불변 plan의 삭제가 발생했습니다."
                        )
                    if live[2] != self.reuse_liveness[2]:
                        raise PageReuseExpired()
                else:
                    if self.reuse_namespaces and set(connection.scalars(select(
                        self.heads.heads.c.namespace,
                    ).where(any_of(
                        self.heads.heads.c.namespace, sorted(self.reuse_namespaces),
                    )))) != self.reuse_namespaces:
                        # 현재 head 유실은 재구성 경로에서 즉시 stale로 판정한다.
                        raise PageReuseExpired()
                    generations, plans = self.heads.generations, self.heads.plans
                    present = dict(connection.execute(select(
                        generations.c.generation_id, generations.c.attempt_id,
                    ).where(any_of(
                        generations.c.generation_id, sorted(self.reuse_generations),
                    ))).all())
                    attempts = set(present.values())
                    if present.keys() != self.reuse_generations or set(connection.scalars(select(
                        plans.c.attempt_id,
                    ).where(any_of(plans.c.attempt_id, sorted(attempts))))) != attempts:
                        raise ExportArtifactError(
                            "검증한 export의 세대 또는 불변 plan이 유실됐습니다."
                        )
            # 재사용 응답은 이미 검증한 메모리 페이지다. 파일 유실(GC·삭제)은 숨기지 않도록 매 hit
            # 존재·크기를 확인하지만, 파일 전체를 다시 읽어 hash하지는 않는다(odroid eMMC IO).
            # 내용 손상은 다음 miss의 재구성이 동일 fd hash로 잡는다.
            for digest, byte_count, _point_count in self.reuse_artifacts:
                self.artifacts.present(digest, byte_count=byte_count)
        self.require_current_selection()
        if datetime.now(UTC) >= row["expires_at"]:
            raise PageReuseExpired()
        self._renew_reused(dict(row, _reused=True))

    def _validate_cached_selection(self, connection, selection):
        # 60초 승인은 수집/실패 상태에만 적용한다. 불변 plan·공개 세대 유실은 즉시 검산한다.
        # 파일·chunk 참조 검산은 기존 page/guard 경로에서 계속 수행한다.
        wanted = {scope.generation_id for scope in selection.scopes}
        if not wanted:
            return
        generations, plans = self.heads.generations, self.heads.plans
        # 큰 JOIN은 부하 중 5초 상한을 넘었다. 같은 RR의 두 PK 집합으로 같은 존재를 검산한다.
        present = dict(connection.execute(select(
            generations.c.generation_id, generations.c.attempt_id,
        ).where(any_of(generations.c.generation_id, sorted(wanted)))).all())
        attempts = set(present.values())
        if present.keys() != wanted or set(connection.scalars(select(plans.c.attempt_id).where(
            any_of(plans.c.attempt_id, sorted(attempts)),
        ))) != attempts:
            raise ExportArtifactError("승인 source의 불변 plan 또는 공개 세대가 유실됐습니다.")

    def require_current_selection(self):
        # 파일 검사·페이지 DB 조회·직렬화가 길어져도 만료 캐시로 200/304를 만들지 않는다.
        if self.selection_cache is not None and self.selection_deadline is not None:
            self.selection_cache.require_current(self.selection_deadline)

    def _select_current_scopes(self, connection, locations):
        if self.source_inventory:
            try:
                selection = self._approved_selection(connection, locations)
            except ExportGuardTimeout:
                raise
            except (ExportArtifactError, KeyError, ValueError, TypeError) as exc:
                raise ExportSnapshotUnavailable(
                    "실제 승인 source 집합이 준비되지 않았습니다."
                ) from exc
            selected = {item.descriptor.namespace: item.location_ids for item in selection.scopes}
            heads = {item.descriptor.namespace: {
                "generation_id": item.generation_id,
                "source_collected_at": item.source_collected_at,
                "data_revision_at": item.data_revision_at,
                "published_at": item.published_at,
            } for item in selection.scopes}
            return (
                [item.descriptor for item in selection.scopes], heads, selected,
                selection.unconfirmed,
            )
        grids = defaultdict(list)
        for location in locations:
            if "python-kma-api" in self.current_providers:
                if location["nx"] is None or location["ny"] is None:
                    raise ExportSnapshotUnavailable("활성 location grid가 준비되지 않았습니다.")
                grids[(location["nx"], location["ny"])].append(location["location_id"])
        descriptors = [
            ScopeDescriptor(
                entity_type="weather_response",
                entity_id=f"grid:{nx}:{ny}",
                nx=nx,
                ny=ny,
                location_ids=tuple(sorted(keys)),
                normalizer_revision="kma-v1",
                winner_policy_revision="kma-base-v1",
                coverage_contract="kma-grid-nowcast-v1",
            )
            for (nx, ny), keys in sorted(grids.items())
        ]
        external = set(self.current_providers) - {"python-kma-api"}
        if external:
            from .export_external_cohorts import selected_external_descriptors

            try:
                descriptors.extend(
                    selected_external_descriptors(
                        connection, self.heads.metadata, locations, external
                    )
                )
            except (ExportArtifactError, KeyError, ValueError, TypeError) as exc:
                raise ExportSnapshotUnavailable(
                    "외부 원천 선택 집합이 준비되지 않았습니다."
                ) from exc
        if len(descriptors) > MAX_SOURCE_SCOPES:
            raise ExportSnapshotUnavailable("현재 원천 vector 상한을 넘었습니다.")
        heads = {
            row["namespace"]: dict(row)
            for row in connection.execute(
                select(self.heads.heads).where(
                    self.heads.heads.c.namespace.in_([d.namespace for d in descriptors])
                )
            ).mappings()
        }
        if len(heads) != len(descriptors):
            raise ExportSnapshotUnavailable("선택 위치 전체의 성공 head가 준비되지 않았습니다.")
        return descriptors, heads, None, False

    def _freeze(self):
        # catalog와 head vector를 동일 MVCC에서 읽는다. 파일 I/O는 이 TX 밖이다.
        with (
            self.repository.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection,
            connection.begin(),
        ):
            connection.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
            catalog = self.heads.metadata.tables["weather_locations"]
            from .export_inventory_selection import catalog_source_projection

            source_from, source_columns = (
                catalog_source_projection(catalog) if self.source_inventory else (catalog, [])
            )

            locations = list(
                connection.execute(
                    select(
                        catalog.c.location_id,
                        catalog.c.name,
                        catalog.c.latitude,
                        catalog.c.longitude,
                        catalog.c.nx,
                        catalog.c.ny,
                        *source_columns,
                    )
                    .select_from(source_from)
                    .where(catalog.c.enabled.is_(True))
                    .order_by(catalog.c.location_id)
                    .limit(MAX_SNAPSHOT_LOCATIONS + 1)
                ).mappings()
            )
            if not locations or len(locations) > MAX_SNAPSHOT_LOCATIONS:
                raise ExportSnapshotUnavailable("활성 location 전체 집합이 준비되지 않았습니다.")
            descriptors, heads, selected, unconfirmed = self._select_current_scopes(
                connection, locations
            )
            generations = [heads[d.namespace]["generation_id"] for d in descriptors]
            grouped_refs = read_generation_refs(connection, self.heads.refs, generations)
            scopes = []
            budget = MetadataBudget()
            for descriptor in descriptors:
                head = heads[descriptor.namespace]
                refs = grouped_refs[head["generation_id"]]
                if (
                    not refs
                    or len(refs) > 8
                    or sum(r["byte_count"] for r in refs) > 16 * 1024 * 1024
                ):
                    raise ExportSnapshotUnavailable(
                        "승인 manifest의 artifact 상한이 올바르지 않습니다."
                    )
                scopes.append(
                    {
                        "namespace": descriptor.namespace,
                        "generation_id": head["generation_id"],
                        "source_scope": descriptor.source_scope,
                        "nx": getattr(descriptor, "nx", None),
                        "ny": getattr(descriptor, "ny", None),
                        "provider": descriptor.provider,
                        "forecast_style": descriptor.forecast_style,
                        "location_ids": list(
                            selected[descriptor.namespace] if selected else descriptor.location_ids
                        ),
                        "refs": refs,
                        **({
                            "approved_location_ids": list(descriptor.location_ids),
                            "dataset_key": descriptor.dataset_key,
                            "source_collected_at": head["source_collected_at"].isoformat(),
                            "data_revision_at": head["data_revision_at"].isoformat(),
                        } if self.source_inventory else {}),
                    }
                )
                budget.add_bounded_record(scopes[-1], MAX_MANIFEST_BYTES)
            entries = self.heads.metadata.tables["reverse_address_entries"]
            addresses = {
                row["target_key"]: row
                for row in connection.execute(
                    select(
                        entries.c.target_key,
                        entries.c.coordinate_key,
                        entries.c.payload["address"]["legal_dong_code"]
                        .as_string()
                        .label("legal_dong_code"),
                        entries.c.payload["region"]["sido"].as_string().label("sido_name"),
                        entries.c.payload["region"]["sigungu"].as_string().label("sigungu_name"),
                    ).where(
                        entries.c.provider_id == VworldKakaoAddressResolver.provider_id,
                        entries.c.status.in_(["ok", "error"]),
                        entries.c.target_key.in_([row["location_id"] for row in locations]),
                    )
                ).mappings()
            }
            items = []
            for location in locations:
                entry = addresses.get(location["location_id"])
                if (
                    entry
                    and entry["coordinate_key"]
                    != AddressTarget(
                        location["location_id"],
                        float(location["longitude"]),
                        float(location["latitude"]),
                    ).coordinate_key
                ):
                    entry = None
                code = entry["legal_dong_code"] if entry else None
                items.append(
                    {
                        "location_id": location["location_id"],
                        "name": location["name"],
                        "latitude": float(location["latitude"]),
                        "longitude": float(location["longitude"]),
                        "legal_dong_code": code
                        if isinstance(code, str) and re.fullmatch(r"[0-9]{10}", code)
                        else None,
                        "sido_name": entry["sido_name"] if entry else None,
                        "sigungu_name": entry["sigungu_name"] if entry else None,
                    }
                )
                budget.add_bounded_record(items[-1], MAX_MANIFEST_BYTES)
            manifest = {"schema": 1, "scopes": scopes, "locations": items}
            if self.source_inventory:
                manifest["unconfirmed_sources"] = unconfirmed
            if self.current_selection is not None:
                manifest["selection"] = self.current_selection
            digest = bounded_manifest_digest(
                manifest, MAX_MANIFEST_BYTES - MANIFEST_STATE_RESERVE,
            )
            now = datetime.now(UTC)
            # 재사용 조건은 그대로다: 같은 digest의 유효한 handle이 그 selection의 **가장 최근**
            # 발급이어야 한다. 같은 digest면 manifest(안의 selection 포함)가 같으므로, 그 handle보다
            # 뒤에 발급된 같은 selection handle이 없는지만 보면 된다. 예전처럼 selection 조건으로 표
            # 전체를 먼저 훑으면 행마다 큰 manifest json(평균 약 330 KB)을 해석해 65행에 약 3.7초가
            # 걸렸고(2026-11-02 odroid), 5초 statement 상한에 걸려 export 503이 됐다. digest 조회는
            # index로 1 ms 미만이고, 뒤의 확인은 더 새 행만 해석한다.
            existing = (
                connection.execute(
                    select(self.snapshots)
                    .where(
                        self.snapshots.c.manifest_digest == digest,
                        self.snapshots.c.expires_at > now,
                    )
                    .order_by(self.snapshots.c.issue_order.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None and connection.scalar(
                select(self.snapshots.c.issue_order).where(
                    self.snapshots.c.issue_order > existing["issue_order"],
                    *self._same_selection(),
                ).limit(1)
            ) is None:
                return {**existing, "_reused": True}
            row = {
                "snapshot_id": str(uuid4()),
                "manifest_digest": digest,
                "manifest": manifest,
                **_freshness(heads.values(), now),
                "created_at": now,
                "expires_at": snapshot_expires_at(now),
            }
            # INSERT(큰 manifest의 generated column 계산·ref 묶음)는 guard 밖에서 끝낸다.
            # 공유 guard는 파일 존재 stat와 commit만 감싼다(#209: reader가 DB 작업 동안
            # guard를 쥐면 writer가 5초 상한에 걸린다). GC는 배타 guard 안에서 commit된 ref만
            # 보므로, 여기 stat가 통과한 뒤의 GC는 commit된 pin을 보고 파일을 지킨다.
            connection.execute(self.snapshots.insert().values(**row))
            insert_snapshot_refs(
                connection, self.refs, row["snapshot_id"],
                (scope["generation_id"] for scope in scopes),
            )
            if self.immutable_reads:
                with self.artifacts.guard(shared=True):
                    try:
                        for digest, size in {
                            (ref["artifact_digest"], ref["byte_count"])
                            for scope in scopes for ref in scope["refs"]
                        }:
                            self.artifacts.present(digest, byte_count=size)
                    except ExportArtifactError as exc:
                        # 발급 TX를 되돌리고 ensure_snapshot_files가 새 head로 다시 발급한다.
                        raise AtomicPinMissing() from exc
                    connection.commit()
                row["_atomic_files_pinned"] = True
            return row

    def _same_selection(self):
        """이 reader와 같은 selection(없으면 selection 없는 기본 집합)인 snapshot 행 조건들.

        하위 reader(예보)도 같은 모양(조건 tuple)을 돌려준다. 호출부는 풀어서 쓴다.
        """
        if self.current_selection is None:
            return (self.snapshots.c.selection_kind.is_(None),)
        return (self.snapshots.c.selection_signature == self.current_selection["signature"],)

    def _previous_success(self):
        # reader TTL와 마지막 승인 data manifest의 수명은 다르다. 새 handle만 만든다.
        with self.heads._transaction() as session:
            row = (
                session.execute(
                    select(self.snapshots)
                    .where(*self._same_selection())
                    .order_by(self.snapshots.c.issue_order.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ExportSnapshotUnavailable("이전 승인 집합이 없습니다.")
            frozen = dict(row)
            now = datetime.now(UTC)
            if frozen["expires_at"] > now:
                frozen["_reused"] = True
            else:
                frozen.pop("issue_order", None)
                for column in self.snapshots.c:
                    if column.computed is not None:
                        frozen.pop(column.name, None)
                frozen.update(
                    snapshot_id=str(uuid4()), created_at=now, expires_at=snapshot_expires_at(now)
                )
                session.execute(self.snapshots.insert().values(**frozen))
                insert_snapshot_refs(
                    session, self.refs, frozen["snapshot_id"],
                    (scope["generation_id"] for scope in frozen["manifest"]["scopes"]),
                )
            frozen["_source_unconfirmed"] = True
            return frozen

    def page(self, resource: str, *, limit: int, cursor: str | None):
        # 공유 guard는 artifact 파일 하나 읽는 동안만 잡는다(read_shared). 읽을 파일은
        # commit된 snapshot ref·head·보존 창이 GC에서 지킨다(freeze·health는 DB만 본다).
        return self._page_guarded(resource, limit=limit, cursor=cursor)

    def _renew_reused(self, frozen):
        """첫 페이지가 재사용한 기존 handle의 TTL을 연장한다(best-effort).

        만료 직전 handle로 새 pagination을 시작하면 다음 페이지가410이 되고, 처음부터
        다시 받아도 같은 handle을 재사용한다. 내용과 snapshot_id는 그대로라 ETag도 같다.
        REPEATABLE READ freeze가 끝난 뒤 짧은 READ COMMITTED transaction에서 갱신해
        동시 reader끼리 직렬화 충돌(40001)을 만들지 않는다. 실패하면 원래 row를 쓴다.
        """
        if not frozen.pop("_reused", False):
            return frozen
        now = datetime.now(UTC)
        # 새 handle은 분 경계까지 여유를 둔다. 충분한 수명이 남았으면 DB 쓰기·잠금이 없다.
        if frozen["expires_at"] >= now + SNAPSHOT_TTL:
            return frozen
        table = self.snapshots
        try:
            with self.repository.engine.connect() as connection, connection.begin():
                connection.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
                connection.exec_driver_sql("SET LOCAL lock_timeout = '1s'")
                renewed = connection.execute(
                    update(table)
                    .where(
                        table.c.snapshot_id == frozen["snapshot_id"],
                        table.c.expires_at > now,
                        table.c.expires_at < snapshot_expires_at(now),
                    )
                    .values(expires_at=snapshot_expires_at(now))
                    .returning(table.c.expires_at)
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            logger.warning(
                "weather_export_snapshot_renew_deferred reason=%s", type(exc).__name__
            )
            return frozen
        if renewed is not None:
            frozen["expires_at"] = renewed
        return frozen

    def _capacity_fallback(self, frozen):
        # 과거 healthy handle을 바꾸지 않고, fallback cursor에도 거절 상태를 보존한다.
        if frozen["manifest"].get("capacity_limited"):
            return frozen
        now = datetime.now(UTC)
        row = {key: value for key, value in frozen.items()
               if key in self.snapshots.c and key != "issue_order"
               and self.snapshots.c[key].computed is None}
        row.update(snapshot_id=str(uuid4()), created_at=now, expires_at=snapshot_expires_at(now),
                   manifest=dict(frozen["manifest"], capacity_limited=True))
        MetadataBudget().add(row["manifest"])
        row["manifest_digest"] = _digest(row["manifest"])
        with self.heads._transaction() as s:
            s.execute(self.snapshots.insert().values(**row))
            insert_snapshot_refs(
                s, self.refs, row["snapshot_id"],
                (scope["generation_id"] for scope in row["manifest"]["scopes"]),
            )
        return row

    def _page_guarded(self, resource: str, *, limit: int, cursor: str | None):
        if resource not in {"locations", "current"} or not 1 <= limit <= 5000:
            raise ExportCursorInvalid("지원하지 않는 resource/limit입니다.")
        cursor_resource = (
            resource
            if self.current_selection is None
            else (resource + ":" + self.current_selection["signature"])
        )
        if cursor:
            state = self._decode(cursor)
            if state["r"] != cursor_resource or state["l"] != limit:
                raise ExportCursorInvalid("cursor의 resource/limit은 변경할 수 없습니다.")
            with self.heads._transaction() as session:
                frozen = (
                    session.execute(
                        select(self.snapshots).where(self.snapshots.c.snapshot_id == state["s"])
                    )
                    .mappings()
                    .one_or_none()
                )
                if frozen is None or frozen["expires_at"] <= datetime.now(UTC):
                    raise ExportSnapshotExpired(
                        "snapshot이 만료됐습니다. 첫 페이지부터 다시 요청하세요."
                    )
                frozen = dict(frozen)
        else:
            def issue():
                try:
                    return self._freeze()
                except ExportCapacityExceeded:
                    return self._capacity_fallback(self._previous_success())
                except ExportSnapshotUnavailable:
                    return self._previous_success()

            frozen = ensure_snapshot_files(self.artifacts, issue)
            frozen = self._renew_reused(frozen)
            state = {
                "v": 1,
                "s": frozen["snapshot_id"],
                "r": cursor_resource,
                "l": limit,
                "g": 0,
                "p": 0,
            }
        manifest = frozen["manifest"]
        self.reuse_snapshot = (frozen["snapshot_id"], frozen["manifest_digest"])
        self.reuse_generations.update(scope["generation_id"] for scope in manifest["scopes"])
        self.reuse_namespaces.update(scope["namespace"] for scope in manifest["scopes"])
        output, size = [], 0
        if resource == "locations":
            start = state["p"]
            if state["g"] != 0 or start > len(manifest["locations"]):
                raise ExportCursorInvalid("cursor 범위가 올바르지 않습니다.")
            for item in manifest["locations"][start : start + limit]:
                item_size = len(json.dumps(item, ensure_ascii=False).encode()) + 1
                if item_size > MAX_PAGE_BYTES - PAGE_META_RESERVE:
                    raise ExportSnapshotUnavailable(
                        "단일 location이 페이지 byte 상한을 넘었습니다."
                    )
                if size + item_size > MAX_PAGE_BYTES - PAGE_META_RESERVE:
                    break
                output.append(item)
                size += item_size
            state["p"] = start + len(output)
            more = state["p"] < len(manifest["locations"])
        else:
            scopes = manifest["scopes"]
            if state["g"] > len(scopes):
                raise ExportCursorInvalid("cursor 범위가 올바르지 않습니다.")
            more = False
            # artifact GC(export_gc)는 유효하거나 보존 창 안의 snapshot과 최근 snapshot이
            # 가리키는 세대의 파일을 지우지 않는다. snapshot_refs가 generation 행 삭제도 막는다.
            # 파일은 검증한 같은 파일(identity 동일, 10분 이내)이면 복호 캐시를 쓰고, 아니면 동일fd
            # hash/count를 재검증한다. 누락은 성공으로 반환하지 않는다.
            while state["g"] < len(scopes):
                scope = scopes[state["g"]]
                points = []
                selected = set(scope["location_ids"])
                approved_ids = set(scope.get("approved_location_ids", scope["location_ids"]))
                for ref in scope["refs"]:
                    self.reuse_artifacts.add((
                        ref["artifact_digest"], ref["byte_count"], ref["point_count"],
                    ))
                    for point in self.artifacts.read_shared(
                        ref["artifact_digest"],
                        byte_count=ref["byte_count"],
                        point_count=ref["point_count"],
                    ):
                        if (
                            point["provider"] != scope.get("provider", "python-kma-api")
                            or point["forecast_style"] != scope.get("forecast_style", "nowcast")
                            or point["location_id"] not in approved_ids
                        ):
                            raise ExportSnapshotUnavailable(
                                "승인된 scope와 artifact가 다릅니다."
                            )
                        if point["location_id"] in selected:
                            points.append(point)
                points.sort(key=lambda p: (p["location_id"], p["provider"], p["metric_key"]))
                if state["p"] > len(points):
                    raise ExportCursorInvalid("cursor 범위가 올바르지 않습니다.")
                while state["p"] < len(points):
                    point = points[state["p"]]
                    item = {
                        "location_id": point["location_id"],
                        "provider": point["provider"],
                        "metric_key": point["metric_key"],
                        "value": float(point["value_number"])
                        if point["value_number"] is not None
                        else point["value_text"],
                        "unit": point["unit"],
                        "target_at": point["target_at"],
                        "known_at": point["known_at"],
                        "forecast_style": point["forecast_style"],
                    }
                    item_size = len(json.dumps(item, ensure_ascii=False).encode()) + 1
                    if item_size > MAX_PAGE_BYTES - PAGE_META_RESERVE:
                        raise ExportSnapshotUnavailable(
                            "단일 point가 페이지 byte 상한을 넘었습니다."
                        )
                    if (
                        len(output) >= limit
                        or size + item_size > MAX_PAGE_BYTES - PAGE_META_RESERVE
                    ):
                        more = True
                        break
                    output.append(item)
                    size += item_size
                    state["p"] += 1
                if more:
                    break
                state["g"] += 1
                state["p"] = 0
        now = datetime.now(UTC)
        stale = (
            frozen.get("_source_unconfirmed", False)
            or now - frozen["source_collected_at"] > SOURCE_MAX_AGE
            or now - frozen["data_revision_at"] > SOURCE_MAX_AGE
        )
        if self.source_inventory:
            from .export_inventory_selection import source_max_age

            stale = frozen.get("_source_unconfirmed", False) or manifest["unconfirmed_sources"]
            from .export_inventory_selection import counts_for_freshness

            stale |= any(
                now - datetime.fromisoformat(scope[key]) > source_max_age(scope["dataset_key"])
                for scope in manifest["scopes"]
                if counts_for_freshness(datetime.fromisoformat(scope["source_collected_at"]), now)
                for key in ("source_collected_at", "data_revision_at")
            ) or not any(
                counts_for_freshness(datetime.fromisoformat(scope["source_collected_at"]), now)
                for scope in manifest["scopes"]
            )
        stale |= bool(manifest.get("capacity_limited"))
        owners, attempts = self.heads.lifecycle.owners, self.heads.lifecycle.attempts
        runs = self.heads.metadata.tables["weather_sync_runs"]
        with (
            self.repository.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as session, session.begin(),
        ):
            session.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
            catalog = self.heads.metadata.tables["weather_locations"]
            from .export_inventory_selection import catalog_source_projection

            source_from, source_columns = (
                catalog_source_projection(catalog) if self.source_inventory else (catalog, [])
            )

            roster = session.execute(
                select(
                    catalog.c.location_id,
                    catalog.c.nx,
                    catalog.c.ny,
                    catalog.c.latitude,
                    catalog.c.longitude,
                    *source_columns,
                )
                .select_from(source_from)
                .where(catalog.c.enabled.is_(True))
                .order_by(catalog.c.location_id)
                .limit(MAX_SNAPSHOT_LOCATIONS + 1)
            ).all()
            stale |= len(roster) > MAX_SNAPSHOT_LOCATIONS
            if self.source_inventory:
                try:
                    latest = self._approved_selection(
                        session, [row._mapping for row in roster],
                    )
                    stale |= latest.unconfirmed or {
                        (item.descriptor.namespace, item.location_ids) for item in latest.scopes
                    } != {
                        (scope["namespace"], tuple(scope["location_ids"]))
                        for scope in manifest["scopes"]
                    }
                except ExportGuardTimeout:
                    raise
                except (ExportArtifactError, KeyError, ValueError, TypeError):
                    stale = True
            else:
                expected = sorted(
                    (key, scope["nx"], scope["ny"])
                    for scope in manifest["scopes"]
                    if scope.get("provider", "python-kma-api") == "python-kma-api"
                    for key in scope["location_ids"]
                )
                if "python-kma-api" in self.current_providers:
                    stale |= [tuple(row)[:3] for row in roster] != expected
                else:
                    stale |= [
                        (row.location_id, float(row.latitude), float(row.longitude))
                        for row in roster
                    ] != [
                        (row["location_id"], row["latitude"], row["longitude"])
                        for row in manifest["locations"]
                    ]
                external = set(self.current_providers) - {"python-kma-api"}
                if external:
                    from .export_external_cohorts import selected_external_descriptors

                    try:
                        current_external = selected_external_descriptors(
                            session, self.heads.metadata, [row._mapping for row in roster], external
                        )
                        stale |= {d.namespace for d in current_external} != {
                            scope["namespace"]
                            for scope in manifest["scopes"]
                            if scope.get("provider", "python-kma-api") != "python-kma-api"
                        }
                    except (ExportArtifactError, KeyError, ValueError, TypeError):
                        stale = True
            # cursor로 같은 reader handle을 이어 읽어도 현재 head 유효성을 확인한다.
            # 이전 manifest를 반환했다는 메모리 flag만으로는 다음 요청에 stale이
            # 사라져 삭제/복구 중인 source를 정상이라고 표시할 수 있다.
            current_namespaces = set(
                session.scalars(
                    select(self.heads.heads.c.namespace)
                    .join(
                        self.heads.generations,
                        self.heads.generations.c.generation_id == self.heads.heads.c.generation_id,
                    )
                    .where(
                        any_of(self.heads.heads.c.namespace,
                               [scope["namespace"] for scope in manifest["scopes"]])
                        if session.dialect.name == "postgresql" else
                        self.heads.heads.c.namespace.in_(
                            [scope["namespace"] for scope in manifest["scopes"]])
                    )
                )
            )
            stale |= current_namespaces != {scope["namespace"] for scope in manifest["scopes"]}
            statuses = session.execute(
                select(attempts.c.status, runs.c.status, self.heads.generations.c.generation_id)
                .select_from(
                    owners.join(attempts, owners.c.active_attempt_id == attempts.c.attempt_id)
                    .join(runs, runs.c.run_id == attempts.c.run_id)
                    .outerjoin(
                        self.heads.generations,
                        self.heads.generations.c.attempt_id == attempts.c.attempt_id,
                    )
                )
                .where(
                    any_of(owners.c.source_scope,
                           [scope["source_scope"] for scope in manifest["scopes"]])
                    if session.dialect.name == "postgresql" else
                    owners.c.source_scope.in_(
                        [scope["source_scope"] for scope in manifest["scopes"]])
                )
            ).all()
        stale |= len(statuses) != len({s["source_scope"] for s in manifest["scopes"]}) or any(
            a == "failed"
            or run not in {"running", "success"}
            or (run == "success" and generation is None)
            for a, run, generation in statuses
        )
        status = "stale" if stale else "ok"
        meta = {
            "generated_at": now.isoformat(),
            "source_collected_at": frozen["source_collected_at"].isoformat(),
            "source_status": status,
            "item_count": len(output),
            "snapshot_id": frozen["snapshot_id"],
            "snapshot_generated_at": frozen["created_at"].isoformat(),
            "as_of": frozen["created_at"].isoformat(),
            "next_cursor": self._encode(state) if more else None,
            "has_more": more,
        }
        # generated_at은 ETag에 넣지 않는다. 데이터/vector/페이지/상태 변화는 포함한다.
        etag = (
            'W/"'
            + _digest(
                [frozen["snapshot_id"], frozen["manifest_digest"], resource, limit, cursor, status]
            )
            + '"'
        )
        if self.immutable_reads and datetime.now(UTC) >= frozen["expires_at"]:
            raise ExportSnapshotExpired("읽는 동안 고정 snapshot이 만료됐습니다.")
        return {"items": output, "meta": meta}, etag
