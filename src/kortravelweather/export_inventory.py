"""quota 회전에 누락되지 않는 위치별 마지막 승인 source의 작은 projection."""

from dataclasses import dataclass
from math import isfinite

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    select,
)
from sqlalchemy.dialects.postgresql import insert

from .export_artifacts import ExportArtifactError

MAX_INVENTORY_ROWS = 20000


def export_inventory_table(metadata: MetaData):
    name = "weather_export_approved_locations"
    if name in metadata.tables:
        return metadata.tables[name]
    return Table(
        name, metadata,
        Column("provider", String(120), primary_key=True),
        Column("dataset_key", String(160), primary_key=True),
        Column("location_id", String(120), primary_key=True),
        Column("source_part", String(16), primary_key=True, server_default="main"),
        Column("namespace", String(64), nullable=False, index=True),
        Column("generation_id", String(36), ForeignKey(
            "weather_export_generations.generation_id", ondelete="RESTRICT",
        ), nullable=False, index=True),
        Column("source_scope", String(64), nullable=False),
        Column("latitude", Float, nullable=False),
        Column("longitude", Float, nullable=False),
        Column("nx", Integer), Column("ny", Integer),
        Column("source_collected_at", DateTime(timezone=True), nullable=False),
        Column("published_at", DateTime(timezone=True), nullable=False),
        CheckConstraint(
            "source_part IN ('main','land','temperature')", name="ck_weather_export_inventory_part",
        ),
    )


def record_approved_locations(session, metadata, *, descriptor, generation_id):
    """head CAS와 같은 TX에서 선택 위치만 갱신한다. 선택 밖 이전 승인은 유지한다."""
    from .export_heads import ScopeDescriptor

    table = export_inventory_table(metadata)
    generations, catalog = metadata.tables["weather_export_generations"], metadata.tables[
        "weather_locations"
    ]
    generation = session.execute(select(
        generations.c.namespace, generations.c.source_collected_at, generations.c.created_at,
    ).where(generations.c.generation_id == generation_id)).one()
    if generation.namespace != descriptor.namespace:
        raise ExportArtifactError("승인 inventory의 namespace와 generation이 다릅니다.")
    locations = session.execute(select(
        catalog.c.location_id, catalog.c.latitude, catalog.c.longitude, catalog.c.nx, catalog.c.ny,
    ).where(
        catalog.c.location_id.in_(descriptor.location_ids), catalog.c.enabled.is_(True),
    )).mappings().all()
    rows = []
    for location in locations:
        latitude, longitude = float(location["latitude"]), float(location["longitude"])
        if not (
            isfinite(latitude) and isfinite(longitude)
            and -90 <= latitude <= 90 and -180 <= longitude <= 180
        ):
            continue
        if isinstance(descriptor, ScopeDescriptor):
            valid = (location["nx"], location["ny"]) == (descriptor.nx, descriptor.ny)
        else:
            valid = (latitude, longitude) == (
                descriptor.latitude, descriptor.longitude,
            )
        if not valid:
            # 실행 도중 바뀐 위치를 새 승인으로 꾸미지 않는다. 이전 행은 stale 판별에 남긴다.
            continue
        rows.append({
            "provider": descriptor.provider, "dataset_key": descriptor.dataset_key,
            "source_part": getattr(descriptor, "source_part", "main"),
            **{key: location[key] for key in ["location_id", "latitude", "longitude", "nx", "ny"]},
            "namespace": descriptor.namespace, "source_scope": descriptor.source_scope,
            "generation_id": generation_id, "source_collected_at": generation.source_collected_at,
            "published_at": generation.created_at,
        })
    if not rows:
        return
    statement = insert(table).values(rows)
    session.execute(statement.on_conflict_do_update(
        index_elements=["provider", "dataset_key", "location_id", "source_part"],
        set_={key: getattr(statement.excluded, key) for key in rows[0] if key not in {
            "provider", "dataset_key", "location_id", "source_part",
        }},
        where=table.c.published_at < statement.excluded.published_at,
    ))


def read_approved_locations(connection, metadata, *, providers, dataset_keys):
    """작은 승인표만 읽는다. quota 선택 밖 위치의 이전 승인도 함께 유지한다."""
    table = export_inventory_table(metadata)
    rows = connection.execute(select(table).where(
        table.c.provider.in_(providers), table.c.dataset_key.in_(dataset_keys),
    ).order_by(table.c.provider, table.c.dataset_key, table.c.location_id).limit(
        MAX_INVENTORY_ROWS + 1
    )).mappings().all()
    if len(rows) > MAX_INVENTORY_ROWS:
        raise ExportArtifactError("승인 inventory 상한을 넘었습니다.")
    return rows


@dataclass(frozen=True)
class InventoryBootstrap:
    visited: int
    next_namespace: str | None
    exhausted: bool


def bootstrap_approved_locations(repository, *, after_namespace=None, limit=2):
    """이전 버전 승인 head를 작은 PK 키셋으로 재투영한다. fact/파일은 읽지 않는다.

    publisher는 마지막 cursor를 보존하고 exhausted 후 반복 전체 sweep을 하지 않는다.
    이는 rollout 준비용 helper이며 자동 호출/운영 활성화는 여기서 하지 않는다.
    """
    from .export_heads import ExportHeads, parse_scope_descriptor

    if not 1 <= limit <= 16:
        raise ValueError("inventory bootstrap은 한 번에 1~16개 head만 처리합니다.")
    if after_namespace is not None and (
        len(after_namespace) != 64 or any(c not in "0123456789abcdef" for c in after_namespace)
    ):
        raise ValueError("inventory bootstrap cursor가 올바르지 않습니다.")
    heads = ExportHeads(repository)
    with heads._transaction() as s:
        query = select(heads.heads.c.namespace).order_by(heads.heads.c.namespace).limit(limit + 1)
        if after_namespace is not None:
            query = query.where(heads.heads.c.namespace > after_namespace)
        namespaces = list(s.scalars(query))
    for namespace in namespaces[:limit]:
        with heads._transaction() as s:
            current = s.execute(select(
                heads.heads.c.generation_id, heads.plans.c.descriptor,
            ).select_from(
                heads.heads.join(
                    heads.generations,
                    heads.generations.c.generation_id == heads.heads.c.generation_id,
                ).join(heads.plans, heads.plans.c.attempt_id == heads.generations.c.attempt_id)
            ).where(heads.heads.c.namespace == namespace)).one_or_none()
            if current is not None:
                record_approved_locations(
                    s, heads.metadata,
                    descriptor=parse_scope_descriptor(current.descriptor),
                    generation_id=current.generation_id,
                )
    return InventoryBootstrap(
        min(limit, len(namespaces)), namespaces[min(limit, len(namespaces)) - 1]
        if namespaces else after_namespace, len(namespaces) <= limit,
    )
