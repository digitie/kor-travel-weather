"""실제 승인 위치와 최신 HTTP 전 선택 집합을 같은 MVCC에서 함께 읽는다."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, tuple_

from .export_artifacts import ExportArtifactError
from .export_external import RetrievalScopeDescriptor
from .export_external_cohorts import external_cohort_table
from .export_heads import ScopeDescriptor, parse_scope_descriptor
from .export_inventory import export_inventory_table
from .export_mid import MidScopeDescriptor
from .export_mid_anchor import mid_anchor_columns
from .export_sdk import SdkScopeDescriptor

MAX_SELECTED_ROWS = 20000
SDK_NETWORKS = {
    "krforest_mountain_weather": "산악기상관측망", "krforest_dust": "청정넷(AICAN)",
    "krex_restarea_weather": "고속도로 휴게소 기상관측", "khoa_beach_index": "해수욕장 해양지수",
}


def catalog_source_columns(catalog):
    """임의 metadata 전체를 읽지 않고 SDK anchor의 작은 allowlist만 투영한다."""
    point = catalog.c.metadata["measurement_point"]
    return [*mid_anchor_columns(catalog), *[
            func.left(point[key].as_string(), maximum + 1).label("point_" + key)
            for key, maximum in [
                ("provider", 120), ("station_id", 120), ("station_name", 200),
                ("sido_name", 32), ("network", 120),
            ]]]


def source_max_age(dataset):
    if dataset in SDK_NETWORKS:
        return timedelta(hours=36)
    if dataset == "kma_mid_forecast":
        return timedelta(hours=18)
    if dataset == "kma_short_forecast" or dataset.endswith("_forecast"):
        return timedelta(hours=6)
    return timedelta(hours=3)


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


def _cohort_descriptors(row):
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
    rows = connection.execute(select(
        table, plans.c.descriptor, attempts.c.run_id,
        generations.c.data_revision_at, generations.c.created_at.label("generation_published_at"),
    ).select_from(
        table.join(generations, table.c.generation_id == generations.c.generation_id)
        .join(attempts, generations.c.attempt_id == attempts.c.attempt_id)
        .join(plans, plans.c.attempt_id == attempts.c.attempt_id)
    ).where(tuple_(table.c.provider, table.c.dataset_key).in_(expected)).order_by(
        table.c.provider, table.c.dataset_key, table.c.location_id,
    ).limit(MAX_SELECTED_ROWS + 1)).mappings().all()
    if len(rows) > MAX_SELECTED_ROWS:
        raise ExportArtifactError("승인 source inventory metadata 상한을 넘었습니다.")
    catalog = {row["location_id"]: row for row in locations}
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
    descriptors_by_namespace = {}
    for row in rows:
        current = catalog.get(row["location_id"])
        if current is None:
            continue  # 위에서 같은 MVCC의 명시 enabled=False만 확인했다.
        if row["namespace"] not in descriptors_by_namespace:
            descriptors_by_namespace[row["namespace"]] = parse_scope_descriptor(row["descriptor"])
        descriptor = descriptors_by_namespace[row["namespace"]]
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
    if expected - optional - sources:
        raise ExportArtifactError("필수 provider/dataset의 최초 승인 위치가 없습니다.")
    now = datetime.now(UTC)
    health_rows = [
        r for r in valid.values() if health_keys is None or r["location_id"] in health_keys
    ]
    unconfirmed = (bool(expected - sources) if health_keys is None else False) or any(
        now - row["source_collected_at"] > source_max_age(row["dataset_key"])
        or now - row["data_revision_at"] > source_max_age(row["dataset_key"])
        for row in health_rows
    )
    namespaces = {row["namespace"] for row in health_rows}
    heads = metadata.tables["weather_export_heads"]
    live_heads = set(connection.scalars(select(heads.c.namespace).where(
        heads.c.namespace.in_(namespaces),
    )))
    # inventory는 파일 live root이지만 현재 head의 유실까지 정상으로 꾸미지는 않는다.
    unconfirmed |= live_heads != namespaces
    owners = metadata.tables["weather_export_scope_owners"]
    source_scopes = {row["source_scope"] for row in health_rows}
    active = list(connection.execute(select(
        owners.c.source_scope, attempts.c.status, runs.c.status,
        generations.c.generation_id, owners.c.owner_epoch,
        func.left(attempts.c.owner_epochs[owners.c.source_scope].as_string(), 32),
    ).select_from(owners.join(
        attempts, owners.c.active_attempt_id == attempts.c.attempt_id,
    ).join(runs, runs.c.run_id == attempts.c.run_id).outerjoin(
        generations, generations.c.attempt_id == attempts.c.attempt_id,
    )).where(owners.c.source_scope.in_(source_scopes))))
    # native terminal뿐 아니라 확정된 Dagster 실패를 publisher가 기록한 attempt도
    # 현재 승인 집합의 건강도에 반영한다. 이전 파일과 inventory는 그대로 보존한다.
    unconfirmed |= len(active) != len(source_scopes) or any(
        attempt == "failed" or native not in {"running", "success"}
        or native == "success" and generation is None
        or claimed_epoch != str(epoch)
        for _, attempt, native, generation, epoch, claimed_epoch in active
    )
    cohorts = external_cohort_table(metadata)
    declarations = {
        (row["provider"], row["dataset_key"]): row for row in connection.execute(select(
            cohorts, runs.c.status.label("native_status"),
        ).outerjoin(runs, runs.c.run_id == cohorts.c.run_id).where(
            tuple_(cohorts.c.provider, cohorts.c.dataset_key).in_(expected),
        )).mappings()
    }
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
            for key in descriptor.location_ids:
                part = getattr(descriptor, "source_part", "main")
                if (key, part) in selected_parts:
                    raise ExportArtifactError("실제 source roster에 동일 위치가 중복됩니다.")
                selected_parts.add((key, part))
                selected.add(key)
                if health_keys is not None and key not in health_keys:
                    continue
                approved = valid.get((*source, key, part))
                if approved is None or (
                    approved["namespace"] != descriptor.namespace
                    or approved["run_id"] != row["run_id"]
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
    if not scopes or len(scopes) > 5000:
        raise ExportArtifactError("승인 source vector가 비었거나 상한을 넘었습니다.")
    return ApprovedSelection(tuple(scopes), unconfirmed)
