from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from kortravelweather.models import KST, ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.providers.kma import (
    KmaForecastRow,
    KmaNowcastRow,
    short_forecast_to_weather_values,
)
from kortravelweather.repository import PURGE_SOURCE_RECORDS_SQL, WeatherRepository
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


def test_source_identity_replay_reuses_existing_primary_key(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    repo.upsert_location(_location())
    payload = {"rows": [{"TMP": "1"}]}
    repo.record_source(
        source_record_key="legacy-response-key",
        provider="p",
        dataset_key="d",
        source_entity_type="weather_response",
        source_entity_id="x",
        payload=payload,
    )
    value = WeatherValue(
        location_id="x",
        provider="p",
        dataset_key="d",
        weather_domain="d",
        forecast_style=ForecastStyle.SHORT,
        metric_key="TMP",
        target_at=datetime(2026, 1, 1, tzinfo=UTC),
        value_number=Decimal("1"),
        payload={"metric": "TMP"},
        source_record_key="new-response-key",
    )

    assert repo.ingest_batch(
        source_records=[
            {
                "source_record_key": "new-response-key",
                "provider": "p",
                "dataset_key": "d",
                "source_entity_type": "weather_response",
                "source_entity_id": "x",
                "payload": payload,
            }
        ],
        values=[value],
    ) == 1
    assert repo.get_source_record("new-response-key") is None
    assert repo.timeline("x", include_revisions=True)[0].source_record_key == "legacy-response-key"


def test_source_identity_replay_keeps_legacy_fact_payload(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    repo.upsert_location(_location())
    source_payload = {"rows": [{"ALERT": "호우주의보 발표"}]}
    repo.record_source(
        source_record_key="legacy-alert-source",
        provider="p",
        dataset_key="d",
        source_entity_type="weather_response",
        source_entity_id="x",
        payload=source_payload,
    )
    target = datetime(2026, 1, 1, tzinfo=UTC)
    legacy = WeatherValue(
        location_id="x",
        provider="p",
        dataset_key="d",
        weather_domain="d",
        forecast_style=ForecastStyle.SHORT,
        metric_key="ALERT",
        target_at=target,
        value_text="호우주의보 발표",
        payload={"legacy": True},
        source_record_key="legacy-alert-source",
    )
    assert repo.upsert_values([legacy]) == 1

    replay = legacy.model_copy(
        update={
            "source_record_key": "new-alert-source",
            "payload": {"alert_action": "active", "legacy": True},
        }
    )
    assert (
        repo.ingest_batch(
            source_records=[
                {
                    "source_record_key": "new-alert-source",
                    "provider": "p",
                    "dataset_key": "d",
                    "source_entity_type": "weather_response",
                    "source_entity_id": "x",
                    "payload": source_payload,
                }
            ],
            values=[replay],
        )
        == 0
    )
    assert repo.timeline("x", include_revisions=True)[0].payload == {"legacy": True}


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


def test_get_locations_by_ids_uses_primary_key_and_filters_disabled(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    repo.upsert_location(_location())
    repo.upsert_location(
        WeatherLocation(
            location_id="disabled",
            name="Disabled",
            latitude=37.1,
            longitude=127.1,
            enabled=False,
        )
    )

    loaded = repo.get_locations_by_ids(
        ["missing", "disabled", "x", "x"], enabled_only=True
    )

    assert [location.location_id for location in loaded] == ["x"]
    assert repo.get_locations_by_ids(["disabled"], enabled_only=False)[0].location_id == "disabled"


def test_nearest_locations_uses_sql_bbox_exact_distance_and_limit(tmp_path, monkeypatch) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    for location in (
        WeatherLocation(
            location_id="nearest-1",
            name="Nearest 1",
            latitude=37.01,
            longitude=127.01,
        ),
        WeatherLocation(
            location_id="nearest-2",
            name="Nearest 2",
            latitude=37.02,
            longitude=127.01,
        ),
        # This point is inside the rectangular prefilter but outside the
        # requested circle; the SQL Haversine predicate must remove it.
        WeatherLocation(
            location_id="bbox-only",
            name="Bbox only",
            latitude=37.04,
            longitude=127.05,
        ),
        WeatherLocation(
            location_id="nearest-disabled",
            name="Disabled",
            latitude=37.001,
            longitude=127.001,
            enabled=False,
        ),
    ):
        repo.upsert_location(location)

    def unexpected_catalog_scan(*args, **kwargs):
        raise AssertionError("nearest query must not materialize the location catalog")

    monkeypatch.setattr(repo, "list_locations", unexpected_catalog_scan)
    rows = repo.nearest_locations(37.0, 127.0, radius_km=5, limit=2)

    assert [location.location_id for location, _ in rows] == ["nearest-1", "nearest-2"]
    assert rows[0][1] == pytest.approx(1.423, abs=0.02)
    assert all(distance <= 5 for _, distance in rows)


def test_current_projection_hides_append_only_revisions_from_bundles(tmp_path) -> None:
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    repo.upsert_location(_location())
    target = datetime(2026, 9, 1, 12, tzinfo=UTC)
    source_records = []
    facts = []
    for revision in range(64):
        source_key = f"projection-revision-{revision}"
        source_records.append(
            {
                "source_record_key": source_key,
                "provider": "p",
                "dataset_key": "d",
                "source_entity_type": "weather_response",
                "source_entity_id": "x",
                "payload": {"revision": revision},
                "fetched_at": datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=revision),
            }
        )
        facts.append(
            WeatherValue(
                location_id="x",
                provider="p",
                dataset_key="d",
                weather_domain="weather",
                forecast_style=ForecastStyle.SHORT,
                metric_key="TMP",
                target_at=target,
                value_number=Decimal(revision),
                payload={"revision": revision},
                source_record_key=source_key,
            )
        )

    assert repo.ingest_batch(source_records=source_records, values=facts) == 64
    latest = repo.latest_values_many(["x"], limit_per_location=100)["x"]
    timeline = repo.timeline_many(["x"], limit_per_location=100)["x"]
    forecast = repo.timeline("x", limit=100)

    assert len(latest) == len(timeline) == len(forecast) == 1
    assert latest[0].value_number == Decimal("63.0000")
    assert timeline[0].value_number == Decimal("63.0000")
    assert forecast[0].value_number == Decimal("63.0000")
    with repo.engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM weather_current_values "
                "WHERE location_id = 'x'"
            )
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM weather_values WHERE location_id = 'x'")
        ).scalar_one() == 64


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


