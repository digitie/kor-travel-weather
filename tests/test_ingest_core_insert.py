"""원본 ingest transaction 안의 Python 작업을 줄인다(2026-11-11).

odroid kma_short 표본: ingest 세션의 25~30%가 transaction을 연 채 Python 작업이었다.
GitHub Actions 프로파일(격자 1개 4,800 fact)에서 ORM 행 구성·속성 계측·
unit-of-work flush가 그 대부분이었다. 새 fact는 같은 transaction의 Core INSERT로
저장하고, 입력 증거 digest는 transaction 밖에서 계산한다. 결과·receipt는 같다.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from kortravelweather import repository as repository_module
from kortravelweather.export_artifacts import ExportArtifactStore
from kortravelweather.export_lifecycle import normalization_input_digest
from kortravelweather.export_receipts import (
    ExportCapture,
    export_receipt_tables,
    source_scope_key,
)
from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.repository import (
    Base,
    WeatherCurrentValueRow,
    WeatherRepository,
    WeatherValueRow,
)


@pytest.fixture(scope="module")
def repo():
    repository = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    repository.create_schema()
    yield repository
    repository.engine.dispose()


def _batch(repository, count=3):
    identity = uuid.uuid4().hex
    location_id = "core-insert-" + identity
    provider = "core-insert-provider-" + identity
    old = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    repository.create_location(
        WeatherLocation(location_id=location_id, name="검증", latitude=37.5, longitude=127)
    )
    record = dict(
        source_record_key="source-" + identity, provider=provider, dataset_key="d",
        source_entity_type="weather_response", source_entity_id=location_id,
        payload={"rows": ["1"]}, fetched_at=old,
    )
    values = [
        WeatherValue(
            location_id=location_id, provider=provider, dataset_key="d",
            weather_domain="weather", forecast_style=ForecastStyle.SHORT,
            metric_key="TMP", target_at=old + timedelta(hours=index + 1), issued_at=old,
            valid_at=old + timedelta(hours=index + 1),
            value_number=Decimal("1.23445") + index, unit="C",
            payload={"obsr_value": str(index), "category": "TMP"},
            source_record_key=record["source_record_key"],
        )
        for index in range(count)
    ]
    return record, values


def _capture(repository, record, root):
    run = repository.start_sync_run(provider=record["provider"], dataset_key=record["dataset_key"])
    record = {**record, "run_id": run.run_id}
    attempt = repository.begin_export_attempt(
        run_id=run.run_id, scope_key="all", execution_mode="native",
        planned_sources=[source_scope_key(
            record["provider"], record["dataset_key"],
            record["source_entity_type"], record["source_entity_id"],
        )],
    )
    capture = ExportCapture(
        attempt=attempt, chunk_key="chunk-1",
        source_observed_at={record["source_record_key"]: datetime.now(UTC)},
        artifacts=ExportArtifactStore(root),
    )
    return record, capture


def test_new_facts_are_inserted_without_orm_fact_rows(repo, tmp_path, monkeypatch):
    record, values = _batch(repo)
    record, capture = _capture(repo, record, tmp_path)
    added: list[type] = []
    original_add = Session.add

    def spy(self, instance, *args, **kwargs):
        added.append(type(instance))
        return original_add(self, instance, *args, **kwargs)

    monkeypatch.setattr(Session, "add", spy)
    assert repo.ingest_batch(
        source_records=[record], values=values, export_capture=capture,
    ) == len(values)
    # 원천·run link는 ORM이지만 fact 행은 ORM 객체로 만들지 않는다.
    assert WeatherValueRow not in added
    with repo.engine.connect() as connection:
        rows = connection.execute(
            select(WeatherValueRow).where(WeatherValueRow.location_id == values[0].location_id)
            .order_by(WeatherValueRow.target_at)
        ).mappings().all()
        projected = connection.scalar(
            select(WeatherCurrentValueRow.value_id).where(
                WeatherCurrentValueRow.location_id == values[0].location_id
            ).limit(1)
        )
    assert [row["value_number"] for row in rows] == [Decimal("1.2345"), Decimal("2.2345"),
                                                      Decimal("3.2345")]
    assert all(row["payload"]["category"] == "TMP" and row["unit"] == "C" for row in rows)
    assert all(row["forecast_style"] == "short" for row in rows)
    assert projected is not None
    _, chunks = export_receipt_tables(Base.metadata)
    with repo.engine.connect() as connection:
        receipt = connection.execute(
            select(chunks).where(chunks.c.attempt_id == capture.attempt.attempt_id)
        ).mappings().one()
    assert receipt["point_count"] == len(values)


def test_input_digest_is_computed_outside_the_ingest_transaction(repo, tmp_path, monkeypatch):
    record, values = _batch(repo, count=2)
    record, capture = _capture(repo, record, tmp_path)
    calls: list[int] = []

    def digest(facts):
        # 잠금을 쥔 ingest transaction(연결) 밖이어야 한다.
        calls.append(repo.engine.pool.checkedout())
        return normalization_input_digest(facts)

    monkeypatch.setattr(repository_module, "normalization_input_digest", digest)
    assert repo.ingest_batch(
        source_records=[record], values=values, export_capture=capture,
    ) == len(values)
    assert calls == [0]


def test_duplicate_fact_in_one_batch_is_still_a_replay(repo):
    record, values = _batch(repo, count=1)
    repo.record_source(**{k: record[k] for k in (
        "source_record_key", "provider", "dataset_key", "source_entity_type",
        "source_entity_id", "payload", "fetched_at",
    )})
    assert repo.ingest_batch(values=[values[0], values[0]]) == 1
    changed = values[0].model_copy(update={"value_number": Decimal("9")})
    with pytest.raises(ValueError, match="immutable weather fact"):
        repo.ingest_batch(values=[values[0], changed])
