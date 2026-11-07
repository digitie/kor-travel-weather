"""승인 공백 날짜의 일간 요약을 KMA 실황 원천 응답으로 다시 채우는 일회성 backfill.

2026-10-25~11-01에 export publisher가 멈춰 있었고(11-02 03:13Z 재개), 그동안 쌓인 attempt
약 19만 8천 건은 SUPERSEDED로 퇴역했다. 그 날짜들의 승인 generation이 없어 W5 요약 표본이
0이다. 정규화된 fact는 보존 7일이 지나 사라졌지만 원천 응답(``weather_source_records``)은
남아 있다.

이 모듈은 그 원천 응답을 수집 때와 같은 정규화(``ultra_short_nowcast_to_weather_values``)로
다시 읽고, 같은 reducer(``merge_observations``/``summarize``)로 기존 요약에 합친다.

- 성공한 수집 run에 묶인 원천만 쓴다(실패·부분 run 자료는 섞지 않는다). 정상 경로는 export
  증명까지 통과한 승인만 쓰므로 이 복구는 그보다 넓다(수집은 성공했으나 증명 전 퇴역한 응답
  포함 — 그것이 복구 대상이다).
- KMA 실황만 쓴다. 요약 winner 우선순위에서 KMA가 첫째라 다른 제공처를 더해도 같은 시간의
  winner는 바뀌지 않는다. KMA가 비운 시간만 다른 제공처가 채웠을 것이고, 그것은 복구하지
  않는다(원천별 정규화가 제각각이라 이 일회성 범위 밖이다).
- 위치는 지금 catalog의 활성 위치 중 같은 격자(nx, ny)다. 그 사이 격자가 바뀐 위치는 새 격자의
  값을 받는다(해당 기간 catalog 이력은 없다).
- head·generation·inventory는 건드리지 않는다. 요약 행만 합친다(같은 키는 행 잠금, 같은 값이면
  쓰지 않는다). 다시 돌려도 결과가 같다.
- 묶음마다 짧은 transaction(statement 5초·lock 1초)과 쉼을 두고, 시간 상한에서 cursor를 돌려준다.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select, text, tuple_
from sqlalchemy.dialects.postgresql import insert

from .export_artifacts import canonical_export_number
from .export_daily import KST, merge_observations, observation, summarize
from .export_daily_store import daily_summary_tables
from .export_heads import ExportHeads
from .providers.kma import ultra_short_nowcast_to_weather_values

PROVIDER = "python-kma-api"
DATASET = "kma_ultra_short_nowcast"
MAX_DAYS = 31
_GRID = re.compile(r"grid:(\d{1,3}):(\d{1,3})")


def _grid(entity_id, rows):
    """원천 응답의 격자. entity id(``grid:nx:ny``)를 먼저, 없으면 응답 행이 한 격자일 때만."""
    match = _GRID.fullmatch(str(entity_id))
    if match is not None:
        return int(match[1]), int(match[2])
    cells = {(row.get("nx"), row.get("ny")) for row in rows if isinstance(row, dict)}
    if len(cells) == 1:
        nx, ny = next(iter(cells))
        if isinstance(nx, int) and isinstance(ny, int):
            return nx, ny
    return None


def _points(record, locations):
    for location_id in locations:
        for value in ultra_short_nowcast_to_weather_values(
            record["payload"]["rows"],
            location_id=location_id,
            source_record_key=record["source_record_key"],
            known_at=record["fetched_at"],
        ):
            # 수집 시각은 원천 fetch 시각으로 고정한다(기본값 now면 재실행마다 tie-break hash가
            # 달라진다).
            value = value.model_copy(update={"collected_at": record["fetched_at"]})
            point = value.model_dump(mode="json", exclude={"payload"})
            if value.value_number is not None:
                point["value_number"] = canonical_export_number(value.value_number)
            yield point


def backfill_daily_from_kma_nowcast(
    repository,
    *,
    first_day: date,
    last_day: date,
    after: str = "",
    batch: int = 100,
    pause_seconds: float = 0.5,
    max_seconds: float | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """[first_day, last_day](KST) 요약을 원천 응답으로 채운다. ``after``부터 이어 간다."""
    now = now or datetime.now(UTC)
    today = now.astimezone(KST).date()
    # 오늘은 정상 reducer가 쓰는 중이다(없던 행을 덮어쓸 수 있다). 지난 날만 채운다.
    if not (today - timedelta(days=MAX_DAYS) <= first_day <= last_day < today):
        raise ValueError("backfill 날짜는 최근 31일 안의 지난 날이어야 합니다.")
    if not 1 <= batch <= 1000:
        raise ValueError("backfill 묶음은 1~1000입니다.")
    heads = ExportHeads(repository)
    summaries, _ = daily_summary_tables(heads.metadata)
    catalog = heads.metadata.tables["weather_locations"]
    sources = heads.metadata.tables["weather_source_records"]
    run_sources = heads.metadata.tables["weather_sync_run_sources"]
    runs = heads.metadata.tables["weather_sync_runs"]
    with repository.engine.connect() as connection:
        grids = defaultdict(list)
        for row in connection.execute(
            select(catalog.c.location_id, catalog.c.nx, catalog.c.ny).where(
                catalog.c.enabled.is_(True), catalog.c.nx.is_not(None), catalog.c.ny.is_not(None),
            )
        ):
            grids[(row.nx, row.ny)].append(row.location_id)
    # 실황 응답의 base 시각은 보통 fetch 직전 정시지만 늦은 재수집도 있다. 앞은 두 시간, 뒤는
    # 하루를 더 읽고 point의 KST 날짜로 다시 거른다.
    start = datetime.combine(first_day, datetime.min.time(), KST) - timedelta(hours=2)
    end = datetime.combine(last_day + timedelta(days=2), datetime.min.time(), KST)
    deadline = None if max_seconds is None else time.monotonic() + max_seconds
    stats = {"records": 0, "keys": 0, "updated": 0, "skipped": 0, "days": defaultdict(int),
             "cursor": after}
    # 대상 원천 key를 (dataset, fetched_at) index로 한 번만 고른다. 성공 run 연결은 key마다
    # EXISTS다.
    with repository.engine.connect() as connection, connection.begin():
        connection.exec_driver_sql("SET LOCAL statement_timeout = '120s'")
        wanted = list(connection.scalars(
            select(sources.c.source_record_key).where(
                sources.c.provider == PROVIDER, sources.c.dataset_key == DATASET,
                sources.c.fetched_at >= start, sources.c.fetched_at < end,
                sources.c.source_record_key > after,
                select(run_sources.c.run_id).join(runs, runs.c.run_id == run_sources.c.run_id)
                .where(run_sources.c.source_record_key == sources.c.source_record_key,
                       runs.c.status == "success").exists(),
            ).order_by(sources.c.source_record_key)
        ))
    stats["candidates"] = len(wanted)
    position = 0
    while position < len(wanted) and (deadline is None or time.monotonic() < deadline):
        chunk = wanted[position:position + batch]
        with repository.engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
            records = list(connection.execute(
                select(
                    sources.c.source_record_key, sources.c.source_entity_id,
                    sources.c.payload, sources.c.fetched_at,
                ).where(sources.c.source_record_key.in_(chunk))
                .order_by(sources.c.source_record_key)
            ).mappings())
        grouped = defaultdict(list)
        for record in records:
            payload = record["payload"]
            grid = (
                _grid(record["source_entity_id"], payload["rows"])
                if isinstance(payload, dict) and isinstance(payload.get("rows"), list) else None
            )
            if grid is None:
                stats["skipped"] += 1
                continue
            try:
                points = list(_points(record, grids.get(grid, [])))
            except (ValueError, TypeError, KeyError):
                stats["skipped"] += 1  # 한 응답의 형식 오류가 전체 복구를 멈추지 않는다
                continue
            for point in points:
                parsed = observation(point)
                if parsed is not None and first_day <= date.fromisoformat(parsed[0][1]) <= last_day:
                    grouped[parsed[0]].append(point)
        stats["records"] += len(records)
        stats["keys"] += len(grouped)
        if grouped and not dry_run:
            with heads._transaction() as session:
                session.execute(text("SET LOCAL statement_timeout = '5s'"))
                session.execute(text("SET LOCAL lock_timeout = '1s'"))
                keys = sorted(grouped)
                existing = {
                    (row.location_id, row.date.isoformat(), row.slot): row.hourly_winners
                    for row in session.execute(
                        select(
                            summaries.c.location_id, summaries.c.date, summaries.c.slot,
                            summaries.c.hourly_winners,
                        ).where(tuple_(summaries.c.location_id, summaries.c.date, summaries.c.slot)
                                .in_([(k[0], date.fromisoformat(k[1]), k[2]) for k in keys]))
                        # 정상 reducer와 같은 키 순서로 잠근다(교착 회피).
                        .order_by(summaries.c.location_id, summaries.c.date, summaries.c.slot)
                        .with_for_update()
                    )
                }
                for key in keys:
                    previous = existing.get(key)
                    state = merge_observations(previous or {}, grouped[key], key=key, now=now)
                    summary = summarize(*key, state, now=now)
                    # 정상 경로가 이미 같은 값을 낸 행은 그대로 둔다(동률 tie-break hash만
                    # 다른 winner로 바꿔 쓰지 않는다).
                    if previous is not None and summarize(*key, previous, now=now) == summary:
                        continue
                    values = {
                        "summary": summary,
                        "hourly_winners": state,
                        "updated_at": now,
                    }
                    session.execute(
                        insert(summaries)
                        .values(location_id=key[0], date=date.fromisoformat(key[1]), slot=key[2],
                                **values)
                        .on_conflict_do_update(
                            index_elements=["location_id", "date", "slot"], set_=values,
                        )
                    )
                    stats["updated"] += 1
                    stats["days"][key[1]] += 1
        position += len(chunk)
        stats["cursor"] = chunk[-1]
        if pause_seconds:
            time.sleep(pause_seconds)
    stats["days"] = dict(sorted(stats["days"].items()))
    stats["done"] = position >= len(wanted)
    return stats
