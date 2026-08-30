from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from kortravelweather.models import KST, ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.providers.kma import (
    KmaForecastRow,
    KmaNowcastRow,
    short_forecast_to_weather_values,
)
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)


def test_sqlite_database_urls_are_rejected() -> None:
    with pytest.raises(ValueError, match="PostgreSQL"):
        WeatherRepository("sqlite:///:memory:")
    with pytest.raises(ValueError, match="postgresql"):
        WeatherSettings(_env_file=None, database_url="sqlite:///:memory:")


def _location() -> WeatherLocation:
    return WeatherLocation(location_id="x", name="X", latitude=37, longitude=127, nx=1, ny=1)


def test_kma_raw_aliases_and_qualifier() -> None:
    assert (
        KmaForecastRow.from_raw(
            {
                "baseDate": "20260101",
                "baseTime": "0000",
                "fcstDate": "20260101",
                "fcstTime": "0100",
                "nx": 1,
                "ny": 1,
                "category": "RN1",
                "fcstValue": "1mm 미만",
            }
        ).fcst_value
        == "1mm 미만"
    )
    assert (
        KmaNowcastRow.from_raw(
            {
                "baseDate": "20260101",
                "baseTime": "0000",
                "nx": 1,
                "ny": 1,
                "category": "T1H",
                "obsrValue": "2",
            }
        ).obsr_value
        == "2"
    )
    value = short_forecast_to_weather_values(
        [
            {
                "baseDate": "20260101",
                "baseTime": "0000",
                "fcstDate": "20260101",
                "fcstTime": "0100",
                "nx": 1,
                "ny": 1,
                "category": "RN1",
                "fcstValue": "1mm 미만",
            }
        ],
        location_id="x",
    )[0]
    assert value.value_number == 0 and value.value_text == "1mm 미만"