def test_bundle_read_joins_the_fact_table_only_for_the_alert_window() -> None:
    """The join exists for one predicate; losing it compiles to a cross join.

    ``valid_until`` is the only sort or filter column still on the fact table.
    A future predicate added to the alert branch without the join would pair
    every projected row with every fact before the limit -- unbounded and wrong,
    with no exception to notice.
    """
    import contextlib
    from datetime import UTC, datetime

    from sqlalchemy.dialects import postgresql

    from kortravelweather.repository import WeatherRepository

    repository = WeatherRepository.__new__(WeatherRepository)
    captured: list[str] = []

    class _Recorder:
        def execute(self, statement, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            captured.append(
                str(statement.compile(dialect=postgresql.dialect()))
            )
            raise _Stop

        def scalars(self, statement, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            raise _Stop

    class _Stop(Exception):
        pass

    def compiled(**kwargs) -> str:
        captured.clear()
        with contextlib.suppress(_Stop):
            WeatherRepository._current_value_models_many(
                repository,
                _Recorder(),
                ["anywhere"],
                limit_per_location=5,
                prefer_current=True,
                **kwargs,
            )
        assert captured, "no statement was compiled"
        return captured[0]

    without_alerts = compiled()
    assert "weather_values" not in without_alerts, (
        "the ordinary bundle read must not touch the fact table before its limit"
    )

    with_alerts = compiled(alerts_only=True, alert_active_at=datetime.now(UTC))
    assert "JOIN weather_values" in with_alerts
    assert "valid_until" in with_alerts


def test_projection_sort_keys_move_with_the_pointer() -> None:
    """The one invariant this denormalisation introduces.

    ``weather_current_values`` carries a copy of the fact's ``known_at`` and
    ``source_record_key`` so a bundle read can order a location's slice without
    joining.  If the pointer moves to a newer revision and the copies stay
    behind, every read orders on a fact that is no longer there -- silently, and
    the LIMIT then drops the wrong rows.
    """
    from datetime import UTC, datetime
    from decimal import Decimal

    from sqlalchemy import text

    from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue
    from kortravelweather.repository import WeatherRepository

    repository = WeatherRepository(TEST_DATABASE_URL)
    repository.create_schema()
    repository.upsert_location(
        WeatherLocation(
            location_id="pointer", name="포인터", latitude=37.5, longitude=127.0
        )
    )

    def publish(suffix: str, known_at: datetime, value: str) -> None:
        repository.record_source(
            source_record_key=f"pointer-{suffix}",
            provider="p",
            dataset_key="d",
            source_entity_type="weather_response",
            source_entity_id="pointer",
            # A replayed identical payload reuses the existing key, so each
            # revision needs a distinguishable response body.
            payload={"rows": [], "revision": suffix},
        )
        repository.upsert_values(
            [
                WeatherValue(
                    location_id="pointer",
                    provider="p",
                    dataset_key="d",
                    weather_domain="weather",
                    forecast_style=ForecastStyle.OBSERVED,
                    metric_key="TMP",
                    target_at=datetime(2026, 1, 1, tzinfo=UTC),
                    known_at=known_at,
                    value_number=Decimal(value),
                    source_record_key=f"pointer-{suffix}",
                )
            ]
        )

    def stored() -> tuple:
        with repository.engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT cv.value_id = wv.value_id, "
                    "       cv.known_at = wv.known_at, "
                    "       cv.source_record_key = wv.source_record_key "
                    "FROM weather_current_values cv "
                    "JOIN weather_values wv ON wv.value_id = cv.value_id "
                    "WHERE cv.location_id = 'pointer'"
                )
            ).one()

    publish("old", datetime(2026, 1, 1, tzinfo=UTC), "1")
    assert stored() == (True, True, True)

    # A later revision of the same logical point moves the pointer.
    publish("new", datetime(2026, 1, 2, tzinfo=UTC), "2")
    assert stored() == (True, True, True), (
        "the pointer moved but its denormalised sort keys did not follow"
    )


