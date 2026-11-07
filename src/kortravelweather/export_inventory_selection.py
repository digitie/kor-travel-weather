"""실제 승인 위치와 최신 HTTP 전 선택 집합을 같은 MVCC에서 함께 읽는다."""

import hashlib
import threading
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, true, tuple_

from .export_artifacts import ExportArtifactError
from .export_capacity import (
    MAX_MANIFEST_BYTES,
    MAX_SOURCE_SCOPES,
    ExportCapacityExceeded,
    MetadataBudget,
    any_of,
    read_scope_plans,
)
from .export_external import RetrievalScopeDescriptor
from .export_external_cohorts import external_cohort_table
from .export_heads import ScopeDescriptor, parse_scope_descriptor
from .export_inventory import export_inventory_table
from .export_mid import MidScopeDescriptor
from .export_sdk import SdkScopeDescriptor

MAX_SELECTED_ROWS = 20000
SDK_NETWORKS = {
    "krforest_mountain_weather": "산악기상관측망", "krforest_dust": "청정넷(AICAN)",
    "krex_restarea_weather": "고속도로 휴게소 기상관측", "khoa_beach_index": "해수욕장 해양지수",
}


#: catalog anchor 투영의 관측점 allowlist와 각 문자열 상한.
_POINT_FIELDS = (
    ("provider", 120), ("station_id", 120), ("station_name", 200),
    ("sido_name", 32), ("network", 120),
)


def catalog_source_projection(catalog):
    """SDK anchor allowlist 투영의 ``(FROM, 열)``. 임의 metadata 전체는 읽지 않는다.

    ``metadata``는 json이라 경로 연산자마다 원문 전체를 다시 해석한다. 예전 7열은 행마다 약
    14번 해석했고, 1,451곳 catalog 조회가 포화된 odroid에서 0.7~1.1초였다(2026-11-02 실측).
    LATERAL에서 관측점 객체와 중기 코드 셋만 한 번씩 꺼낸다(원문 해석 4번). 관측점 다섯 열은
    작은 관측점 json에서 꺼낸다. ``OFFSET 0``은 planner가 subquery를 바깥 열마다 펼치는 것을
    막는다. json 연산 그대로라 값·오류 동작(전체 문서를 jsonb로 바꾸지 않음)은 예전과 같다.
    호출마다 새 LATERAL을 만든다. FROM과 열이 같은 객체를 쓰므로 한 SELECT에서 함께 쓴다.
    """
    metadata = catalog.c.metadata
    lateral = (
        select(
            metadata["measurement_point"].label("point"),
            metadata["mid_land_region_code"].as_string().label("mid_land_region_code"),
            metadata["mid_temperature_region_code"].as_string().label(
                "mid_temperature_region_code"
            ),
            metadata["mid_region_code"].as_string().label("mid_region_code"),
        )
        .correlate(catalog)
        .offset(0)
        .lateral("catalog_source_metadata")
    )
    columns = [
        *[
            func.left(func.coalesce(
                lateral.c[f"mid_{part}_region_code"], lateral.c.mid_region_code,
            ), 33).label(f"mid_{part}_region")
            for part in ("land", "temperature")
        ],
        *[
            func.left(lateral.c.point[key].as_string(), maximum + 1).label("point_" + key)
            for key, maximum in _POINT_FIELDS
        ],
    ]
    return catalog.join(lateral, true()), columns


def source_max_age(dataset):
    if dataset in SDK_NETWORKS:
        return timedelta(hours=36)
    if dataset == "kma_mid_forecast":
        return timedelta(hours=18)
    if dataset == "kma_short_forecast" or dataset.endswith("_forecast"):
        return timedelta(hours=6)
    return timedelta(hours=3)


#: 승인 만료 = max(하한, 배수 × 신선도 상한). 신선도 상한을 넘으면 stale, 만료를 넘으면 그 원천이
#: 더 이상 그 위치를 내지 않는 것으로 보고 export에서 뺀다(2026-11-03: 응답에서 빠진 지점의 6일 전
#: 값이 "현재값"으로 나가며 전체 meta를 10-26으로 끌어내렸다). KMA 회전 slot은 수 시간 안에
#: 갱신된다.
SOURCE_EXPIRY_FLOOR = timedelta(hours=24)
SOURCE_EXPIRY_FACTOR = 2


