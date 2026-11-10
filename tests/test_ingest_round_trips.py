"""수집 한 배치의 DB 왕복이 레코드 수에 비례해 늘지 않는다(짧은 수집 TX).

예전에는 레코드마다 run-source link 존재 확인, 위치마다 row KEY SHARE, fact마다 원천 행
재조회를 따로 보냈다. 같은 잠금·같은 결과를 유지하면서 그 셋을 한 문장씩으로 줄였다.
"""

from __future__ import annotations

import os
import re
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import event

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherRepository

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)


def _batch(provider: str, run_id: str, locations: list[str], *, hours: int, base: datetime):
    records, values = [], []
    for key in locations:
        source = f"{provider}-{key}"
        records.append({
            "source_record_key": source, "provider": provider, "dataset_key": "d",
            "source_entity_type": "weather_response", "source_entity_id": key,
            "payload": {"rows": [{"h": h} for h in range(hours)]}, "run_id": run_id,
        })
        values.extend(
            WeatherValue(
                location_id=key, provider=provider, dataset_key="d", weather_domain="d",
                forecast_style=ForecastStyle.SHORT, metric_key="TMP",
                target_at=base + timedelta(hours=h + 1), issued_at=base,
                value_number=Decimal(h), source_record_key=source,
            )
            for h in range(hours)
        )
    return records, values


def _count_by_table(repo: WeatherRepository, action) -> Counter:
    counts: Counter = Counter()

    def before(_conn, _cursor, statement, _params, _context, _many):  # noqa: ANN001
        verb = statement.lstrip().split(None, 1)[0].upper()
        table = re.search(r"(?:FROM|INTO|UPDATE)\s+(weather_\w+)", statement, re.I)
        counts[(verb, table.group(1) if table else "-")] += 1

    event.listen(repo.engine, "before_cursor_execute", before)
    try:
        action()
    finally:
        event.remove(repo.engine, "before_cursor_execute", before)
    return counts


def test_ingest_batch_round_trips_do_not_grow_per_record() -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    provider = "rt-" + uuid.uuid4().hex[:8]
    locations = [f"{provider}-loc-{index:02d}" for index in range(12)]
    for key in locations:
        repo.upsert_location(
            WeatherLocation(location_id=key, name=key, latitude=37.5, longitude=127, nx=60, ny=127)
        )
    base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    run = repo.start_sync_run(provider=provider, dataset_key="d", locations_total=len(locations))
    records, values = _batch(provider, run.run_id, locations, hours=6, base=base)
    try:
        counts = _count_by_table(
            repo, lambda: repo.ingest_batch(source_records=records, values=values)
        )
        # 결과는 그대로다: fact 72개, run link 12개, current 12×6.
        assert repo.get_sync_run(run.run_id).values_loaded == len(values)
        assert len(repo.list_sync_run_sources(run.run_id)) == len(locations)
        # 레코드 12개·위치 12개·fact 72개여도 각각 한 문장.
        assert counts[("SELECT", "weather_sync_run_sources")] == 1, counts
        assert counts[("SELECT", "weather_locations")] <= 1, counts
        # 원천 행: 레코드마다 키/identity 확인(2) + 배치 전체 한 번. fact마다 다시 읽지 않는다.
        assert counts[("SELECT", "weather_source_records")] <= 2 * len(records) + 1, counts

        # 같은 run에 같은 원천을 다시 보내도 link가 중복되지 않는다(prefetch가 기존 link를 본다).
        repo.ingest_batch(source_records=records[:3], values=values[:18])
        assert len(repo.list_sync_run_sources(run.run_id)) == len(locations)
    finally:
        repo.finish_sync_run(run.run_id, status="success")
        repo.engine.dispose()