def _seed_history(repository: WeatherRepository, suffix: str, known_at: datetime) -> str:
    """Publish one fact as its own logical point and return its source key."""
    source_key = f"retention-{suffix}"
    repository.record_source(
        source_record_key=source_key,
        provider="p",
        dataset_key="d",
        source_entity_type="weather_response",
        source_entity_id="retention",
        payload={"rows": [], "revision": suffix},
    )
    repository.upsert_values(
        [
            WeatherValue(
                location_id="retention",
                provider="p",
                dataset_key="d",
                weather_domain="weather",
                forecast_style=ForecastStyle.OBSERVED,
                # A distinct metric per revision keeps these separate logical
                # points, so the projection pins each one rather than only the
                # newest.
                metric_key=f"M{suffix}",
                target_at=datetime(2026, 1, 1, tzinfo=UTC),
                known_at=known_at,
                value_number=Decimal(1),
                source_record_key=source_key,
            )
        ]
    )
    return source_key


def _age_history(repository: WeatherRepository, source_key: str, known_at: datetime) -> None:
    """Move a published fact into the past, partition and pointer with it.

    ``known_at`` is the partition key and half the projection's foreign key, so
    ageing a fact means moving it between partitions *and* keeping the pointer
    consistent.  The immutability trigger refuses UPDATE, and the pointer has to
    be lifted before the fact moves; putting both back afterwards is what the
    real ingest path does when a logical point receives a new revision.
    """
    from kortravelweather.partitions import ensure_partitions

    with repository.engine.begin() as connection:
        # Without a partition for the target day the row lands in DEFAULT,
        # which retention never drops -- the test would then be checking the
        # safety net rather than the mechanism.
        ensure_partitions(connection, start=known_at.date(), end=known_at.date())
        pointers = connection.execute(
            text(
                "SELECT value_id FROM weather_current_values cv "
                "WHERE EXISTS (SELECT 1 FROM weather_values wv "
                "              WHERE wv.value_id = cv.value_id "
                "                AND wv.source_record_key = :key)"
            ),
            {"key": source_key},
        ).scalars().all()
        connection.execute(
            text("DELETE FROM weather_current_values WHERE value_id = ANY(:ids)"),
            {"ids": list(pointers)},
        )
        for table, column in (
            ("weather_values", "known_at"),
            ("weather_source_records", "fetched_at"),
        ):
            connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
            connection.execute(
                text(f"UPDATE {table} SET {column} = :at WHERE source_record_key = :key"),
                {"at": known_at, "key": source_key},
            )
            connection.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))
        connection.execute(
            text(
                "INSERT INTO weather_current_values (value_id, location_id, provider, "
                "dataset_key, weather_domain, forecast_style, metric_key, target_at, "
                "known_at, source_record_key) "
                "SELECT value_id, location_id, provider, dataset_key, weather_domain, "
                "forecast_style, metric_key, target_at, known_at, source_record_key "
                "FROM weather_values WHERE value_id = ANY(:ids)"
            ),
            {"ids": list(pointers)},
        )


def _retention_repository() -> WeatherRepository:
    repository = WeatherRepository(TEST_DATABASE_URL)
    repository.create_schema()
    repository.upsert_location(
        WeatherLocation(location_id="retention", name="보존", latitude=37.5, longitude=127.0)
    )
    return repository


