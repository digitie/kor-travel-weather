"""재수집 판정은 원천 원문 payload를 읽지 않는다(2026-11-24 Map 호출 검토 L6).

원천 payload는 최대 8MiB다. 재수집 판정(기본키 조회, 새 키로 다시 받은 같은 원문의 identity
조회)은 identity 열만 쓰므로 payload를 SELECT하지도 identity map에 두지도 않는다.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, text

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherRepository

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)

LOCATION = "replay-payload"


@pytest.fixture
def repository():
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    repo.upsert_location(WeatherLocation(
        location_id=LOCATION, name="재수집", latitude=37.5, longitude=127, nx=60, ny=127,
    ))
    yield repo
    repo.engine.dispose()


def _batch() -> tuple[list[dict], list[WeatherValue]]:
    key = f"{LOCATION}-{uuid.uuid4().hex[:8]}"
    base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    record = {
        "source_record_key": key, "provider": "p", "dataset_key": "d",
        "source_entity_type": "weather_response", "source_entity_id": LOCATION,
        "payload": {"rows": [{"h": 1}], "blob": "x" * 2048},
    }
    value = WeatherValue(
        location_id=LOCATION, provider="p", dataset_key="d", weather_domain="d",
        forecast_style=ForecastStyle.SHORT, metric_key="TMP",
        target_at=base + timedelta(hours=1), issued_at=base, value_number=Decimal(1),
        source_record_key=key,
    )
    return [record], [value]


def _source_selects(repository: WeatherRepository, action) -> list[str]:  # noqa: ANN001
    selects: list[str] = []

    def before(_conn, _cursor, statement, _params, _context, _many):  # noqa: ANN001
        normalized = " ".join(statement.split())
        if normalized.upper().startswith("SELECT") and re.search(
            r"FROM weather_source_records\b", normalized
        ):
            selects.append(normalized)

    event.listen(repository.engine, "before_cursor_execute", before)
    try:
        action()
    finally:
        event.remove(repository.engine, "before_cursor_execute", before)
    return selects


def test_replay_checks_do_not_load_the_raw_payload(repository: WeatherRepository) -> None:
    records, values = _batch()
    assert repository.ingest_batch(source_records=records, values=values) == 1

    # 같은 키의 재수집: 기본키 조회(session.get)와 배치 원천 prefetch.
    same_key = _source_selects(
        repository, lambda: repository.ingest_batch(source_records=records, values=values)
    )
    # 같은 원문을 새 키로 다시 받음: 기본키 조회가 비고 identity 조회로 기존 행을 찾는다.
    alias = [dict(records[0], source_record_key=records[0]["source_record_key"] + "-alias")]
    aliased = _source_selects(
        repository, lambda: repository.ingest_batch(source_records=alias, values=[])
    )

    assert same_key and aliased
    assert any("raw_payload_hash =" in sql for sql in aliased), aliased
    for sql in same_key + aliased:
        assert "weather_source_records.payload" not in sql, sql
    # 결과는 그대로다: 별칭은 기존 원천 하나로 접힌다.
    with repository.engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM weather_source_records WHERE source_entity_id = :e"),
            {"e": LOCATION},
        ).scalar_one() == 1


def test_a_metric_source_replay_does_not_load_its_payload(
    repository: WeatherRepository,
) -> None:
    """원천 키 없이 들어온 fact의 대체 원천을 다시 찾는 기본키 조회(``_resolve_fact_session``).

    명시 원천은 배치 prefetch가 이미 payload 없이 읽는다. 이 경로만 fact마다 따로 찾는다.
    """
    _, values = _batch()
    unsourced = [values[0].model_copy(update={"source_record_key": None})]
    assert repository.ingest_batch(values=unsourced) == 1
    selects = _source_selects(repository, lambda: repository.ingest_batch(values=unsourced))
    assert any("weather_source_records.source_record_key = " in sql for sql in selects), selects
    for sql in selects:
        assert "weather_source_records.payload" not in sql, sql
