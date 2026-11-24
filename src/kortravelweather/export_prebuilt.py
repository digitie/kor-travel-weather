"""Map 표준 프로필(W2 current) 완료 페이지의 사전 구성(호출 검토 A3, 설계 P0-A).

Map은 ``/v1/service/exports/current?limit=2000``을 매시 :25·:40에 처음부터 끝까지 읽는다.
첫 페이지마다 요청 경로에서 freeze(catalog·승인 선택·manifest·handle 발급)와 artifact 본문
조립을 했고, 캐시는 프로세스 메모리·60초라 재시작이나 다음 순회에서 다시 처음부터 만들었다.

여기서는 publisher가 승격한 뒤 별도 유한 job(``build_current_profile``)이 같은 freeze를 한 번 하고
그 manifest의 모든 페이지 **본문(불변 data payload)**을 DB에 저장한다. 요청 경로는:

1. 짧은 RR 하나에서 **입력 proof**(삭제 epoch 3개·승인 inventory digest·catalog/주소 digest)를
   읽어 builder가 manifest를 만든 RR의 proof와 같은지, 시간 경계(``valid_until``) 전인지 본다.
   같으면 지금 freeze해도 같은 manifest다(아래 "proof가 덮는 것").
2. handle 수명: 같은 manifest의 유효 handle은 freeze 재사용과 같은 조건(그 selection의 최근
   발급)으로 이어 쓴다(build handle이 만료된 뒤 앞 요청이 발급한 handle 포함). 유효 handle은 기존
   조건부 갱신을 먼저 시도하고, 그래도 남은 수명이 15분 미만이면 아래 검산을 모두 통과한 뒤 같은
   manifest로 새 handle을 발급한다. 만료 handle은 연장하지 않는다(새 handle만 발급). 새 handle의
   snapshot ref가 참조 세대를 GC에서 지킨다. 발급을 못 하면 기존 freeze 경로로 간다.
3. 상태(``source_status``): builder 판정이 60초 승인 수명 안이고 삭제 epoch가 같으면 그 판정을
   쓰고, 아니면 기존 첫 페이지처럼 새로 판정한다. 새 판정의 승인 선택이 manifest와 다르면
   (scope 집합·미확인 여부) 사전 구성을 버리고 기존 freeze로 간다.
4. 본문은 저장된 payload의 sha256·byte·항목 수를 검산해 쓰고, 그 페이지가 읽었던 artifact의
   존재·크기는 기존 재사용 경로처럼 매번 확인한다. snapshot/cursor envelope(``snapshot_id``·
   ``next_cursor``·``generated_at``·상태)은 요청에서 결합한다.

준비되지 않았거나 proof가 다르면 아무 것도 바꾸지 않고 기존 경로(같은 export 동시성 제한)로
간다. 요청 경로는 builder를 부르지 않는다(동기 재구성 폭증 없음).

proof가 덮는 것: manifest = f(활성 catalog 행, 주소 행, 승인 inventory 행, 세대·plan, 시각).
inventory는 승격 TX에서만 upsert되고(``record_approved_locations``) 세대·plan은 불변이며 삭제는
page liveness epoch가 센다. 시각에 따른 만료·신선도 경계는 builder 선택의
``health_recheck_at``(가장 이른 경계)을 ``valid_until``로 둔다. 실행·시도 상태(실패 등)는
manifest가 아니라 상태 판정의 몫이라 3번에서 다룬다.

W5(daily-summaries)는 아직 하지 않는다. 설계 §P0-A는 W5 불변 프로필을 summary watermark·
coverage digest를 key/proof에 넣은 가산 opt-in 계약 revision으로 요구하고 Map 소비자 검증을
함께 바꾸라고 한다(현재 W5는 페이지별 RR에서 최신 집계를 읽는다).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    cast,
    delete,
    extract,
    func,
    literal,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.exc import SQLAlchemyError

from .export_artifacts import ExportArtifactError, ExportGuardTimeout

logger = logging.getLogger(__name__)

#: 저장 형식·proof 의미가 바뀌면 올린다. 다른 버전의 build는 profile key가 달라 쓰지 않는다.
PREBUILT_VERSION = 1
#: Map 기본 ``KOR_TRAVEL_MAP_KOR_TRAVEL_WEATHER_PAGE_SIZE``(map settings.py)와 같다.
PROFILE_LIMIT = 2000
#: build 하나의 페이지 수·본문 byte 상한.
#: 운영 규모(point 약 2.7만, limit 2000)는 14페이지·약 6MiB다.
MAX_BUILD_PAGES = 64
MAX_BUILD_BYTES = 64 * 1024 * 1024
#: profile마다 남기는 build 수. 앞 build로 순회 중인 cursor가 끝까지 사전 구성 본문을 쓰게 한다.
KEEP_BUILDS = 2
#: builder 한 번의 전체 예산(초). 넘으면 저장하지 않고 다음 실행으로 넘긴다.
BUILD_BUDGET_SECONDS = 600.0
#: builder 상태 판정을 요청이 이어받는 승인 수명(소유자 결정의 최대 60초)과 최소 남은 수명.
STATUS_MAX_AGE = timedelta(seconds=60)
#: no-op 판정 때 시간 경계까지 이만큼은 남아야 다시 만들지 않는다.
VALID_UNTIL_MARGIN = timedelta(minutes=5)
#: builder의 정상 결과. CLI(배포 hook)와 Dagster job이 같은 집합을 쓴다. ``not_ready``는 아직 완결
#: 승인 집합이 없거나 이전 승인 fallback 상태라 만들 것이 없다는 뜻이다(요청 경로가 기존
#: fallback·503을 그대로 쓴다). ``unsupported``는 PostgreSQL이 아닌 저장소다.
SUCCESS_OUTCOMES = frozenset({"built", "unchanged", "disabled", "unsupported", "not_ready"})


def export_prebuilt_tables(metadata: MetaData):
    """build(작은 proof·상태 행)와 페이지 본문 표. 본문은 build 삭제와 함께 지운다."""
    if "weather_export_prebuilt_builds" in metadata.tables:
        return (metadata.tables["weather_export_prebuilt_builds"],
                metadata.tables["weather_export_prebuilt_pages"])
    builds = Table(
        "weather_export_prebuilt_builds",
        metadata,
        Column("build_id", String(36), primary_key=True),
        Column("profile_key", String(64), nullable=False),
        # builder가 발급(또는 재사용)한 handle. 그 manifest가 이 build의 정본이다.
        Column("snapshot_id", String(36), ForeignKey(
            "weather_export_snapshots.snapshot_id", ondelete="CASCADE",
        ), nullable=False, index=True),
        Column("manifest_digest", String(64), nullable=False),
        Column("page_limit", Integer, nullable=False),
        Column("proof_digest", String(64), nullable=False),
        Column("liveness_plans", BigInteger, nullable=False),
        Column("liveness_generations", BigInteger, nullable=False),
        Column("liveness_heads", BigInteger, nullable=False),
        Column("valid_until", DateTime(timezone=True), nullable=False),
        Column("status_stale", Boolean, nullable=False),
        Column("status_checked_at", DateTime(timezone=True), nullable=False),
        Column("page_count", Integer, nullable=False),
        Column("item_count", Integer, nullable=False),
        Column("byte_count", BigInteger, nullable=False),
        Column("built_at", DateTime(timezone=True), nullable=False),
        CheckConstraint("page_limit BETWEEN 1 AND 5000", name="ck_weather_prebuilt_limit"),
        CheckConstraint(
            f"page_count BETWEEN 1 AND {MAX_BUILD_PAGES}", name="ck_weather_prebuilt_pages",
        ),
        CheckConstraint(
            f"byte_count BETWEEN 0 AND {MAX_BUILD_BYTES}", name="ck_weather_prebuilt_bytes",
        ),
    )
    Index("ix_weather_export_prebuilt_builds_profile", builds.c.profile_key,
          builds.c.status_checked_at.desc())
    Index("ix_weather_export_prebuilt_builds_manifest", builds.c.manifest_digest,
          builds.c.page_limit)
    pages = Table(
        "weather_export_prebuilt_pages",
        metadata,
        Column("build_id", String(36), ForeignKey(
            "weather_export_prebuilt_builds.build_id", ondelete="CASCADE",
        ), primary_key=True),
        Column("start_g", Integer, primary_key=True),
        Column("start_p", Integer, primary_key=True),
        Column("end_g", Integer, nullable=False),
        Column("end_p", Integer, nullable=False),
        Column("has_more", Boolean, nullable=False),
        Column("item_count", Integer, nullable=False),
        Column("byte_count", Integer, nullable=False),
        Column("payload_digest", String(64), nullable=False),
        Column("payload", LargeBinary, nullable=False),
        CheckConstraint("item_count BETWEEN 0 AND 5000", name="ck_weather_prebuilt_page_items"),
        CheckConstraint(
            "byte_count BETWEEN 0 AND 4194304", name="ck_weather_prebuilt_page_bytes",
        ),
    )
    return builds, pages


def _tables(reader):
    return export_prebuilt_tables(reader.heads.metadata)


def profile_key(reader, limit: int) -> str:
    """resource·reader 선택(제공처·기대 원천)·limit·저장 형식이 같은 요청만 같은 profile이다."""
    from .export_heads import _digest

    return _digest([
        "current", PREBUILT_VERSION, reader.current_selection,
        sorted(reader.expected_sources), sorted(reader.optional_sources), limit,
    ])


def eligible(reader) -> bool:
    """사전 구성을 쓸 수 있는 reader. 승인 inventory·삭제 epoch가 있는 PostgreSQL current만이다."""
    from .export_snapshots import FrozenCurrentExports

    return (
        type(reader) is FrozenCurrentExports
        and reader.source_inventory and reader.page_liveness and reader.immutable_reads
        and reader.selection_cache is not None
        and reader.repository.engine.dialect.name == "postgresql"
    )


def _inventory_digest(connection, reader):
    """기대 원천의 승인 inventory 전체 행 digest(SQL 집계, 행 전송 없음)."""
    from .export_inventory import export_inventory_table

    table = export_inventory_table(reader.heads.metadata)
    row = cast(func.row(
        table.c.provider, table.c.dataset_key, table.c.location_id, table.c.source_part,
        table.c.namespace, table.c.generation_id, table.c.source_scope,
        table.c.latitude, table.c.longitude, table.c.nx, table.c.ny,
        extract("epoch", table.c.source_collected_at), extract("epoch", table.c.published_at),
    ), Text)
    count, digest = connection.execute(select(
        func.count(),
        func.md5(func.coalesce(func.string_agg(row, aggregate_order_by(
            literal("\n"), table.c.provider, table.c.dataset_key, table.c.location_id,
            table.c.source_part,
        )), "")),
    ).where(tuple_(table.c.provider, table.c.dataset_key).in_(
        sorted(reader.expected_sources),
    ))).one()
    return count, digest


def state_proof(connection, reader, *, locations=None, addresses=None):
    """caller RR의 manifest 입력 proof. 같은 proof면 같은 manifest다(module docstring).

    builder는 freeze가 이미 읽은 catalog·주소 행을 넘기고, 요청은 같은 조회(``_catalog_locations``·
    ``_address_entries``)로 다시 읽는다. 처리할 수 없으면 None이다.
    """
    from .export_heads import _digest
    from .export_page_liveness import capture_liveness
    from .export_snapshots import MAX_SNAPSHOT_LOCATIONS

    if connection.dialect.name != "postgresql" or not reader.source_inventory:
        return None
    liveness = capture_liveness(connection, reader.heads.metadata)
    if liveness is None:
        return None
    if locations is None:
        locations = reader._catalog_locations(connection)
    if not locations or len(locations) > MAX_SNAPSHOT_LOCATIONS:
        return None
    if addresses is None:
        addresses = reader._address_entries(connection, locations)
    return {
        "liveness": tuple(liveness),
        "digest": _digest([
            PREBUILT_VERSION,
            [dict(row) for row in locations],
            sorted([key, dict(value)] for key, value in addresses.items()),
            list(_inventory_digest(connection, reader)),
        ]),
    }


def _latest_build(connection, reader, key):
    builds, _ = _tables(reader)
    return connection.execute(
        select(builds).where(builds.c.profile_key == key)
        .order_by(builds.c.status_checked_at.desc()).limit(1)
    ).mappings().one_or_none()


def _build_liveness(build):
    return (build["liveness_plans"], build["liveness_generations"], build["liveness_heads"])


def _reusable_handle(connection, reader, build):
    """build manifest의 handle 중 freeze 재사용과 같은 조건을 만족하는 것(없으면 None).

    조건은 freeze와 같다: 같은 manifest digest의 유효한 handle 중 가장 최근 발급이고, 그 뒤에
    같은 selection으로 발급된 handle이 없어야 한다. build가 가리키는 handle이 만료된 뒤 앞 요청이
    같은 manifest로 새 handle을 발급했으면 그것을 이어 쓴다(요청마다 새로 발급하지 않는다).
    """
    snapshots = reader.snapshots
    latest = connection.execute(
        select(snapshots.c.snapshot_id, snapshots.c.issue_order).where(
            snapshots.c.manifest_digest == build["manifest_digest"],
            snapshots.c.expires_at > datetime.now(UTC),
        ).order_by(snapshots.c.issue_order.desc()).limit(1)
    ).one_or_none()
    if latest is None or connection.scalar(select(snapshots.c.issue_order).where(
        snapshots.c.issue_order > latest.issue_order, *reader._same_selection(),
    ).limit(1)) is not None:
        return None
    return reader._cursor_snapshot_row(connection, latest.snapshot_id)


def _carry_status(reader, old, new):
    """같은 manifest의 새 handle로 옮길 때 방금 만든 공용 상태 판정을 새 key에도 남긴다.

    판정은 manifest와 현재 상태의 함수다(handle id와 무관). 이어지는 cursor 페이지가 새 handle
    key로 다시 판정하지 않게 한다.
    """
    cache = reader._state_proof_cache()
    if cache is None:
        return
    prefix = (id(reader.repository.engine), reader._state_signature())
    with cache.lock:
        entry = cache.status_proofs.get((*prefix, old["snapshot_id"], old["manifest_digest"]))
    if entry is not None:
        cache.put_status((*prefix, new["snapshot_id"], new["manifest_digest"]), *entry)


def prebuilt_first_page(reader, limit):
    """사전 구성된 첫 페이지의 handle·상태를 돌려준다. 쓸 수 없으면 None(기존 freeze 경로).

    결과는 ``(frozen, status)``이고 본문은 ``prebuilt_page``가 같은 manifest로 찾는다.
    ``reader.prebuilt_outcome``에 판정 이유를 남긴다(응답 header ``x-export-prebuilt``).
    """
    from .export_snapshots import SNAPSHOT_TTL, STATUS_PROOF_MIN_REMAINING

    reader.prebuilt_outcome = "off"
    if not eligible(reader):
        return None
    key = profile_key(reader, limit)
    snapshots = reader.snapshots
    try:
        with reader.repository.engine.connect().execution_options(
            isolation_level="REPEATABLE READ",
        ) as connection, connection.begin():
            connection.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
            build = _latest_build(connection, reader, key)
            if build is None:
                reader.prebuilt_outcome = "none"
                return None
            proof = state_proof(connection, reader)
            now = datetime.now(UTC)
            if (proof is None or proof["digest"] != build["proof_digest"]
                    or proof["liveness"] != _build_liveness(build)):
                reader.prebuilt_outcome = "changed"
                return None
            if now >= build["valid_until"]:
                reader.prebuilt_outcome = "expired"
                return None
            # build handle이 만료됐어도 앞 요청이 같은 manifest로 발급한 유효 handle이 있으면 쓴다.
            frozen = _reusable_handle(connection, reader, build)
            if frozen is None:
                frozen = reader._cursor_snapshot_row(connection, build["snapshot_id"])
            if frozen is None:
                # 만료된 handle도 manifest는 그대로다. 새 handle 발급에만 쓴다(연장하지 않는다).
                row = connection.execute(select(snapshots).where(
                    snapshots.c.snapshot_id == build["snapshot_id"],
                )).mappings().one_or_none()
                frozen = dict(row) if row is not None else None
            if frozen is None or frozen["manifest_digest"] != build["manifest_digest"]:
                reader.prebuilt_outcome = "missing"
                return None
    except ExportGuardTimeout:
        raise
    except (SQLAlchemyError, ExportArtifactError) as exc:
        # 판정을 못 했을 뿐 사전 구성이 틀린 것은 아니다. 기존 경로가 같은 오류를 분류한다.
        # 삭제 epoch 조회 실패는 capture_liveness가 ExportArtifactError로 바꿔 올린다.
        logger.warning("weather_export_prebuilt_check_failed reason=%s", type(exc).__name__)
        reader.prebuilt_outcome = "error"
        return None
    token = proof["liveness"]
    frozen = dict(frozen)
    refs = [ref for scope in frozen["manifest"]["scopes"] for ref in scope["refs"]]
    # 검산을 모두 통과한 뒤에만 새 handle을 발급한다(버려질 handle·ref를 남기지 않는다).
    # 발급 전 이 파일 검산은 기존 재사용 handle과 같다: 같은 proof라 승인 inventory가 build 때와
    # 같고, inventory가 참조하는 세대는 GC가 지킨다.
    if not reader.artifacts.all_present(refs):
        # 없으면 기존 경로가 새 head로 다시 발급한다.
        reader.prebuilt_outcome = "artifacts"
        return None
    if frozen["expires_at"] > datetime.now(UTC):
        frozen = reader._renew_reused(dict(frozen, _reused=True))
    # 갱신 실패(best-effort)·총 수명 상한·이미 만료: 판정 뒤 같은 manifest로 새 handle을 발급한다.
    needs_handle = frozen["expires_at"] - datetime.now(UTC) < SNAPSHOT_TTL
    remaining = (build["status_checked_at"] + STATUS_MAX_AGE - datetime.now(UTC)).total_seconds()
    builder_status = remaining > STATUS_PROOF_MIN_REMAINING
    reader.__dict__.pop("_state_selection_parity", None)
    if builder_status:
        # builder가 manifest와 같은 RR에서 판정했다. 승인 수명 남은 만큼만 이어 쓴다.
        # 완료 페이지 재사용 검산은 proof RR의 삭제 epoch와 비교한다(새 판정 경로는 그 RR 값).
        if reader.reuse_liveness is None:
            reader.reuse_liveness = token
        cache = reader._state_proof_cache()
        reader._freeze_status = (
            frozen["snapshot_id"], frozen["manifest_digest"], build["status_stale"],
            cache.clock() + remaining, token,
        )
    status = reader._page_status(frozen, frozen["manifest"], reuse=False)
    if builder_status:
        reader.page_status_proof = "prebuilt"
    else:
        parity = reader.__dict__.pop("_state_selection_parity", None)
        if parity != (bool(frozen["manifest"].get("unconfirmed_sources")), True):
            # 지금 승인 선택이 manifest와 다르다. freeze가 새 manifest를 만들게 한다.
            reader.prebuilt_outcome = "selection"
            return None
    if needs_handle:
        try:
            with reader.heads._transaction() as session:
                issued = reader._copy_handle(session, frozen)
        except SQLAlchemyError as exc:
            # 잠금 대기·세대 삭제 FK 등. 사전 구성을 버리고 기존 freeze가 같은 오류를 분류한다.
            logger.warning("weather_export_prebuilt_handle_failed reason=%s", type(exc).__name__)
            reader.prebuilt_outcome = "error"
            return None
        _carry_status(reader, frozen, issued)
        frozen = issued
    reader.prebuilt_outcome = "hit"
    return frozen, status


def _covered_refs(manifest, start_g, end_g, end_p):
    scopes = manifest["scopes"]
    last = min(len(scopes), end_g + (1 if end_p > 0 else 0))
    return [
        (ref["artifact_digest"], ref["byte_count"], ref["point_count"])
        for scope in scopes[start_g:last] for ref in scope["refs"]
    ]


def prebuilt_page(reader, frozen, state, limit):
    """같은 manifest·limit·시작 위치의 사전 구성 본문. 없거나 검산 실패면 None(기존 조립)."""
    if not getattr(reader, "prebuilt", False) or not eligible(reader):
        return None
    builds, pages = _tables(reader)
    try:
        with reader.repository.engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
            row = connection.execute(
                select(pages).join(builds, builds.c.build_id == pages.c.build_id).where(
                    builds.c.profile_key == profile_key(reader, limit),
                    builds.c.manifest_digest == frozen["manifest_digest"],
                    builds.c.page_limit == limit,
                    pages.c.start_g == state["g"], pages.c.start_p == state["p"],
                ).order_by(builds.c.status_checked_at.desc()).limit(1)
            ).mappings().one_or_none()
    except SQLAlchemyError as exc:
        logger.warning("weather_export_prebuilt_page_failed reason=%s", type(exc).__name__)
        return None
    if row is None:
        return None
    payload = bytes(row["payload"])
    try:
        if (len(payload) != row["byte_count"]
                or hashlib.sha256(payload).hexdigest() != row["payload_digest"]):
            raise ValueError("digest")
        items = json.loads(payload)
        if not isinstance(items, list) or len(items) != row["item_count"] or (
            (row["end_g"], row["end_p"]) <= (row["start_g"], row["start_p"])
        ):
            raise ValueError("shape")
    except ValueError as exc:
        # 저장 본문 손상은 숨기지 않고 남긴 뒤 원본 artifact에서 다시 조립한다.
        logger.warning("weather_export_prebuilt_page_invalid reason=%s", exc)
        return None
    refs = _covered_refs(frozen["manifest"], row["start_g"], row["end_g"], row["end_p"])
    # 본문이 읽었던 artifact의 존재·크기를 기존 재사용처럼 매번 확인한다(누락을 숨기지 않는다).
    present_many = getattr(reader.artifacts, "present_many", None)
    payload_refs = [{"artifact_digest": d, "byte_count": b} for d, b, _ in refs]
    if callable(present_many):
        present_many(payload_refs)
    else:
        for ref in payload_refs:
            reader.artifacts.present(ref["artifact_digest"], byte_count=ref["byte_count"])
    reader.reuse_artifacts.update(refs)
    state["g"], state["p"] = row["end_g"], row["end_p"]
    reader.prebuilt_pages = getattr(reader, "prebuilt_pages", 0) + 1
    return items, bool(row["has_more"])


def prebuild_enabled(settings) -> bool:
    root = settings.service_export_artifact_root
    return bool(
        settings.service_exports_enabled and settings.service_export_prebuild_enabled
        and root is not None
    )


def _builder_reader(repository, settings):
    from .export_database_artifacts import export_artifact_store
    from .export_selection_cache import ExportSelectionCache
    from .export_snapshots import FrozenCurrentExports
    from .export_source_registry import select_export_sources

    selection = select_export_sources(settings.enabled_providers, resource="current")
    return FrozenCurrentExports(
        repository, export_artifact_store(repository, settings.service_export_artifact_root),
        # cursor 서명은 요청 경로가 자기 token으로 한다. builder는 본문만 만든다.
        signing_key="prebuild-cursor-unused",
        providers=selection.providers, source_inventory=True,
        expected_sources=selection.expected, optional_sources=selection.optional,
        selection_cache=ExportSelectionCache(), immutable_reads=True, page_liveness=True,
    )


def _unchanged(repository, reader, key) -> bool:
    """마지막 build의 proof·승인 선택이 지금과 같고 시간 경계·handle이 남아 있으면 그대로 둔다.

    handle 수명은 보지 않는다. 만료됐으면 첫 요청이 같은 manifest로 새 handle을 한 번 발급하고
    다음 요청부터는 그 handle을 이어 쓴다(``_reusable_handle``). 시도·run 실패와 복구처럼
    manifest의 미확인 여부만 바꾸는 변화는 proof에 없으므로, 요청 경로가 builder 판정 수명 뒤에
    하는 것과 같은 승인 선택 비교(``_state_selection_parity``)를 여기서도 한다. 다르면 다시 만든다.
    """
    from .export_selection_cache import ExportSelectionCache

    with repository.engine.connect().execution_options(
        isolation_level="REPEATABLE READ",
    ) as connection, connection.begin():
        connection.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
        build = _latest_build(connection, reader, key)
        if build is None or build["valid_until"] <= datetime.now(UTC) + VALID_UNTIL_MARGIN:
            return False
        proof = state_proof(connection, reader)
        if (proof is None or proof["digest"] != build["proof_digest"]
                or proof["liveness"] != _build_liveness(build)):
            return False
        manifest = connection.scalar(select(reader.snapshots.c.manifest).where(
            reader.snapshots.c.snapshot_id == build["snapshot_id"],
        ))
        if manifest is None:
            return False
        reader.selection_cache = ExportSelectionCache()
        reader.selection_min_remaining = None
        reader.selection_deadline = None
        reader.reuse_liveness = None
        reader.selection_checked_after = monotonic()
        reader.__dict__.pop("_state_selection_parity", None)
        reader._compute_state_stale(connection, manifest)
        reader.__dict__.pop("_selection_read_in", None)
        parity = reader.__dict__.pop("_state_selection_parity", None)
        return parity == (bool(manifest.get("unconfirmed_sources")), True)


def build_current_profile(
    repository, settings, *, limit: int = PROFILE_LIMIT,
    budget_seconds: float = BUILD_BUDGET_SECONDS, clock=monotonic,
) -> dict[str, Any]:
    """W2 표준 프로필 하나를 만든다. 입력이 그대로면 아무 것도 쓰지 않는다(멱등).

    결과 ``outcome``: ``built``·``unchanged``·``disabled``·``unsupported``·``not_ready``
    (승인 선택이 아직 없거나 상한 초과로 freeze가 이전 승인 fallback을 써야 하는 상태)·
    ``over_budget``·``capacity``. 저장은 build 행과 모든 페이지를 한 transaction에 넣는다.
    """
    from .export_capacity import ExportCapacityExceeded
    from .export_selection_cache import ExportSelectionCache
    from .export_snapshots import ExportSnapshotUnavailable, ensure_snapshot_files

    started = clock()
    report: dict[str, Any] = {"limit": limit}
    if not prebuild_enabled(settings):
        return {**report, "outcome": "disabled"}
    if repository.engine.dialect.name != "postgresql":
        return {**report, "outcome": "unsupported"}
    reader = _builder_reader(repository, settings)
    key = profile_key(reader, limit)
    report["profile_key"] = key
    if _unchanged(repository, reader, key):
        return {**report, "outcome": "unchanged", "seconds": round(clock() - started, 3)}
    reader._prebuild_capture = True
    reader._capture_forecast_health = True
    checked_at = datetime.now(UTC)

    def issue():
        # 시도마다 새 승인 선택을 manifest·proof와 같은 RR에서 읽는다. 앞 시도의 선택을
        # 이어 쓰면 proof(이번 RR)와 manifest(앞 RR)가 어긋날 수 있다.
        nonlocal checked_at
        reader.selection_cache = ExportSelectionCache()
        reader.selection_min_remaining = None
        reader.selection_deadline = None
        reader.reuse_liveness = None
        reader.selection_checked_after = monotonic()
        for name in ("_prebuild_proof", "_fresh_forecast_health", "_freeze_status"):
            reader.__dict__.pop(name, None)
        checked_at = datetime.now(UTC)
        return reader._freeze()

    try:
        frozen = ensure_snapshot_files(reader.artifacts, issue)
    except (ExportSnapshotUnavailable, ExportCapacityExceeded):
        return {**report, "outcome": "not_ready", "seconds": round(clock() - started, 3)}
    proof = reader.__dict__.pop("_prebuild_proof", None)
    health = getattr(reader, "_fresh_forecast_health", None)
    if proof is None or health is None or health[1] is None:
        return {**report, "outcome": "not_ready", "seconds": round(clock() - started, 3)}
    frozen = reader._renew_reused(frozen)
    manifest = frozen["manifest"]
    # freeze RR의 판정을 그대로 쓴다(pending). 시각에 따른 부분은 요청이 다시 본다.
    stale = reader._current_state_stale(frozen, manifest, reuse=False)
    state, rows, total, items_total = {"g": 0, "p": 0}, [], 0, 0
    while True:
        if clock() - started > budget_seconds:
            return {**report, "outcome": "over_budget", "pages": len(rows)}
        start = (state["g"], state["p"])
        items, more = reader._current_items(manifest["scopes"], state, limit)
        payload = json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode()
        rows.append({
            "start_g": start[0], "start_p": start[1], "end_g": state["g"], "end_p": state["p"],
            "has_more": more, "item_count": len(items), "byte_count": len(payload),
            "payload_digest": hashlib.sha256(payload).hexdigest(), "payload": payload,
        })
        total += len(payload)
        items_total += len(items)
        if total > MAX_BUILD_BYTES or len(rows) > MAX_BUILD_PAGES:
            return {**report, "outcome": "capacity", "pages": len(rows), "bytes": total}
        if not more:
            break
        if (state["g"], state["p"]) <= start:
            raise ExportArtifactError("사전 구성 페이지 위치가 앞으로 가지 않습니다.")
    build_id = str(uuid4())
    builds, pages = _tables(reader)
    with repository.engine.connect() as connection, connection.begin():
        connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
        connection.exec_driver_sql("SET LOCAL lock_timeout = '1s'")
        connection.execute(builds.insert().values(
            build_id=build_id, profile_key=key, snapshot_id=frozen["snapshot_id"],
            manifest_digest=frozen["manifest_digest"], page_limit=limit,
            proof_digest=proof["digest"], liveness_plans=proof["liveness"][0],
            liveness_generations=proof["liveness"][1], liveness_heads=proof["liveness"][2],
            valid_until=health[1], status_stale=bool(stale), status_checked_at=checked_at,
            page_count=len(rows), item_count=items_total, byte_count=total,
            built_at=datetime.now(UTC),
        ))
        for row in rows:
            connection.execute(pages.insert().values(build_id=build_id, **row))
        # 늦게 끝난 builder가 더 새 build를 지우지 않도록 판정 시각 순으로 오래된 것만 지운다.
        keep = select(builds.c.build_id).where(builds.c.profile_key == key).order_by(
            builds.c.status_checked_at.desc(),
        ).limit(KEEP_BUILDS)
        removed = connection.execute(delete(builds).where(
            builds.c.profile_key == key, builds.c.build_id.not_in(keep),
        )).rowcount
    return {
        **report, "outcome": "built", "build_id": build_id,
        "snapshot_id": frozen["snapshot_id"], "pages": len(rows), "items": items_total,
        "bytes": total, "status": "stale" if stale else "ok", "removed_builds": removed,
        "valid_until": health[1].isoformat(),
        # 시간 경계(가장 이른 health_recheck_at)까지 남은 초. 여러 provider가 순환 수집하는 운영에서
        # 이 값이 Map 도착(:25·:40)보다 짧으면 요청이 ``expired``로 기존 경로를 쓴다.
        "valid_for_seconds": round((health[1] - checked_at).total_seconds(), 1),
        "seconds": round(clock() - started, 3),
    }


def main(argv: list[str] | None = None) -> int:
    """배포 hook·수동 실행용. 결과 JSON 한 줄을 내고 정상 결과가 아니면 1을 돌려준다(배포는 경고만).

    정상 결과는 Dagster job과 같은 ``SUCCESS_OUTCOMES``다(``not_ready``도 정상 상태다).
    """
    from .repository import WeatherRepository
    from .settings import get_settings

    parser = argparse.ArgumentParser(description="Map 표준 프로필 export 페이지를 사전 구성한다.")
    parser.add_argument("--limit", type=int, default=PROFILE_LIMIT)
    # 페이지 조립 사이에서만 확인하는 예산이다. 배포 hook은 프로세스 자체도 timeout으로 끊는다.
    parser.add_argument("--budget-seconds", type=float, default=BUILD_BUDGET_SECONDS)
    args = parser.parse_args(argv)
    if not 0 < args.budget_seconds <= BUILD_BUDGET_SECONDS:
        parser.error(f"--budget-seconds는 0 초과 {BUILD_BUDGET_SECONDS:g} 이하여야 합니다.")
    settings = get_settings()
    repository = WeatherRepository(settings.database_url)
    try:
        report = build_current_profile(
            repository, settings, limit=args.limit, budget_seconds=args.budget_seconds,
        )
    except (ExportArtifactError, SQLAlchemyError, OSError) as exc:
        from .export_collector import failure_reason

        reason = failure_reason(exc) if isinstance(exc, SQLAlchemyError) else type(exc).__name__
        print(json.dumps({"outcome": "failed", "reason": reason}, ensure_ascii=False))
        return 1
    finally:
        repository.engine.dispose()
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0 if report["outcome"] in SUCCESS_OUTCOMES else 1


if __name__ == "__main__":
    sys.exit(main())