def test_repository_is_immutable_and_timezone_safe(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    repo.upsert_location(_location())
    issued = datetime(2026, 1, 1, tzinfo=UTC)
    value = WeatherValue(
        location_id="x",
        provider="p",
        dataset_key="d",
        weather_domain="d",
        forecast_style=ForecastStyle.SHORT,
        metric_key="TMP",
        issued_at=issued,
        valid_at=issued,
        target_at=issued,
        value_number=Decimal("1"),
        payload={"row": 1},
        source_record_key="sr-1",
    )
    repo.record_source(
        source_record_key="sr-1",
        provider="p",
        dataset_key="d",
        source_entity_type="weather_response",
        source_entity_id="x",
        payload={"rows": [1]},
    )
    repo.upsert_values([value])
    assert repo.upsert_values([value]) == 0
    loaded = repo.latest_values("x")[0]
    assert loaded.issued_at is not None and loaded.issued_at.tzinfo is not None
    assert loaded.target_at.tzinfo is not None
    with pytest.raises(ValueError, match="immutable weather fact"):
        repo.upsert_values([value.model_copy(update={"payload": {"row": 2}})])
    with pytest.raises(ValueError, match="unit"):
        repo.upsert_values([value.model_copy(update={"unit": "bogus"})])


def test_value_id_is_stable_across_kst_postgresql_round_trip(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    repo.upsert_location(_location())
    target = datetime(2026, 1, 1, 9, tzinfo=KST)
    value = WeatherValue(
        location_id="x",
        provider="p",
        dataset_key="d",
        weather_domain="d",
        forecast_style=ForecastStyle.SHORT,
        metric_key="TMP",
        target_at=target,
        value_number=Decimal("1"),
        source_record_key="kst-source",
    )
    repo.record_source(
        source_record_key="kst-source",
        provider="p",
        dataset_key="d",
        source_entity_type="weather_response",
        source_entity_id="x",
        payload={"rows": []},
    )
    repo.upsert_values([value])
    loaded = repo.latest_values("x")[0]
    assert loaded.identity_key() == value.identity_key()


def test_response_source_is_shared_by_metrics_without_payload_corruption(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    repo.upsert_location(_location())
    target = datetime(2026, 1, 1, tzinfo=UTC)
    repo.record_source(
        source_record_key="response-key",
        provider="p",
        dataset_key="d",
        source_entity_type="weather_response",
        source_entity_id="grid:1:1",
        payload={"rows": [{"TMP": "1", "REH": "40"}], "request": {"nx": 1, "ny": 1}},
    )
    values = [
        WeatherValue(
            location_id="x",
            provider="p",
            dataset_key="d",
            weather_domain="d",
            forecast_style=ForecastStyle.SHORT,
            metric_key=metric,
            target_at=target,
            value_number=Decimal(number),
            payload={"metric": metric, "value": number},
            source_record_key="response-key",
        )
        for metric, number in (("TMP", "1"), ("REH", "40"))
    ]
    assert repo.upsert_values(values) == 2
    source = repo.get_source_record("response-key")
    assert source is not None
    assert source["payload"]["request"] == {"nx": 1, "ny": 1}
    assert len(repo.timeline("x", include_revisions=True)) == 2


def test_location_anchor_cannot_move_after_fact(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    repo.upsert_location(_location())
    target = datetime(2026, 1, 1, tzinfo=UTC)
    repo.record_source(
        source_record_key="anchor-source",
        provider="p",
        dataset_key="d",
        source_entity_type="weather_response",
        source_entity_id="x",
        payload={"rows": []},
    )
    repo.upsert_values(
        [
            WeatherValue(
                location_id="x",
                provider="p",
                dataset_key="d",
                weather_domain="d",
                forecast_style=ForecastStyle.SHORT,
                metric_key="TMP",
                target_at=target,
                value_number=Decimal("1"),
                source_record_key="anchor-source",
            )
        ]
    )
    with pytest.raises(ValueError, match="좌표/grid"):
        repo.upsert_location(_location().model_copy(update={"nx": 2}))


def test_location_patch_preserves_independent_concurrent_fields(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    repo.upsert_location(_location())
    repo.patch_location("x", {"name": "renamed"})
    repo.patch_location("x", {"region_code": "11"})
    loaded = repo.get_location("x")
    assert loaded is not None
    assert loaded.name == "renamed"
    assert loaded.region_code == "11"


def test_nearest_locations_scans_full_enabled_catalog(tmp_path, monkeypatch) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    observed: dict[str, object] = {}

    def all_locations(*, enabled_only: bool, limit: int | None) -> list[WeatherLocation]:
        observed.update(enabled_only=enabled_only, limit=limit)
        return [
            WeatherLocation(
                location_id="far",
                name="Far",
                latitude=37.5,
                longitude=127.5,
                nx=1,
                ny=1,
            )
        ]

    monkeypatch.setattr(repo, "list_locations", all_locations)
    repo.nearest_locations(37.0, 127.0, radius_km=100)
    assert observed == {"enabled_only": True, "limit": None}


def test_location_coordinates_match_numeric_storage_precision(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    location = WeatherLocation(
        location_id="precise",
        name="Precise",
        latitude=37.1234567,
        longitude=127.1234567,
        nx=1,
        ny=1,
    )

    assert location.latitude == 37.123457
    assert location.longitude == 127.123457
    repo.upsert_location(location)
    loaded = repo.get_location("precise")
    assert loaded is not None
    assert repo.upsert_location(loaded) == location


def test_replayed_decimal_is_canonical_at_database_precision(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    repo.upsert_location(_location())
    repo.record_source(
        source_record_key="decimal-source",
        provider="p",
        dataset_key="d",
        source_entity_type="weather_response",
        source_entity_id="x",
        payload={"rows": []},
    )
    value = WeatherValue(
        location_id="x",
        provider="p",
        dataset_key="d",
        weather_domain="d",
        forecast_style=ForecastStyle.SHORT,
        metric_key="TMP",
        target_at=datetime(2026, 1, 1, tzinfo=UTC),
        value_number=Decimal("1.23456"),
        source_record_key="decimal-source",
    )
    assert value.value_number == Decimal("1.2346")
    assert repo.upsert_values([value]) == 1
    assert repo.upsert_values([value]) == 0


def test_local_source_identity_normalizes_equivalent_timezones(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    repo.upsert_location(_location())
    common = dict(
        location_id="x",
        provider="p",
        dataset_key="d",
        weather_domain="d",
        forecast_style=ForecastStyle.SHORT,
        metric_key="TMP",
        value_number=Decimal("1"),
    )
    first = WeatherValue(**common, target_at=datetime(2026, 1, 1, 9, tzinfo=KST))
    second = WeatherValue(**common, target_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert repo.upsert_values([first]) == 1
    assert repo.upsert_values([second]) == 0


def test_late_finish_cannot_resurrect_reconciled_run(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    run = repo.start_sync_run(provider="p", dataset_key="d")
    with repo.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE weather_sync_runs SET started_at = '2020-01-01 00:00:00+00', "
                "heartbeat_at = '2020-01-01 00:00:00+00' WHERE run_id = :run_id"
            ),
            {"run_id": run.run_id},
        )
    assert repo.reconcile_stale_sync_runs(max_age_minutes=1) == 1
    late = repo.finish_sync_run(run.run_id, status="success", values_loaded=99)
    assert late.status == "failed"
    assert late.values_loaded == 0


def test_sync_run_heartbeat_keeps_active_worker_from_stale_recovery(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    run = repo.start_sync_run(provider="p", dataset_key="d")
    assert repo.heartbeat_sync_run(run.run_id)
    with repo.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE weather_sync_runs SET heartbeat_at = '2020-01-01 00:00:00+00' "
                "WHERE run_id = :run_id"
            ),
            {"run_id": run.run_id},
        )
    assert repo.reconcile_stale_sync_runs(max_age_minutes=1) == 1
    assert repo.heartbeat_sync_run(run.run_id) is False


def test_source_lineage_rejects_other_grid(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    repo.upsert_location(
        WeatherLocation(location_id="other", name="Other", latitude=37, longitude=127, nx=2, ny=2)
    )
    repo.record_source(
        source_record_key="grid-source",
        provider="p",
        dataset_key="d",
        source_entity_type="weather_response",
        source_entity_id="grid:1:1",
        payload={"rows": []},
    )
    value = WeatherValue(
        location_id="other",
        provider="p",
        dataset_key="d",
        weather_domain="d",
        forecast_style=ForecastStyle.SHORT,
        metric_key="TMP",
        target_at=datetime(2026, 1, 1, tzinfo=UTC),
        value_number=Decimal("1"),
        source_record_key="grid-source",
    )
    with pytest.raises(ValueError, match="entity"):
        repo.upsert_values([value])


def test_sync_run_source_provider_and_dataset_must_match(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    run = repo.start_sync_run(provider="p", dataset_key="d")
    with pytest.raises(ValueError, match="provider/dataset"):
        repo.ingest_batch(
            source_records=[
                {
                    "source_record_key": "wrong-run-source",
                    "provider": "other",
                    "dataset_key": "other",
                    "source_entity_type": "weather_response",
                    "source_entity_id": "x",
                    "payload": {"rows": []},
                    "run_id": run.run_id,
                }
            ]
        )
    assert repo.list_sync_run_sources(run.run_id) == []
