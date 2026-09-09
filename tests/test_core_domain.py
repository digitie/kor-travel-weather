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
    """Move a published fact into the past.

    The immutability trigger refuses UPDATE, so the ageing has to disable it --
    which is also a small proof that the trigger still refuses everything except
    the purge's own DELETE.
    """
    with repository.engine.begin() as connection:
        for table, column in (
            ("weather_values", "known_at"),
            ("weather_source_records", "fetched_at"),
        ):
            connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
            connection.execute(
                text(
                    f"UPDATE {table} SET {column} = :at WHERE source_record_key = :key"
                ),
                {"at": known_at, "key": source_key},
            )
            connection.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))


def _retention_repository() -> WeatherRepository:
    repository = WeatherRepository(TEST_DATABASE_URL)
    repository.create_schema()
    repository.upsert_location(
        WeatherLocation(location_id="retention", name="보존", latitude=37.5, longitude=127.0)
    )
    return repository


def test_history_older_than_the_retention_window_is_purged() -> None:
    """Age decides, and only age.

    ``recent`` is the row that makes the cutoff load-bearing.  Without it every
    surviving fact is one the projection pins, so the purge would pass this test
    just as well with no date predicate at all -- a mutation that widened the
    window by 400 days did exactly that and went unnoticed.
    """
    repository = _retention_repository()
    now = datetime.now(KST)
    pinned = _seed_history(repository, "pinned", now)
    recent = _seed_history(repository, "recent", now)
    stale = _seed_history(repository, "stale", now)
    _age_history(repository, stale, now - timedelta(days=30))
    # The projection pins the newest fact of every logical point and a pinned
    # fact is never deleted, so drop two pointers: that is what those rows look
    # like once a newer revision of the same logical point exists.  ``recent``
    # is then inside the window and unpinned; only the cutoff can spare it.
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM weather_current_values "
                "WHERE metric_key IN ('Mstale', 'Mrecent')"
            )
        )

    report = repository.purge_expired_history(retention_days=7)

    assert report.values_deleted == 1
    assert report.sources_deleted == 1
    assert report.truncated is False
    with repository.engine.connect() as connection:
        remaining = sorted(
            connection.execute(
                text("SELECT source_record_key FROM weather_values")
            ).scalars()
        )
        sources = sorted(
            connection.execute(
                text("SELECT source_record_key FROM weather_source_records")
            ).scalars()
        )
    assert remaining == sorted([pinned, recent]), (
        f"expected only {stale} to go; the cutoff is not deciding what survives"
    )
    assert sources == sorted([pinned, recent])


def test_purge_never_deletes_a_fact_the_projection_still_points_at() -> None:
    """A location that stopped reporting must keep its last reading.

    The projection's foreign key is ON DELETE RESTRICT, so deleting a pinned
    fact would not corrupt anything -- it would raise, and the nightly job would
    then fail every night for as long as any location stayed quiet past the
    window.  Age alone makes this reachable; no operator action is involved.
    """
    repository = _retention_repository()
    source_key = _seed_history(repository, "quiet", datetime.now(KST))
    _age_history(repository, source_key, datetime.now(KST) - timedelta(days=400))

    report = repository.purge_expired_history(retention_days=7)

    assert report.values_deleted == 0, (
        "the purge deleted the fact its own projection pointer names"
    )
    with repository.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM weather_values")).scalar_one() == 1
        )


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
    transaction.  Plain ``SET`` would look identical here -- same purge, same
    result -- while quietly leaving DELETE open on a pooled connection for
    everything that borrowed it afterwards, which on a long-lived API process is
    every request.  So the check has to come *after* a purge that really ran,
    on the same engine, not after a transaction that merely set the flag.
    """
    repository = _retention_repository()
    old = datetime.now(KST) - timedelta(days=30)
    _age_history(repository, _seed_history(repository, "gone", datetime.now(KST)), old)
    with repository.engine.begin() as connection:
        connection.execute(text("DELETE FROM weather_current_values"))

    assert repository.purge_expired_history(retention_days=7).values_deleted == 1

    _seed_history(repository, "after", datetime.now(KST))
    with repository.engine.begin() as connection, pytest.raises(Exception, match="immutable"):
        connection.execute(text("DELETE FROM weather_values"))


def test_purge_reports_when_it_stops_short_of_the_backlog() -> None:
    """Hitting the cap has to be visible; the deleted count cannot say it."""
    repository = _retention_repository()
    old = datetime.now(KST) - timedelta(days=30)
    for index in range(3):
        _age_history(
            repository, _seed_history(repository, f"old{index}", datetime.now(KST)), old
        )
    with repository.engine.begin() as connection:
        connection.execute(text("DELETE FROM weather_current_values"))

    report = repository.purge_expired_history(
        retention_days=7, batch_rows=1, max_batches=2
    )

    assert report.values_deleted == 2, "the cap should bound the run, not end it"
    assert report.truncated is True, (
        "the run stopped with a backlog and said nothing; a purge that never "
        "catches up then looks exactly like one with no work to do"
    )
