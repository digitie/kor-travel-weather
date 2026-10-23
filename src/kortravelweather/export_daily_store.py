"""승인 generation에서 작은 일간 요약을 증분 생성한다. 요청 경로는 집계하지 않는다."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert

from .export_artifacts import ExportArtifactError, ExportArtifactStore
from .export_daily import KST, merge_observations, observation, summarize
from .export_heads import ExportHeads


def daily_summary_tables(metadata: MetaData):
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
    )
    progress = Table(
        "weather_daily_summary_progress",
        metadata,
        Column("progress_id", Integer, primary_key=True),
        Column("published_at", DateTime(timezone=True), nullable=False),
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
    with heads._transaction() as s:
        checkpoint = s.execute(select(progress)).mappings().one_or_none()
        after = checkpoint["published_at"] if checkpoint else datetime(1970, 1, 1, tzinfo=UTC)
        rows = [
            dict(r)
            for r in s.execute(
                select(heads.generations, heads.plans.c.descriptor)
                .join(heads.plans, heads.plans.c.attempt_id == heads.generations.c.attempt_id)
                .where(heads.generations.c.created_at > after)
                .order_by(heads.generations.c.created_at)
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
    # guard timeout은 획득 대기 상한이며 전체 IO의 wall deadline은 아니다.
    with artifacts.guard(timeout_seconds=2):
        for row in rows:
            if row["descriptor"]["forecast_style"] not in {"observed", "nowcast"}:
                continue
            chunks = refs[row["generation_id"]]
            if not chunks or sum(c["point_count"] for c in chunks) > 5000:
                raise ExportArtifactError("관측 요약 generation 상한을 넘었습니다.")
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
            .values(progress_id=1, published_at=after, updated_at=now)
            .on_conflict_do_nothing()
        )
        locked = (
            s.execute(select(progress).where(progress.c.progress_id == 1).with_for_update())
            .mappings()
            .one()
        )
        if locked["published_at"] != after:
            return {"processed": 0, "has_more": True, "race": True}
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
                source_collected_at=collected,
                updated_at=now,
            )
        )
    return {"processed": len(rows), "has_more": len(rows) == limit}