#: 신선도(meta.source_collected_at·stale)에 세는 승인 나이 상한. 이보다 오래됐지만 만료 전인 승인은
#: 값은 내되 신선도·미확인 판정에서 뺀다(소유자 2026-11-07: 응답에서 빠진 산악기상 몇 곳이 SDK 만료
#: 72시간 동안 current·daily meta를 붙잡았다). 만료 하한과 같은 값에서 파생한다.
FRESHNESS_WINDOW = SOURCE_EXPIRY_FLOOR
#: 그래서 신선도 창 안 승인이 하나도 없는 원천은 stale이다 — SDK(신선도 상한 36시간)도 통째로 24시간
#: 침묵하면 stale이 된다(실효 상한 = min(source_max_age, FRESHNESS_WINDOW)).


def counts_for_freshness(collected_at, now):
    """이 승인이 신선도(meta·stale)에 들어가는가. 값 제공 여부(만료)와는 별개다."""
    return now - collected_at <= FRESHNESS_WINDOW


def freshness_min(values, now):
    """신선도에 드는 값의 최솟값. 하나도 없으면 전체 최솟값(그때는 나이 검사가 stale로 만든다)."""
    values = [value for value in values if value is not None]
    fresh = [value for value in values if counts_for_freshness(value, now)]
    return min(fresh or values, default=None)


def source_expiry(dataset):
    """이 나이를 넘은 위치별 승인은 값·meta·건강도 어디에도 쓰지 않는다(coverage 공백).

    나이는 ``source_collected_at``(원천이 이 위치를 마지막으로 준 때)로만 잰다. 원천이 계속
    주되 관측 시각(``data_revision_at``)이 멈춘 경우는 공백이 아니라 원천의 stale 값이므로
    그대로 내보내고 stale로 표시한다. 요청한 원천이 전부 만료면 낼 것이 없어 reader가 이전
    snapshot으로 fallback한다(단일 원천 export는 옛 값을 stale로 계속 낸다 — 만료 전과 같되
    정직하게 표시).
    """
    return max(SOURCE_EXPIRY_FLOOR, SOURCE_EXPIRY_FACTOR * source_max_age(dataset))


def _validate_anchor(descriptor, current):
    if isinstance(descriptor, ScopeDescriptor):
        if (
            descriptor.normalizer_revision != "kma-v1"
            or descriptor.winner_policy_revision != "kma-base-v1"
        ):
            raise ExportArtifactError("승인 KMA 정규화 정책이 현재 reader와 다릅니다.")
    elif isinstance(descriptor, MidScopeDescriptor):
        if current.get(f"mid_{descriptor.source_part}_region") != descriptor.region_code:
            raise ExportArtifactError("승인 중기 지역 코드가 바뀌었습니다.")
    elif isinstance(descriptor, SdkScopeDescriptor):
        station = current.get("point_station_id")
        if descriptor.provider == "python-airkorea-api" and not station:
            station = current["location_id"]
        if (
            current.get("point_provider") != descriptor.provider
            or current.get("point_station_name") != descriptor.station_name
            or station != descriptor.station_id
            or current.get("point_sido_name") != descriptor.sido_name
            or (
                descriptor.dataset_key in SDK_NETWORKS
                and current.get("point_network") != SDK_NETWORKS[descriptor.dataset_key]
            )
        ):
            raise ExportArtifactError("승인 SDK anchor 코드/이름/원천 문맥이 바뀌었습니다.")


@dataclass(frozen=True)
class ApprovedScope:
    descriptor: ScopeDescriptor | RetrievalScopeDescriptor | SdkScopeDescriptor
    generation_id: str
    location_ids: tuple[str, ...]
    source_collected_at: datetime
    data_revision_at: datetime
    published_at: datetime


@dataclass(frozen=True)
class ApprovedSelection:
    scopes: tuple[ApprovedScope, ...]
    unconfirmed: bool
    #: 만료로 뺀 (provider, dataset_key, location_id). 미확인이 아니라 coverage 공백이다.
    expired: frozenset[tuple[str, str, str]] = frozenset()