def _partitions(repository: WeatherRepository) -> list[str]:
    from kortravelweather.partitions import existing_partitions

    with repository.engine.connect() as connection:
        return [name for name, _ in existing_partitions(connection)]


def test_a_day_outside_the_window_is_dropped_whole() -> None:
    """Retention removes a partition, not three million rows.

    The window is expressed in days and the table is partitioned by day, so
    ageing out is a catalog change: the day's partition goes, and with it every
    fact in it. Nothing is scanned and nothing is vacuumed.
    """
    repository = _retention_repository()
    now = datetime.now(KST)
    fresh = _seed_history(repository, "fresh", now)
    stale = _seed_history(repository, "stale", now)
    _age_history(repository, stale, now - timedelta(days=30))
    aged_day = (now - timedelta(days=30)).strftime("%Y%m%d")
    assert any(name.endswith(aged_day) for name in _partitions(repository)), (
        "the aged fact is not in a dated partition, so this would be testing "
        "the DEFAULT safety net rather than the drop"
    )

    report = repository.purge_expired_history(retention_days=2)

    assert report.partitions_dropped, "nothing was dropped, so nothing aged out"
    assert report.rows_outside_any_partition == 0
    with repository.engine.connect() as connection:
        remaining = list(
            connection.execute(
                text("SELECT source_record_key FROM weather_values")
            ).scalars()
        )
    assert remaining == [fresh], f"expected only {stale} to go"


def test_a_location_that_went_quiet_loses_its_current_value() -> None:
    """The deliberate consequence of dropping days rather than rows.

    The batched delete this replaced spared any fact the projection still
    pointed at, so a station that stopped reporting kept its last reading for
    ever. A partition cannot be dropped selectively, so the pointer goes first
    and the location reads as having no current data -- which is what "we keep
    two days" actually means, and is worth being explicit about rather than
    discovering from an empty map.
    """
    repository = _retention_repository()
    source_key = _seed_history(repository, "quiet", datetime.now(KST))
    _age_history(repository, source_key, datetime.now(KST) - timedelta(days=400))

    report = repository.purge_expired_history(retention_days=2)

    assert report.pointers_deleted == 1
    with repository.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM weather_current_values")
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(text("SELECT count(*) FROM weather_values")).scalar_one()
            == 0
        )


def test_tomorrow_has_a_partition_before_today_is_dropped() -> None:
    """A missing partition is an insert that fails at midnight.

    The maintenance job is the only thing that creates them, and it runs once a
    day, so it has to look forward further than it looks back.
    """
    repository = _retention_repository()
    _seed_history(repository, "today", datetime.now(KST))

    repository.purge_expired_history(retention_days=2, ahead_days=5)

    names = set(_partitions(repository))
    for offset in range(0, 6):
        day = (datetime.now(KST) + timedelta(days=offset)).strftime("%Y%m%d")
        assert f"weather_values_{day}" in names, f"no partition for +{offset} days"


def test_rows_that_landed_outside_every_partition_are_reported() -> None:
    """The DEFAULT partition is a safety net, and a silent one is a leak.

    Rows there are never dropped by retention, so a non-zero count is the table
    quietly starting to grow again -- the exact failure partitioning exists to
    end. Nothing else in the report distinguishes it from a healthy run.
    """
    repository = _retention_repository()
    _seed_history(repository, "anchor", datetime.now(KST))
    # Inserted directly, because the ingest path cannot produce this row: it
    # takes ``known_at`` from the source record's fetch time, so a caller
    # cannot date a fact into a range with no partition.  The row this test
    # needs is the one a *missing* partition produces -- a day the maintenance
    # job failed to create -- and raw SQL is the only way to stage it.
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO weather_values (value_id, location_id, provider, "
                "dataset_key, weather_domain, forecast_style, metric_key, target_at, "
                "known_at, normalization_version, payload, collected_at, "
                "source_record_key, value_number) "
                "SELECT 'stranded', location_id, provider, dataset_key, weather_domain, "
                "forecast_style, 'STRANDED', target_at, "
                "now() + interval '900 days', normalization_version, payload, "
                "collected_at, source_record_key, 1 FROM weather_values LIMIT 1"
            )
        )

    report = repository.purge_expired_history(retention_days=2)

    assert report.rows_outside_any_partition == 1


