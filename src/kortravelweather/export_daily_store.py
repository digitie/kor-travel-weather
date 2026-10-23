"""승인 generation에서 작은 일간 요약을 증분 생성한다. 요청 경로는 집계하지 않는다."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert

from .export_artifacts import ExportArtifactError, ExportArtifactStore
from .export_daily import KST, merge_observations, observation, summarize
from .export_heads import ExportHeads


def daily_coverage_table(metadata: MetaData):
    name = "weather_daily_summary_coverage"
    if name in metadata.tables:
        return metadata.tables[name]
    return Table(
        name,
        metadata,
        Column("location_id", String(120), primary_key=True),
        Column("namespace", String(64), primary_key=True),
        Column("provider", String(40), nullable=False),
        Column("source_scope", String(64), nullable=False),
        Column("nx", Integer),
        Column("ny", Integer),
        Column("latitude", Float),
        Column("longitude", Float),
        Column("source_collected_at", DateTime(timezone=True), nullable=False),
        Column("published_at", DateTime(timezone=True), nullable=False),
        Column("generation_id", String(36), nullable=False),
    )


def daily_summary_tables(metadata: MetaData):
    daily_coverage_table(metadata)
    if "weather_daily_summaries" in metadata.tables:
        return metadata.tables["weather_daily_summaries"], metadata.tables[
            "weather_daily_summary_progress"
        ]
    summaries = Table(
        "weather_daily_summaries",
        metadata,
        Column("location_id", String(120), primary_key=True),
        Column("date", Date, primary_key=True),
        Column("slot", String(10), primary_key=True),
        Column("summary", JSON, nullable=False),
        Column("hourly_winners", JSON, nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        Index("ix_weather_daily_summaries_date", "date"),
    )
    progress = Table(
        "weather_daily_summary_progress",
        metadata,
        Column("progress_id", Integer, primary_key=True),
        Column("published_at", DateTime(timezone=True), nullable=False),
        Column("generation_id", String(36), nullable=False),
        Column("source_collected_at", DateTime(timezone=True)),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    return summaries, progress


def refresh_daily_summaries(repository, artifacts: ExportArtifactStore, *, limit=4):
    """키셋 최대 4 generation. IO 중 PG connection과 summary 잠금을 잡지 않는다.

    성공 publication만 읽으므로 원본 부분 commit/실패 run은 섞이지 않는다.
    checkpoint와 요약 변경은 하나의 짧은 transaction으로 확정한다.
    """
    if not 1 <= limit <= 8:
        raise ValueError("요약 tick의 generation 상한은 1~8입니다.")
    heads = ExportHeads(repository)
    summaries, progress = daily_summary_tables(heads.metadata)
    coverage = daily_coverage_table(heads.metadata)
    with heads._transaction() as s:
        checkpoint = s.execute(select(progress)).mappings().one_or_none()
        after = checkpoint["published_at"] if checkpoint else datetime(1970, 1, 1, tzinfo=UTC)
        after_generation = checkpoint["generation_id"] if checkpoint else ""
        rows = [
            dict(r)
            for r in s.execute(
                select(heads.generations, heads.plans.c.descriptor, heads.plans.c.source_scope)
                .join(heads.plans, heads.plans.c.attempt_id == heads.generations.c.attempt_id)
                .where(
                    tuple_(heads.generations.c.created_at, heads.generations.c.generation_id)
                    > tuple_(after, after_generation)
                )
                .order_by(heads.generations.c.created_at, heads.generations.c.generation_id)
                .limit(limit)
            ).mappings()
        ]
        refs = defaultdict(list)
        for ref in s.execute(
            select(heads.refs)
            .where(heads.refs.c.generation_id.in_([r["generation_id"] for r in rows]))
            .order_by(heads.refs.c.ordinal)
        ).mappings():
            refs[ref["generation_id"]].append(dict(ref))
    if not rows:
        return {"processed": 0, "has_more": False}
    grouped, collected = (
        defaultdict(list),
        checkpoint["source_collected_at"] if checkpoint else None,
    )
    now = datetime.now(UTC)
    approved_coverage = []
    # guard timeout은 획득 대기 상한이며 전체 IO의 wall deadline은 아니다.
    with artifacts.guard(timeout_seconds=2, shared=True):
        for row in rows:
            if row["descriptor"]["forecast_style"] not in {"observed", "nowcast"}:
                continue
            chunks = refs[row["generation_id"]]
            if not chunks or sum(c["point_count"] for c in chunks) > 5000:
                raise ExportArtifactError("관측 요약 generation 상한을 넘었습니다.")
            descriptor = row["descriptor"]
            for location in descriptor["location_ids"]:
                approved_coverage.append(
                    {
                        "location_id": location,
                        "namespace": row["namespace"],
                        "provider": descriptor["provider"],
                        "source_scope": row["source_scope"],
                        "nx": descriptor.get("nx"),
                        "ny": descriptor.get("ny"),
                        "latitude": descriptor.get("latitude"),
                        "longitude": descriptor.get("longitude"),
                        "source_collected_at": row["source_collected_at"],
                        "published_at": row["created_at"],
                        "generation_id": row["generation_id"],
                    }
                )
            collected = (
                max(collected, row["source_collected_at"])
                if collected
                else row["source_collected_at"]
            )
            for ref in chunks:
                for point in artifacts.read(
                    ref["artifact_digest"],
                    byte_count=ref["byte_count"],
                    point_count=ref["point_count"],
                ):
                    parsed = observation(point)
                    if parsed is not None and now.astimezone(KST).date() - datetime.fromisoformat(
                        parsed[0][1]
                    ).date() <= timedelta(days=31):
                        grouped[parsed[0]].append(point)
    with heads._transaction() as s:
        # 단일 writer CAS. 이 잠금은 파일을 다 읽은 뒤에만 획득한다.
        s.execute(
            insert(progress)
            .values(
                progress_id=1, published_at=after, generation_id=after_generation, updated_at=now
            )
            .on_conflict_do_nothing()
        )
        locked = (
            s.execute(select(progress).where(progress.c.progress_id == 1).with_for_update())
            .mappings()
            .one()
        )
        if (locked["published_at"], locked["generation_id"]) != (after, after_generation):
            return {"processed": 0, "has_more": True, "race": True}
        for values in approved_coverage:
            s.execute(
                coverage.delete().where(
                    coverage.c.location_id == values["location_id"],
                    coverage.c.source_scope == values["source_scope"],
                    coverage.c.namespace != values["namespace"],
                )
            )
            statement = insert(coverage).values(**values)
            s.execute(
                statement.on_conflict_do_update(
                    index_elements=["location_id", "namespace"],
                    set_={
                        key: value
                        for key, value in values.items()
                        if key not in {"location_id", "namespace"}
                    },
                )
            )
        for key, points in sorted(grouped.items()):
            location, day, slot = key
            day_value = datetime.fromisoformat(day).date()
            row = s.execute(
                select(summaries.c.hourly_winners)
                .where(
                    summaries.c.location_id == location,
                    summaries.c.date == day_value,
                    summaries.c.slot == slot,
                )
                .with_for_update()
            ).scalar_one_or_none()
            state = merge_observations(row or {}, points, key=key, now=now)
            if row is not None and state == row:
                continue
            values = {
                "summary": summarize(*key, state, now=now),
                "hourly_winners": state,
                "updated_at": now,
            }
            s.execute(
                insert(summaries)
                .values(location_id=location, date=day_value, slot=slot, **values)
                .on_conflict_do_update(index_elements=["location_id", "date", "slot"], set_=values)
            )
        s.execute(
            update(progress)
            .where(progress.c.progress_id == 1)
            .values(
                published_at=rows[-1]["created_at"],
                generation_id=rows[-1]["generation_id"],
                source_collected_at=collected,
                updated_at=now,
            )
        )
    return {"processed": len(rows), "has_more": len(rows) == limit}


def prune_daily_summaries(repository, *, limit=500):
    """최대31일 조회에 하루 cursor 유예를 더해 오래된 파생 요약만 작게 지운다."""
    if not 1 <= limit <= 1000:
        raise ValueError("요약 정리 상한은 1~1000입니다.")
    heads = ExportHeads(repository)
    table, _ = daily_summary_tables(heads.metadata)
    cutoff = datetime.now(KST).date() - timedelta(days=31)
    with heads._transaction() as s:
        keys = list(
            s.execute(
                select(table.c.location_id, table.c.date, table.c.slot)
                .where(table.c.date < cutoff)
                .order_by(table.c.date, table.c.location_id, table.c.slot)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        if keys:
            s.execute(
                table.delete().where(
                    tuple_(table.c.location_id, table.c.date, table.c.slot).in_(
                        [tuple(k) for k in keys]
                    )
                )
            )
    return len(keys)