#: attempt별 불변 plan의 해석 결과. descriptor 열은 insert 뒤 바뀌지 않으므로(export_heads는
#: revision·digest 열만 갱신) attempt_id로 기억해도 된다. 2026-11-02 odroid 실측: 한 freeze가
#: plan 3,307개를 서버 cursor로 읽고 해석하는 데 6~8초였다. 기억한 plan은 존재만 다시 확인한다
#: (유실 탐지 유지).
_PLAN_CACHE_SIZE = 16_384
_PLAN_CACHE_BYTES = 64 * 1024 * 1024
#: attempt_id -> (해석된 descriptor, 정규 JSON 바이트 수). 바이트로도 상한을 둔다.
_parsed_plans: OrderedDict[str, tuple[object, int]] = OrderedDict()
_parsed_plans_bytes = 0
_parsed_plans_lock = threading.Lock()


def _remembered_plans(attempt_ids):
    with _parsed_plans_lock:
        found = {}
        for key in attempt_ids:
            value = _parsed_plans.get(key)
            if value is not None:
                _parsed_plans.move_to_end(key)
                found[key] = value
        return found


def _remember_plans(parsed):
    global _parsed_plans_bytes
    with _parsed_plans_lock:
        for key, value in parsed.items():
            previous = _parsed_plans.pop(key, None)
            if previous is not None:
                _parsed_plans_bytes -= previous[1]
            _parsed_plans[key] = value
            _parsed_plans_bytes += value[1]
        while _parsed_plans and (
            len(_parsed_plans) > _PLAN_CACHE_SIZE or _parsed_plans_bytes > _PLAN_CACHE_BYTES
        ):
            _parsed_plans_bytes -= _parsed_plans.popitem(last=False)[1][1]


def parsed_scope_plans(connection, plans, attempt_ids):
    """attempt별 해석된 plan. 기억한 것은 행 존재만, 나머지는 읽어서 해석하고 기억한다.

    선택 전체의 누적 메타데이터 예산(``MAX_MANIFEST_BYTES``)은 기억 여부와 무관하게 같은 바이트로
    다시 센다 — 결과가 프로세스 이력에 따라 달라지지 않는다.
    """
    attempt_ids = tuple(dict.fromkeys(attempt_ids))
    if len(attempt_ids) > MAX_SOURCE_SCOPES:
        raise ExportCapacityExceeded("승인 source plan 수가 상한을 넘었습니다.")
    remembered = _remembered_plans(attempt_ids)
    if remembered:
        present = set(connection.scalars(select(plans.c.attempt_id).where(
            any_of(plans.c.attempt_id, sorted(remembered)),
        )))
        if present != remembered.keys():
            raise ExportCapacityExceeded("승인 source의 불변 plan이 유실됐습니다.")
    missing = [key for key in attempt_ids if key not in remembered]
    # 개별/누적 거부 검사에서 이미 계산한 정규 UTF-8 크기를 cache 예산에도 쓴다.
    # 같은 descriptor를 다시 JSON으로 만들지 않으며 cache hit/miss의 전체 상한은 같다.
    byte_sizes = {}
    fresh = {
        key: (parse_scope_descriptor(value), byte_sizes[key])
        for key, value in read_scope_plans(
            connection, plans, missing, byte_sizes=byte_sizes,
        ).items()
    }
    if sum(size for _, size in (*remembered.values(), *fresh.values())) > MAX_MANIFEST_BYTES:
        raise ExportCapacityExceeded("승인 manifest 메타데이터 예산을 넘었습니다.")
    _remember_plans(fresh)
    return {key: value[0] for key, value in {**remembered, **fresh}.items()}


_cohort_cache = OrderedDict()
_cohort_cache_bytes = 0
_cohort_cache_lock = threading.Lock()
_COHORT_CACHE_BYTES = 16 * 1024 * 1024
_COHORT_CACHE_ENTRIES = 256