def test_history_stays_immutable_outside_the_purge() -> None:
    """The escape hatch opens for one transaction, for DELETE, and no further."""
    repository = _retention_repository()
    _seed_history(repository, "kept", datetime.now(KST))

    with repository.engine.begin() as connection, pytest.raises(Exception, match="immutable"):
        connection.execute(text("DELETE FROM weather_values"))
    with repository.engine.begin() as connection, pytest.raises(Exception, match="immutable"):
        connection.execute(text("UPDATE weather_values SET value_number = 2"))
    # Asking for the purge permission must not also open UPDATE.
    with repository.engine.begin() as connection, pytest.raises(Exception, match="immutable"):
        connection.execute(text("SET LOCAL kortravelweather.purge = 'on'"))
        connection.execute(text("UPDATE weather_values SET value_number = 2"))
    with repository.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM weather_values")).scalar_one() == 1
        )


def test_the_purge_permission_does_not_outlive_the_purge() -> None:
    """A real purge must leave the tables as locked as it found them.

    The permission is granted with ``SET LOCAL``, which expires with the
    transaction. Plain ``SET`` would look identical here -- same purge, same
    result -- while quietly leaving DELETE open on a pooled connection for
    everything that borrowed it afterwards.
    """
    repository = _retention_repository()
    _age_history(
        repository,
        _seed_history(repository, "gone", datetime.now(KST)),
        datetime.now(KST) - timedelta(days=30),
    )

    repository.purge_expired_history(retention_days=2)

    _seed_history(repository, "after", datetime.now(KST))
    with repository.engine.begin() as connection, pytest.raises(Exception, match="immutable"):
        connection.execute(text("DELETE FROM weather_values"))


def test_the_purge_lookup_is_served_by_its_indexes() -> None:
    """Plan the real "still cited?" check and refuse a plan that scans.

    Both ``NOT EXISTS`` subqueries key on ``source_record_key``, which appears
    nowhere else as a leading index column, so this check used to fall back to
    a full scan per candidate row -- of ``weather_values``, the biggest table
    in the database. That scan ran inside the same transaction as the
    partition maintenance ahead of it, so it held every lock that transaction
    had taken for as long as it ran, which is what once stalled every
    ingestion job that reads ``weather_locations`` for as long as the scan
    took.

    Migration 0016 and the matching ORM indexes are what closes that. This
    test binds the exact query text ``purge_expired_history`` runs
    (``PURGE_SOURCE_RECORDS_SQL``) to those indexes, so an edit to either the
    query or the indexes that breaks the match fails here instead of in
    production.
    """
    repository = _retention_repository()
    with repository.engine.connect() as connection:
        transaction = connection.begin()
        try:
            # weather_values' identity constraint and weather_sync_run_sources'
            # primary key both carry source_record_key as a trailing column,
            # so PostgreSQL can serve the lookup from either of them without
            # ever touching the two indexes this test exists to check -- and
            # at this fixture's row count, cost estimates cannot tell them
            # apart, so it will. Dropping both for the plan only, inside a
            # transaction this test never commits, is what forces the
            # question to be "can *this* index serve the lookup" rather than
            # "can any index".
            connection.execute(
                text(
                    "ALTER TABLE weather_values "
                    "DROP CONSTRAINT uq_weather_values_identity"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE weather_sync_run_sources "
                    "DROP CONSTRAINT weather_sync_run_sources_pkey"
                )
            )
            # Not a performance assertion -- the fixture is far too small for
            # the planner to care. Disabling the alternative is what makes
            # the question "can this index serve the lookup" answerable at
            # all once the competing indexes above are out of the way.
            connection.execute(text("SET enable_seqscan = off"))
            plan = "\n".join(
                line
                for (line,) in connection.execute(
                    text("EXPLAIN " + PURGE_SOURCE_RECORDS_SQL),
                    {"cutoff": datetime.now(KST)},
                )
            )
        finally:
            transaction.rollback()

    # weather_values is partitioned, so the plan names each child partition's
    # own copy of the index (e.g. weather_values_20260908_source_record_key_idx)
    # rather than the parent's ix_weather_values_source_record_key -- the
    # parent name never appears in a plan, only in the catalog.
    assert "_source_record_key_idx" in plan, (
        f"the weather_values half of the lookup does not use its index:\n{plan}"
    )
    assert "ix_weather_sync_run_sources_source_record_key" in plan, (
        f"the weather_sync_run_sources half of the lookup does not use its "
        f"index:\n{plan}"
    )
    assert "Seq Scan on weather_values" not in plan, (
        f"the lookup fell back to scanning weather_values whole:\n{plan}"
    )
    assert "Seq Scan on weather_sync_run_sources" not in plan, (
        f"the lookup fell back to scanning weather_sync_run_sources whole:\n{plan}"
    )
