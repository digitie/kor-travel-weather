"""중복 연산 축소의 PostgreSQL 저장·alias·rollback 회귀."""

from decimal import Decimal

import pytest
from sqlalchemy import event, func, select

from kortravelweather import repository as repository_module
from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue, kst_now
from kortravelweather.repository import (
    SourceRecordRow,
    SyncRunSourceRow,
    WeatherRepository,
    WeatherValueRow,
)


def _repository(database_url: str) -> WeatherRepository:
    repo = WeatherRepository(database_url)
    repo.create_schema()
    repo.upsert_location(WeatherLocation(
        location_id="batch-work", name="batch-work", latitude=37, longitude=127,
    ))
    return repo


def _record(key, clock, *, run_id=None, provider="p"):
    return {
        "source_record_key": key, "provider": provider, "dataset_key": "d",
        "source_entity_type": "test_response", "source_entity_id": key,
        "payload": {"key": key}, "fetched_at": clock, "run_id": run_id,
    }


def _fact(key, clock, metric="TEMP"):
    return WeatherValue(
        location_id="batch-work", provider="p", dataset_key="d", metric_key=metric,
        weather_domain="forecast", forecast_style=ForecastStyle.SHORT,
        target_at=clock, valid_at=clock, known_at=clock, collected_at=clock,
        value_number=Decimal("1"), source_record_key=key, payload={"raw": metric},
    )


def test_new_sources_hash_once_and_lineage_flush_together(
    weather_test_database_url, monkeypatch,
):
    repo = _repository(weather_test_database_url)
    try:
        clock = kst_now()
        run = repo.start_sync_run(provider="p", dataset_key="d", locations_total=1)
        records = [_record(f"source-{i}", clock, run_id=run.run_id) for i in range(12)]
        facts = [_fact(record["source_record_key"], clock) for record in records]
        hashes, copies, link_flushes = [], [], []
        original_hash = repository_module._payload_hash
        original_copy = WeatherValue.model_copy

        def hash_payload(payload):
            hashes.append(payload)
            return original_hash(payload)

        def copy_value(value, **kwargs):
            copies.append(kwargs)
            return original_copy(value, **kwargs)

        def before_flush(session, context, instances):
            count = sum(isinstance(row, SyncRunSourceRow) for row in session.new)
            if count:
                link_flushes.append(count)

        monkeypatch.setattr(repository_module, "_payload_hash", hash_payload)
        monkeypatch.setattr(WeatherValue, "model_copy", copy_value)
        event.listen(repo._session_factory, "before_flush", before_flush)
        try:
            assert repo.ingest_batch(source_records=records, values=facts) == 12
        finally:
            event.remove(repo._session_factory, "before_flush", before_flush)
        assert len(hashes) == 12
        assert copies == []
        assert link_flushes == [12]
        with repo._session_factory() as session:
            assert session.scalar(select(func.count()).select_from(SyncRunSourceRow)) == 12
            assert session.scalar(select(func.count()).select_from(WeatherValueRow)) == 12
    finally:
        repo.engine.dispose()


def test_alias_duplicate_lineage_and_fact_keep_canonical_identity(
    weather_test_database_url, monkeypatch,
):
    repo = _repository(weather_test_database_url)
    try:
        clock = kst_now()
        canonical = _record("canonical", clock)
        repo.ingest_batch(source_records=[canonical])
        run = repo.start_sync_run(provider="p", dataset_key="d", locations_total=1)
        alias = {**canonical, "source_record_key": "alias", "run_id": run.run_id}
        records = [alias, {**canonical, "run_id": run.run_id}, alias]
        first, duplicate = _fact("alias", clock), _fact("canonical", clock)
        original_copy = WeatherValue.model_copy
        copies = []

        def copy_value(value, **kwargs):
            copies.append(kwargs)
            return original_copy(value, **kwargs)

        monkeypatch.setattr(WeatherValue, "model_copy", copy_value)
        assert repo.ingest_batch(source_records=records, values=[first, duplicate]) == 1
        assert len(copies) == 1
        assert first.source_record_key == "alias"
        assert repo.ingest_batch(source_records=records, values=[first, duplicate]) == 0
        with repo._session_factory() as session:
            assert session.scalar(select(func.count()).select_from(SyncRunSourceRow)) == 1
            row = session.scalars(select(WeatherValueRow)).one()
            assert row.source_record_key == "canonical"
            assert row.value_id == duplicate.identity_key("canonical", target_at=clock)
    finally:
        repo.engine.dispose()


def test_lineage_provider_conflict_rolls_back_sources_and_pending_links(
    weather_test_database_url,
):
    repo = _repository(weather_test_database_url)
    try:
        clock = kst_now()
        run = repo.start_sync_run(provider="p", dataset_key="d", locations_total=1)
        records = [
            _record("valid", clock, run_id=run.run_id),
            _record("wrong-provider", clock, run_id=run.run_id, provider="q"),
        ]
        with pytest.raises(ValueError, match="provider/dataset"):
            repo.ingest_batch(source_records=records, values=[_fact("valid", clock)])
        with repo._session_factory() as session:
            assert session.scalar(select(func.count()).select_from(SourceRecordRow)) == 0
            assert session.scalar(select(func.count()).select_from(SyncRunSourceRow)) == 0
            assert session.scalar(select(func.count()).select_from(WeatherValueRow)) == 0
    finally:
        repo.engine.dispose()


def test_source_hash_conflict_stays_strict(weather_test_database_url):
    repo = _repository(weather_test_database_url)
    try:
        clock = kst_now()
        original = _record("canonical", clock)
        repo.ingest_batch(source_records=[original])
        with pytest.raises(ValueError, match="immutable source record"):
            repo.ingest_batch(
                source_records=[{**original, "payload": {"changed": True}}],
                values=[_fact("canonical", clock)],
            )
        with repo._session_factory() as session:
            assert session.scalar(select(func.count()).select_from(SourceRecordRow)) == 1
            assert session.scalar(select(func.count()).select_from(WeatherValueRow)) == 0
    finally:
        repo.engine.dispose()