def _cohort_descriptors(row):
    """불변 descriptor 해석만 기억한다. roster 내용·존재·run 상태는 매번 실제로 읽는다."""
    global _cohort_cache_bytes
    payload = MetadataBudget().encoder.encode([
        row["provider"], row["dataset_key"], row["descriptors"], row["targets"],
    ]).encode("utf-8")
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ExportCapacityExceeded("실제 source roster 메타데이터 예산을 넘었습니다.")
    key = hashlib.sha256(payload).digest()
    with _cohort_cache_lock:
        remembered = _cohort_cache.get(key)
        if remembered is not None:
            _cohort_cache.move_to_end(key)
            return list(remembered[0])
    descriptors = _parse_cohort_descriptors(row)
    with _cohort_cache_lock:
        previous = _cohort_cache.pop(key, None)
        if previous is not None:
            _cohort_cache_bytes -= previous[1]
        _cohort_cache[key] = (tuple(descriptors), len(payload))
        _cohort_cache_bytes += len(payload)
        while _cohort_cache and (
            len(_cohort_cache) > _COHORT_CACHE_ENTRIES
            or _cohort_cache_bytes > _COHORT_CACHE_BYTES
        ):
            _cohort_cache_bytes -= _cohort_cache.popitem(last=False)[1][1]
    return descriptors


def _parse_cohort_descriptors(row):
    if row["descriptors"] is not None:
        if not isinstance(row["descriptors"], list) or not 1 <= len(row["descriptors"]) <= 5000:
            raise ExportArtifactError("실제 source roster 상한이 올바르지 않습니다.")
        return [parse_scope_descriptor(item) for item in row["descriptors"]]
    # 0031 기본3 roster와 호환한다. 그 밖의 source는 명시 descriptor가 필요하다.
    return [RetrievalScopeDescriptor(
        provider=row["provider"], dataset_key=row["dataset_key"], entity_id=item["location_id"],
        location_ids=(item["location_id"],), latitude=item["latitude"], longitude=item["longitude"],
        forecast_style="short" if row["dataset_key"].endswith("forecast") else "observed",
        normalizer_revision=f"{row['provider']}-v1",
    ) for item in row["targets"]]


def _active_scope_states(connection, metadata, source_scopes):
    """같은 MVCC의 소유·시도·공개 증거·run 상태를 시도별 한 행으로 읽는다.

    owner_epochs 원본 JSON은 PostgreSQL jsonb로 강제 변환하지 않는다. 예를 들어 1e0은
    Python에서 1.0이며 epoch 1과 다르다. 기존 str/32자 판정을 그대로 적용한다.
    MATERIALIZED는 여러 scope가 공유하는 시도·run을 scope마다 다시 찾는 것을 막는다.
    """
    if not source_scopes:
        return []
    owners = metadata.tables["weather_export_scope_owners"]
    attempts = metadata.tables["weather_export_attempts"]
    runs = metadata.tables["weather_sync_runs"]
    generations = metadata.tables["weather_export_generations"]
    selected_owners = select(
        owners.c.active_attempt_id,
        func.json_agg(func.json_build_array(
            owners.c.source_scope, owners.c.owner_epoch,
        )).label("owner_rows"),
    ).where(any_of(owners.c.source_scope, sorted(source_scopes))).group_by(
        owners.c.active_attempt_id,
    ).cte("selected_owners").prefix_with("MATERIALIZED")
    selected_attempts = select(
        attempts.c.attempt_id, attempts.c.run_id, attempts.c.status, attempts.c.owner_epochs,
    ).where(attempts.c.attempt_id.in_(
        select(selected_owners.c.active_attempt_id),
    )).cte("selected_attempts").prefix_with("MATERIALIZED")
    selected_runs = select(runs.c.run_id, runs.c.status).where(runs.c.run_id.in_(
        select(selected_attempts.c.run_id).distinct(),
    )).cte("selected_run_states").prefix_with("MATERIALIZED")
    publications = select(generations.c.attempt_id, generations.c.generation_id).where(
        generations.c.attempt_id.in_(select(selected_attempts.c.attempt_id)),
    ).cte("selected_publications").prefix_with("MATERIALIZED")
    statement = select(
        selected_attempts.c.attempt_id, selected_attempts.c.run_id,
        selected_attempts.c.status, publications.c.generation_id,
        selected_runs.c.status.label("native_status"), selected_attempts.c.owner_epochs,
        selected_owners.c.owner_rows,
    ).select_from(selected_owners.join(
        selected_attempts, selected_attempts.c.attempt_id == selected_owners.c.active_attempt_id,
    ).outerjoin(
        publications, publications.c.attempt_id == selected_attempts.c.attempt_id,
    ).outerjoin(selected_runs, selected_runs.c.run_id == selected_attempts.c.run_id))
    active = []
    for attempt_id, run_id, status, generation, native_status, epochs, claims in (
        connection.execute(statement)
    ):
        for scope, epoch in claims:
            claimed = epochs.get(scope) if isinstance(epochs, dict) else None
            active.append((
                scope, attempt_id, run_id, status, generation, native_status,
                (str(claimed)[:32] if claimed is not None else None) == str(epoch),
            ))
    return active


