"""소유 테스트 PostgreSQL에서만 실행하는 복합키·참조 보존 회귀."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text, update

from kortravelweather import repository as module
from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.repository import (
    SourceRecordRow,
    WeatherCurrentValueRow,
    WeatherRepository,
    WeatherValueRow,
)
from kortravelweather.retention import ShortForecastPartitionRetained


@pytest.fixture
def repository(weather_test_database_url):
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    yield repo
    repo.engine.dispose()


NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)


def _value(valid_at, **changes):
    values = dict(
        location_id="retention-local", provider="python-kma-api",
        dataset_key="kma_short_forecast", weather_domain="kma_short_forecast",
        forecast_style="short", metric_key="TMP", value_number=1,
        valid_at=valid_at, target_at=valid_at,
        issued_at=NOW - timedelta(days=10), known_at=NOW, collected_at=NOW,
        source_record_key="retention-" + valid_at.isoformat(),
    )
    values.update(changes)
    return WeatherValue(**values)



def _record(repository, *values):
    for value in values:
        repository.record_source(
            source_record_key=value.source_record_key, provider=value.provider,
            dataset_key=value.dataset_key, source_entity_type="retention-fixture",
            source_entity_id=value.location_id, payload={"fixture": value.source_record_key},
            fetched_at=value.known_at,
        )

def test_purge_keeps_boundary_future_other_provider_and_raw(repository, monkeypatch):
    repository.upsert_location(WeatherLocation(
        location_id="retention-local", name="보존시험", latitude=37, longitude=127,
    ))
    cutoff = NOW - timedelta(hours=6)
    expired = _value(cutoff - timedelta(microseconds=1))
    kept = [
        _value(cutoff), _value(NOW + timedelta(days=1)),
        _value(cutoff - timedelta(hours=1), provider="other"),
        _value(cutoff - timedelta(hours=2), dataset_key="kma_ultra_short_forecast",
               weather_domain="kma_ultra_short_forecast", forecast_style="ultra_short"),
    ]
    monkeypatch.setattr(module, "kst_now", lambda: NOW - timedelta(days=1))
    _record(repository, expired, *kept)
    assert repository.upsert_values([expired, *kept]) == 5
    monkeypatch.setattr(module, "kst_now", lambda: NOW)
    dry = repository.purge_expired_short_forecast(cutoff=cutoff)
    assert dry["matched_in_batch"] == 1 and dry["facts_deleted"] == 0
    report = repository.purge_expired_short_forecast(cutoff=cutoff, dry_run=False)
    assert report["facts_deleted"] == report["pointers_deleted"] == 1
    assert report["remaining"] is False
    with repository._session_factory() as session:
        assert session.scalar(select(func.count()).select_from(WeatherValueRow)) == 4
        assert session.scalar(select(func.count()).select_from(SourceRecordRow)) == 5
    # 이미 삭제한 과거 응답을 다시 받더라도 fact/projection을 되살리지 않는다.
    assert repository.upsert_values([expired]) == 0
    with repository._session_factory() as session:
        assert session.scalar(select(func.count()).select_from(WeatherValueRow)) == 4


def test_same_value_id_other_known_at_pointer_is_not_deleted(repository, monkeypatch):
    repository.upsert_location(WeatherLocation(
        location_id="retention-local", name="복합키시험", latitude=37, longitude=127,
    ))
    cutoff = NOW - timedelta(hours=6)
    expired = _value(cutoff - timedelta(hours=1))
    monkeypatch.setattr(module, "kst_now", lambda: NOW - timedelta(days=1))
    _record(repository, expired)
    assert repository.upsert_values([expired]) == 1
    with repository._session_factory.begin() as session:
        old = session.scalars(select(WeatherValueRow)).one()
        fields = {column.key: getattr(old, column.key)
                  for column in WeatherValueRow.__mapper__.column_attrs}
        old_id = old.value_id
        fields.update(known_at=NOW + timedelta(seconds=1),
                      valid_at=NOW + timedelta(days=1), target_at=NOW + timedelta(days=1))
        session.add(WeatherValueRow(**fields))
        session.flush()
        session.execute(update(WeatherCurrentValueRow).values(
            known_at=fields["known_at"], target_at=fields["target_at"],
        ))
    monkeypatch.setattr(module, "kst_now", lambda: NOW)
    report = repository.purge_expired_short_forecast(cutoff=cutoff, dry_run=False)
    assert report["facts_deleted"] == 1 and report["pointers_deleted"] == 0
    with repository._session_factory() as session:
        assert session.get(WeatherValueRow, (old_id, NOW)) is None
        assert session.get(WeatherValueRow, (old_id, NOW + timedelta(seconds=1))) is not None
        assert session.get(WeatherCurrentValueRow, old_id) is not None


def test_old_known_partition_keeps_future_fact_pointer_and_source(repository, monkeypatch):
    from kortravelweather.partitions import ensure_partitions, kst_midnight, partition_name

    repository.upsert_location(WeatherLocation(
        location_id="retention-local", name="미래보존", latitude=37, longitude=127,
    ))
    old = NOW - timedelta(days=20)
    future = _value(
        NOW + timedelta(days=100), known_at=old, collected_at=old,
        issued_at=old - timedelta(days=1),
    )
    monkeypatch.setattr(module, "kst_now", lambda: old)
    with repository.engine.begin() as connection:
        ensure_partitions(connection, start=old.date(), end=old.date() + timedelta(days=1))
    _record(repository, future)
    assert repository.upsert_values([future]) == 1
    name = partition_name(old.date())
    # 실제 old-known fact를 보유한 dated partition의 DETACH/DROP 모두 거절한다.
    monkeypatch.setattr(module, "kst_now", lambda: NOW)
    with repository.engine.begin() as connection:
        connection.execute(text(f"LOCK TABLE {name} IN ACCESS EXCLUSIVE MODE"))
        with pytest.raises(ShortForecastPartitionRetained):
            WeatherRepository._preserve_short_forecast_partition(connection, name)
    # 전역 known_at pointer 정리의 정확 SQL 경로도 미래 fact를 보존한다.
    monkeypatch.setattr(repository, "_extend_forward_window", lambda **kwargs: ([], [], None))
    monkeypatch.setattr(module, "forward_partition_days", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repository, "_validate_fact_foreign_keys", lambda: True)
    report = repository.purge_expired_history(retention_days=7)
    assert name in report.partitions_retained_short_forecast
    with repository._session_factory() as session:
        assert session.scalar(select(func.count()).select_from(WeatherValueRow)) == 1
        assert session.scalar(select(func.count()).select_from(WeatherCurrentValueRow)) == 1
        assert session.scalar(select(func.count()).select_from(SourceRecordRow)) == 1
    assert kst_midnight(old.date()) < NOW - timedelta(days=7)


def test_future_late_reentry_preserves_source_clock_and_replay_pair(repository, monkeypatch):
    from kortravelweather.partitions import (
        DEFAULT_FLOOR_CONSTRAINT,
        DEFAULT_PARTITION,
        add_default_floor,
        dated_partition_bounds,
        default_partition_floor,
        validate_default_floor,
    )

    repository.upsert_location(WeatherLocation(
        location_id="retention-local", name="재입장시험", latitude=37, longitude=127,
    ))
    with repository.engine.begin() as connection:
        original_floor = default_partition_floor(connection)
        bounds = dated_partition_bounds(connection)
        floor = min(lower for lower, _ in bounds) - timedelta(days=30)
        connection.execute(text(
            f"ALTER TABLE {DEFAULT_PARTITION} DROP CONSTRAINT IF EXISTS "
            f"{DEFAULT_FLOOR_CONSTRAINT}"
        ))
        add_default_floor(connection, floor)
        validate_default_floor(connection)
    monkeypatch.setattr(module, "kst_now", lambda: NOW)
    try:
        for label, clock, replay, reentered in (
            ("first-default", floor - timedelta(days=1), False, False),
            ("old-replay", floor - timedelta(days=2), True, False),
            ("first-gap", floor + timedelta(days=10), False, True),
            ("replay-gap", floor + timedelta(days=11), True, True),
        ):
            item = _value(
                NOW + timedelta(days=100), source_record_key=label, known_at=clock,
                collected_at=clock, issued_at=clock - timedelta(hours=1), payload={"raw": label},
            )
            record = dict(
                source_record_key=label, provider=item.provider, dataset_key=item.dataset_key,
                source_entity_type="retention-fixture", source_entity_id=item.location_id,
                payload={"raw": label}, fetched_at=clock,
            )
            if replay:
                repository.record_source(**record)
            assert repository.ingest_batch(
                source_records=[] if replay else [record], values=[item],
            ) == 1
            expected = NOW if reentered else clock
            # 새 wall clock이나 늦은 replay도 동일 pair를 재사용해야 한다.
            monkeypatch.setattr(module, "kst_now", lambda: NOW + timedelta(hours=1))
            assert repository.upsert_values([item]) == 0
            with repository._session_factory() as session:
                rows = session.scalars(select(WeatherValueRow).where(
                    WeatherValueRow.source_record_key == label,
                )).all()
                assert len(rows) == 1
                row = rows[0]
                assert row.known_at == expected and row.collected_at == clock
                assert row.issued_at == item.issued_at and row.valid_at == item.valid_at
                assert row.payload == item.payload
                source = session.get(SourceRecordRow, label)
                assert source.fetched_at == clock and source.payload == record["payload"]
            monkeypatch.setattr(module, "kst_now", lambda: NOW)
    finally:
        with repository.engine.begin() as connection:
            connection.execute(text(
                f"ALTER TABLE {DEFAULT_PARTITION} DROP CONSTRAINT {DEFAULT_FLOOR_CONSTRAINT}"
            ))
            if original_floor is not None:
                add_default_floor(connection, original_floor)
                validate_default_floor(connection)
