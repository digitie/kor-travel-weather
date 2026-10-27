"""소유 테스트 PostgreSQL에서만 실행하는 복합키·참조 보존 회귀."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text, update

from kortravelweather import repository as module
from kortravelweather.models import KST, WeatherLocation, WeatherValue
from kortravelweather.repository import (
    SourceRecordRow,
    WeatherCurrentValueRow,
    WeatherRepository,
    WeatherValueRow,
)
from kortravelweather.retention import (
    SHORT_FORECAST_PREDICATE,
    ShortForecastKey,
    ShortForecastPartitionRetained,
    made_progress,
    run_short_forecast_retention,
)


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


_EXPIRED_ORDER = (
    "SELECT value_id FROM weather_values WHERE " + SHORT_FORECAST_PREDICATE
    + " AND valid_at < :cutoff ORDER BY valid_at, known_at, value_id"
)


def _expired_in_order(repository, cutoff):
    with repository.engine.connect() as connection:
        return list(connection.scalars(text(_EXPIRED_ORDER), {"cutoff": cutoff}))


def test_backlog_drains_across_runs_in_index_order_without_failure(repository, monkeypatch):
    """운영처럼 한 실행이 다 못 지우는 backlog를 위치에서 이어 여러 실행으로 비운다."""
    from kortravelweather.partitions import ensure_partitions, kst_midnight

    repository.upsert_location(WeatherLocation(
        location_id="retention-local", name="이어정리", latitude=37, longitude=127,
    ))
    first_day = (NOW - timedelta(days=3)).astimezone(KST).date()
    with repository.engine.begin() as connection:
        ensure_partitions(connection, start=first_day, end=first_day + timedelta(days=1))
    known = [kst_midnight(first_day + timedelta(days=day)) + timedelta(hours=2) for day in (0, 1)]
    cutoff = NOW - timedelta(hours=6)
    # 다섯 대상시각에 두 partition의 발표가 섞여 (valid_at, known_at) 동률이 batch 경계를 넘는다.
    expired = [
        _value(cutoff - timedelta(hours=1 + n % 5), known_at=known[n % 2],
               collected_at=known[n % 2], metric_key=f"M{n:02d}",
               source_record_key=f"drain-{n:02d}")
        for n in range(25)
    ]
    future = [
        _value(NOW + timedelta(hours=1 + n), known_at=known[1], collected_at=known[1],
               metric_key=f"F{n}", source_record_key=f"future-{n}")
        for n in range(3)
    ]
    monkeypatch.setattr(module, "kst_now", lambda: known[0] - timedelta(hours=1))
    _record(repository, *expired, *future)
    assert repository.upsert_values([*expired, *future]) == 28
    monkeypatch.setattr(module, "kst_now", lambda: NOW)
    order = _expired_in_order(repository, cutoff)
    assert len(order) == 25
    with repository.engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT count(DISTINCT tableoid) FROM weather_values WHERE valid_at < :cutoff"
        ), {"cutoff": cutoff}) == 2

    runs, after = [], None
    for _ in range(6):
        result = run_short_forecast_retention(
            repository, cutoff=cutoff, dry_run=False, batch_size=4, max_batches=2, after=after,
        )
        runs.append(result)
        # 각 실행은 인덱스 순서의 다음 구간만 지우고 건너뛴 행이 없다.
        deleted = sum(item["facts_deleted"] for item in runs)
        assert _expired_in_order(repository, cutoff) == order[deleted:]
        after = ShortForecastKey.from_metadata(result)
        if result["status"] == "complete_snapshot":
            break
    assert [item["facts_deleted"] for item in runs] == [8, 8, 8, 1]
    assert [item["pointers_deleted"] for item in runs] == [8, 8, 8, 1]
    assert [item["status"] for item in runs] == ["budget_exhausted"] * 3 + ["complete_snapshot"]
    # 남은 backlog가 있어도 지운 행이 있는 실행은 실패가 아니다.
    assert all(made_progress(item) for item in runs)
    assert [item["resumed"] for item in runs] == [False, True, True, True]
    assert all(item["backlog_lag_seconds"] > 0 for item in runs[:3])
    assert runs[-1]["backlog_lag_seconds"] == 0.0 and runs[-1]["remaining"] is False
    with repository._session_factory() as session:
        assert session.scalar(select(func.count()).select_from(WeatherValueRow)) == 3
        assert session.scalar(select(func.count()).select_from(WeatherCurrentValueRow)) == 3
        assert session.scalar(select(func.count()).select_from(SourceRecordRow)) == 28
    # 다 지운 뒤 같은 위치로 다시 실행해도 진척 없음이 아니라 완료다.
    again = run_short_forecast_retention(
        repository, cutoff=cutoff, dry_run=False, batch_size=4, max_batches=2, after=after,
    )
    assert again["status"] == "complete_snapshot" and again["facts_deleted"] == 0
    assert made_progress(again)


def test_locked_expired_fact_defers_the_batch_instead_of_skipping(
    repository, monkeypatch, weather_test_database_url,
):
    from sqlalchemy import create_engine

    repository.upsert_location(WeatherLocation(
        location_id="retention-local", name="잠금연기", latitude=37, longitude=127,
    ))
    cutoff = NOW - timedelta(hours=6)
    rows = [_value(cutoff - timedelta(hours=2)),
            _value(cutoff - timedelta(hours=1), metric_key="REH")]
    monkeypatch.setattr(module, "kst_now", lambda: NOW - timedelta(days=1))
    _record(repository, *rows)
    assert repository.upsert_values(rows) == 2
    monkeypatch.setattr(module, "kst_now", lambda: NOW)
    later = _expired_in_order(repository, cutoff)[1]
    other = create_engine(weather_test_database_url)
    try:
        with other.begin() as connection:
            # 수집의 FK 검사와 같은 KEY SHARE 잠금이 두 번째 후보에 걸려 있다.
            connection.execute(text(
                "SELECT 1 FROM weather_values WHERE value_id = :id FOR KEY SHARE"
            ), {"id": later})
            report = repository.purge_expired_short_forecast(cutoff=cutoff, dry_run=False)
            assert report["deferred"] is True and report["error_class"] == "LockNotAvailable"
            assert report["facts_deleted"] == 0 and report["last_key"] is None
        # 앞 행만 지우고 잠긴 행을 건너뛰면 위치 뒤에 행이 남는다. 연기는 둘 다 보존한다.
        assert len(_expired_in_order(repository, cutoff)) == 2
        report = repository.purge_expired_short_forecast(cutoff=cutoff, dry_run=False)
        assert report["facts_deleted"] == report["pointers_deleted"] == 2
        assert report["remaining"] is False and _expired_in_order(repository, cutoff) == []
    finally:
        other.dispose()


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
        # 재입장 시각(kst_now)은 dated partition 안이어야 한다. create_schema는 실제 오늘
        # 기준 ±3일만 만들므로 고정 NOW(2026-10-16)는 10-20부터 그 범위를 벗어났다.
        now = min(lower for lower, _ in bounds) + timedelta(hours=12)
        connection.execute(text(
            f"ALTER TABLE {DEFAULT_PARTITION} DROP CONSTRAINT IF EXISTS "
            f"{DEFAULT_FLOOR_CONSTRAINT}"
        ))
        add_default_floor(connection, floor)
        validate_default_floor(connection)
    monkeypatch.setattr(module, "kst_now", lambda: now)
    try:
        for label, clock, replay, reentered in (
            ("first-default", floor - timedelta(days=1), False, False),
            ("old-replay", floor - timedelta(days=2), True, False),
            ("first-gap", floor + timedelta(days=10), False, True),
            ("replay-gap", floor + timedelta(days=11), True, True),
        ):
            item = _value(
                now + timedelta(days=100), source_record_key=label, known_at=clock,
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
            expected = now if reentered else clock
            # 새 wall clock이나 늦은 replay도 동일 pair를 재사용해야 한다.
            monkeypatch.setattr(module, "kst_now", lambda: now + timedelta(hours=1))
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
            monkeypatch.setattr(module, "kst_now", lambda: now)
    finally:
        with repository.engine.begin() as connection:
            connection.execute(text(
                f"ALTER TABLE {DEFAULT_PARTITION} DROP CONSTRAINT {DEFAULT_FLOOR_CONSTRAINT}"
            ))
            if original_floor is not None:
                add_default_floor(connection, original_floor)
                validate_default_floor(connection)