def approved_source_selection(
    connection, metadata, locations, *, expected, optional=(), health_location_ids=None,
):
    """caller가 RR/5s 상한을 소유한다. 이력/fact/SDK/파일/네트워크를 조회하지 않는다."""
    expected, optional = set(expected), set(optional)
    health_keys = None if health_location_ids is None else set(health_location_ids)
    if not expected or len(expected) > 128 or not optional.issubset(expected):
        raise ExportArtifactError("요청 source inventory 범위가 올바르지 않습니다.")
    table = export_inventory_table(metadata)
    generations, plans = metadata.tables["weather_export_generations"], metadata.tables[
        "weather_export_scope_plans"
    ]
    attempts, runs = (
        metadata.tables["weather_export_attempts"], metadata.tables["weather_sync_runs"]
    )
    # 위치마다 attempt·plan을 PK로 두 번 더 찾던 join은 부하 때 5s 상한을 넘었다.
    # plan은 attempt마다 한 번(read_scope_plans), run_id는 cohort 대조에 필요한 것만 읽는다.
    # generation은 attempt 이력 정리와 독립인 공개 성공 증거다(FK 없음).
    # inventory→generation JOIN은 수천 위치에서 동일 세대를 다시 찾아 운영5초 상한을 넘었다.
    # 작은 inventory를 먼저 제한하고 서로 다른 generation만 배열 한 번으로 읽는다.
    # 두 조회는 caller의 같은 RR 안에 있으며 generation의 승인 clock/plan을 그대로 사용한다.
    rows = [dict(row) for row in connection.execute(select(table).where(
        tuple_(table.c.provider, table.c.dataset_key).in_(expected),
    ).order_by(
        table.c.provider, table.c.dataset_key, table.c.location_id,
    ).limit(MAX_SELECTED_ROWS + 1)).mappings()]
    if len(rows) > MAX_SELECTED_ROWS:
        raise ExportArtifactError("승인 source inventory metadata 상한을 넘었습니다.")
    generation_ids = sorted({row["generation_id"] for row in rows})
    approved = {
        row["generation_id"]: row
        for row in connection.execute(select(
            generations.c.generation_id,
            generations.c.attempt_id.label("plan_attempt_id"),
            generations.c.data_revision_at,
            generations.c.created_at.label("generation_published_at"),
        ).where(any_of(generations.c.generation_id, generation_ids))).mappings()
    } if generation_ids else {}
    if len(approved) != len(generation_ids):
        raise ExportArtifactError("승인 inventory의 generation이 유실됐습니다.")
    for row in rows:
        row.update(approved[row["generation_id"]])
    catalog = {row["location_id"]: row for row in locations}
    now = datetime.now(UTC)
    # 만료한 승인은 값을 내지 않으므로 plan·좌표·건강도 검증 대상도 아니다. 최초 승인 여부만 센다.
    ever = {(row["provider"], row["dataset_key"]) for row in rows if row["location_id"] in catalog}
    expired_parts = {
        (row["provider"], row["dataset_key"], row["location_id"], row["source_part"])
        for row in rows
        if row["location_id"] in catalog
        and now - row["source_collected_at"] > source_expiry(row["dataset_key"])
    }
    rows = [
        row for row in rows
        if (row["provider"], row["dataset_key"], row["location_id"], row["source_part"])
        not in expired_parts
    ]
    missing = {row["location_id"] for row in rows} - catalog.keys()
    if missing:
        catalog_table = metadata.tables["weather_locations"]
        disabled = set(connection.scalars(select(catalog_table.c.location_id).where(
            catalog_table.c.location_id.in_(missing), catalog_table.c.enabled.is_(False),
        )))
        if missing - disabled:
            # 활성 roster에서 보이지 않는 것과 운영자가 명시 비활성화한 것은 다르다.
            # 실제 catalog 유실은 이전 완전 snapshot으로 fallback해야 한다.
            raise ExportArtifactError("승인 source의 canonical catalog 행이 유실됐습니다.")
    valid, sources = {}, set()
    shown = {row["plan_attempt_id"] for row in rows if row["location_id"] in catalog}
    # 보이는 위치의 plan이 없으면 parsed_scope_plans가 "plan 유실"로 거절한다.
    parsed_plans = parsed_scope_plans(connection, plans, sorted(shown))
    hidden = {row["plan_attempt_id"] for row in rows} - shown
    if hidden:
        # 명시 비활성 위치의 plan은 descriptor를 읽지 않고 존재만 확인한다.
        hidden -= set(connection.scalars(
            select(plans.c.attempt_id).where(any_of(plans.c.attempt_id, sorted(hidden)))
        ))
    if hidden:
        raise ExportArtifactError("승인 source의 불변 plan이 유실됐습니다.")
    plan_namespaces = {key: value.namespace for key, value in parsed_plans.items()}
    descriptors_by_namespace = {}
    for row in rows:
        current = catalog.get(row["location_id"])
        if current is None:
            continue  # 위에서 같은 MVCC의 명시 enabled=False만 확인했다.
        descriptor = parsed_plans[row["plan_attempt_id"]]
        if plan_namespaces[row["plan_attempt_id"]] != row["namespace"]:
            raise ExportArtifactError("승인 inventory와 불변 source plan이 다릅니다.")
        descriptors_by_namespace[row["namespace"]] = descriptor
        _validate_anchor(descriptor, current)
        if (
            (float(current["latitude"]), float(current["longitude"]))
            != (row["latitude"], row["longitude"])
            or (
                isinstance(descriptor, ScopeDescriptor)
                and (current["nx"], current["ny"]) != (row["nx"], row["ny"])
            )
        ):
            raise ExportArtifactError("승인 source의 canonical 좌표/grid가 바뀌었습니다.")
        key = row["provider"], row["dataset_key"], row["location_id"], row["source_part"]
        valid[key] = row
        sources.add(key[:2])
    if expected - optional - ever:
        raise ExportArtifactError("필수 provider/dataset의 최초 승인 위치가 없습니다.")
    # 신선도 창 밖(만료 전) 승인은 값으로는 내지만 건강도·미확인 판정에서는 뺀다.
    health_rows = [
        r for r in valid.values()
        if (health_keys is None or r["location_id"] in health_keys)
        and counts_for_freshness(r["source_collected_at"], now)
    ]
    # 승인은 있지만 신선도 창 안 승인이 하나도 없는 원천(통째로 침묵)은 미확인이다.
    fresh_sources = {(row["provider"], row["dataset_key"]) for row in health_rows}
    unconfirmed = (
        bool(expected - fresh_sources) if health_keys is None
        else bool({
            (r["provider"], r["dataset_key"]) for r in valid.values()
            if r["location_id"] in health_keys
        } - fresh_sources)
    ) or any(
        now - row["source_collected_at"] > source_max_age(row["dataset_key"])
        or now - row["data_revision_at"] > source_max_age(row["dataset_key"])
        for row in health_rows
    )
    namespaces = {row["namespace"] for row in health_rows}
    heads = metadata.tables["weather_export_heads"]
    live_heads = set(connection.scalars(select(heads.c.namespace).where(
        any_of(heads.c.namespace, sorted(namespaces)),
    )))
    # inventory는 파일 live root이지만 현재 head의 유실까지 정상으로 꾸미지는 않는다.
    unconfirmed |= live_heads != namespaces
    source_scopes = {row["source_scope"] for row in health_rows}
    # 시도 유실은 INNER JOIN 누락 → 개수/cohort 대조에서 미확인이다. 공개 세대는
    # attempt 이력과 독립인 성공 증거이며, 같은 RR에서 원본 epoch·native 상태를 대조한다.
    active = _active_scope_states(connection, metadata, source_scopes)
    unconfirmed |= len(active) != len(source_scopes) or any(
        attempt == "failed" or native_status not in {"running", "success"}
        or native_status == "success" and generation is None
        or not epoch_matches
        for _, _, _, attempt, generation, native_status, epoch_matches in active
    )
    cohorts = external_cohort_table(metadata)
    declarations = {
        (row["provider"], row["dataset_key"]): row for row in connection.execute(select(
            cohorts, runs.c.status.label("native_status"),
        ).outerjoin(runs, runs.c.run_id == cohorts.c.run_id).where(
            tuple_(cohorts.c.provider, cohorts.c.dataset_key).in_(expected),
        )).mappings()
    }
    # cohort 대조에만 run_id가 필요하다. attempt 이력이 정리됐으면 미확인으로 본다.
    run_ids = {row[1]: row[2] for row in active}
    needed = sorted({
        row["plan_attempt_id"] for row in health_rows
        if (row["provider"], row["dataset_key"]) in declarations
        and row["plan_attempt_id"] not in run_ids
    })
    if needed:
        run_ids.update(connection.execute(select(attempts.c.attempt_id, attempts.c.run_id).where(
            any_of(attempts.c.attempt_id, needed),
        )).tuples().all())
    for source in expected:
        row = declarations.get(source)
        if row is None:
            unconfirmed |= health_keys is None or any(
                (r["provider"], r["dataset_key"]) == source for r in health_rows
            )
            continue
        descriptors = _cohort_descriptors(row)
        if health_keys is not None and not (
            health_keys.intersection(key for d in descriptors for key in d.location_ids)
            or any((r["provider"], r["dataset_key"]) == source for r in health_rows)
        ):
            continue
        latest = connection.scalar(select(runs.c.run_id).where(
            runs.c.provider == source[0], runs.c.dataset_key == source[1],
            runs.c.export_cohort_required.is_(True),
        ).order_by(runs.c.started_at.desc(), runs.c.run_id.desc()).limit(1))
        unconfirmed |= latest != row["run_id"] or row["native_status"] != "success"
        selected, selected_parts = set(), set()
        for descriptor in descriptors:
            if (descriptor.provider, descriptor.dataset_key) != source:
                raise ExportArtifactError("실제 source roster가 다른 dataset을 포함합니다.")
            descriptor_namespace = descriptor.namespace
            for key in descriptor.location_ids:
                part = getattr(descriptor, "source_part", "main")
                if (key, part) in selected_parts:
                    raise ExportArtifactError("실제 source roster에 동일 위치가 중복됩니다.")
                selected_parts.add((key, part))
                selected.add(key)
                if health_keys is not None and key not in health_keys:
                    continue
                approved = valid.get((*source, key, part))
                if approved is None and (*source, key, part) in expired_parts:
                    continue  # 만료 = 이 원천이 내지 않는 위치(공백). 이번 실패의 미확인이 아니다.
                if approved is not None and not counts_for_freshness(
                    approved["source_collected_at"], now,
                ):
                    continue  # 신선도 창 밖 승인은 값만 낸다(미확인 판정에서 뺀다)
                if approved is None or (
                    approved["namespace"] != descriptor_namespace
                    or run_ids.get(approved["plan_attempt_id"]) is None
                    or run_ids[approved["plan_attempt_id"]] != row["run_id"]
                ):
                    unconfirmed = True
        if selected != {item["location_id"] for item in row["targets"]}:
            raise ExportArtifactError("실제 roster의 선택 위치와 descriptor가 다릅니다.")
    grouped = defaultdict(list)
    for row in valid.values():
        grouped[row["namespace"]].append(row)
    scopes = []
    for namespace, items in sorted(grouped.items()):
        sample = items[0]
        if len({row["generation_id"] for row in items}) != 1:
            raise ExportArtifactError("동일 namespace의 승인 generation이 서로 다릅니다.")
        descriptor = descriptors_by_namespace[namespace]
        ids = tuple(sorted(row["location_id"] for row in items))
        if descriptor.namespace != namespace or not set(ids).issubset(descriptor.location_ids):
            raise ExportArtifactError("승인 inventory와 불변 source plan이 다릅니다.")
        scopes.append(ApprovedScope(
            descriptor, sample["generation_id"], ids, sample["source_collected_at"],
            sample["data_revision_at"], sample["generation_published_at"],
        ))
    if not scopes or len(scopes) > MAX_SOURCE_SCOPES:
        raise ExportArtifactError("승인 source vector가 비었거나 상한을 넘었습니다.")
    return ApprovedSelection(
        tuple(scopes), unconfirmed, frozenset(key[:3] for key in expired_parts),
    )
