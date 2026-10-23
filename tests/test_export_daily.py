import os
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text

from kortravelweather.export_artifacts import ExportArtifactStore
from kortravelweather.export_daily import merge_observations, observation, summarize
from kortravelweather.export_daily_store import daily_summary_tables, refresh_daily_summaries
from kortravelweather.repository import Base, WeatherRepository

KST = ZoneInfo("Asia/Seoul")
KEY = ("a", "2026-10-23", "morning")
NOW = datetime(2026, 10, 23, 12, tzinfo=KST)


def point(hour, metric="T1H", value="10", provider="python-kma-api", **changes):
    return {
        "location_id": "a",
        "provider": provider,
        "dataset_key": "kma_ultra_short_nowcast",
        "forecast_style": "nowcast",
        "metric_key": metric,
        "unit": {"T1H": "deg_c", "TEMP": "deg_c", "RN1": "mm", "PRECIP": "mm"}.get(metric, "code"),
        "target_at": datetime(2026, 10, 23, hour, tzinfo=KST).isoformat(),
        "known_at": NOW.isoformat(),
        "value_number": Decimal(value),
        **changes,
    }


def test_hour_winners_deduplicate_providers_replays_and_metric_rows():
    points = [
        point(6, value="12"),
        point(6, "RN1", "2"),
        point(7, "RN1", "3"),
        point(6, "TEMP", "99", provider="weatherapi"),
        point(6, "PRECIP", "200", provider="open_meteo"),
        point(7, value="14"),
    ]
    state = merge_observations({}, points, key=KEY, now=NOW)
    replay = merge_observations(state, points, key=KEY, now=NOW)
    assert replay == state
    summary = summarize(*KEY, replay, now=NOW)
    assert summary["temp_min"] == 12 and summary["temp_max"] == 14
    assert summary["temp_avg"] == 13 and summary["precip_sum_mm"] == 5
    assert summary["sample_count"] == 3  # rain[05,06), rain+temp[06,07), temp[07,08)
    assert summary["source_providers"] == ["python-kma-api"]
    assert summary["precip_occurred"] is True and summary["partial"] is False


def test_kst_slot_boundary_rain_interval_and_future_forecasts_are_not_observations():
    assert observation(point(5, "RN1", "1")) is None  # [04,05)는 morning 밖
    assert observation(point(11, "RN1", "1"))[0] == KEY  # [10,11)는 morning
    assert observation(point(11))[0][2] == "noon"
    state = merge_observations(
        {},
        [point(6, forecast_style="ultra_short"), point(13), point(6, location_id="other")],
        key=KEY,
        now=NOW,
    )
    assert state == {}
    empty = summarize(*KEY, state, now=NOW)
    assert empty["sample_count"] == 0 and empty["source_providers"] == []
    assert all(
        empty[k] is None
        for k in (
            "temp_avg",
            "humidity_avg",
            "precip_sum_mm",
            "precip_occurred",
            "sky_code",
            "wind_speed_avg",
        )
    )
    assert summarize(*KEY, {}, now=datetime(2026, 10, 23, 10, tzinfo=KST))["partial"] is True


def test_order_independent_provider_winner_and_cumulative_rain_exclusion():
    points = [
        point(6, value="10"),
        point(6, "TEMP", "30", provider="weatherapi"),
        point(6, "RN1", "20", provider="python-krforest-api"),
        point(6, "RN1", "999", unit="mm/day"),
    ]
    forward = merge_observations({}, points, key=KEY, now=NOW)
    reverse = merge_observations({}, reversed(points), key=KEY, now=NOW)
    assert forward == reverse
    result = summarize(*KEY, forward, now=NOW)
    assert result["temp_avg"] == 10 and result["precip_sum_mm"] is None
    assert result["source_providers"] == ["python-kma-api"]


@pytest.fixture
def daily_repository():
    repository = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    repository.create_schema()
    with repository.engine.begin() as c:
        c.execute(
            text(
                "TRUNCATE weather_daily_summaries, weather_daily_summary_progress, "
                "weather_export_scope_plans, weather_export_heads, weather_export_generations, "
                "weather_export_generation_chunks, weather_export_pending_refs, "
                "weather_export_attempts, "
                "weather_locations, weather_sync_runs RESTART IDENTITY CASCADE"
            )
        )
    yield repository
    repository.engine.dispose()


def test_daily_projection_uses_only_approved_generations_and_deduplicates_replay(
    daily_repository, tmp_path
):
    from datetime import timedelta

    from test_export_heads import _candidate, _descriptor

    repository = daily_repository
    descriptor = _descriptor(repository)
    now = datetime.now(KST)
    day = (now - timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    for hour, value in [(0, "10"), (0, "10"), (1, "20")]:
        heads, identity, capture, _, _, _ = _candidate(
            repository, tmp_path, descriptor, revision=day + timedelta(hours=hour), value=value
        )
        assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    progress = refresh_daily_summaries(repository, ExportArtifactStore(tmp_path))
    assert progress["processed"] == 3
    table, _ = daily_summary_tables(Base.metadata)
    with repository.engine.connect() as c:
        row = c.execute(select(table.c.summary)).scalar_one()
    assert row["temp_avg"] == 15 and row["sample_count"] == 2
    assert row["source_providers"] == ["python-kma-api"]
    assert refresh_daily_summaries(repository, ExportArtifactStore(tmp_path))["processed"] == 0


def test_failed_raw_commit_never_appears_in_daily_projection(daily_repository, tmp_path):
    from test_export_heads import _candidate, _descriptor

    repository = daily_repository
    descriptor = _descriptor(repository)
    heads, identity, capture, _, _, _ = _candidate(
        repository, tmp_path, descriptor, finish="failed"
    )
    assert heads.promote(identity, capture.artifacts) != "PUBLISHED"
    assert refresh_daily_summaries(repository, ExportArtifactStore(tmp_path))["processed"] == 0
    table, _ = daily_summary_tables(Base.metadata)
    with repository.engine.connect() as c:
        assert c.execute(select(table.c.summary)).first() is None


def test_daily_api_reads_precomputed_rows_auth_etag_pagination_and_empty_slots(
    daily_repository, tmp_path
):
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from kortravelweather_api.app import create_app
    from sqlalchemy import event
    from test_export_heads import _candidate, _descriptor

    from kortravelweather.export_collector import publish_pending_exports
    from kortravelweather.settings import WeatherSettings

    repository = daily_repository
    descriptor = _descriptor(repository)
    yesterday = (datetime.now(KST) - timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0
    )
    _candidate(repository, tmp_path, descriptor, revision=yesterday, value="12")
    assert publish_pending_exports(repository, tmp_path, daily_summaries=True).results == {
        "PUBLISHED": 1
    }
    client = TestClient(
        create_app(
            WeatherSettings(
                environment="development",
                admin_token="daily-test-token",
                enabled_providers=["python-kma-api"],
                service_exports_enabled=True,
                service_export_artifact_root=tmp_path,
            ),
            repository,
        )
    )
    headers = {"x-admin-token": "daily-test-token"}
    url, params = "/v1/service/exports/daily-summaries", {"days": 2, "limit": 2}
    assert client.get(url, params=params).status_code == 401
    statements = []

    def record_sql(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(repository.engine, "before_cursor_execute", record_sql)
    try:
        first = client.get(url, params=params, headers=headers)
        assert first.status_code == 200 and first.headers["content-encoding"] == "gzip"
        assert (
            client.get(
                url, params=params, headers={**headers, "if-none-match": first.headers["etag"]}
            ).status_code
            == 304
        )
        items, page = [], first.json()
        while True:
            items.extend(page["items"])
            if not page["meta"]["has_more"]:
                break
            page = client.get(
                url, params={**params, "cursor": page["meta"]["next_cursor"]}, headers=headers
            ).json()
        assert len(items) == 6 and len({(item["date"], item["slot"]) for item in items}) == 6
        winner = next(
            item
            for item in items
            if item["date"] == yesterday.date().isoformat() and item["slot"] == "morning"
        )
        assert winner["temp_avg"] == 12 and winner["sample_count"] == 1
        assert all(
            item["sample_count"] == 0 and item["temp_avg"] is None
            for item in items
            if item is not winner
        )
        assert not any(
            "weather_values" in statement or "hourly_winners" in statement
            for statement in statements
        )
    finally:
        event.remove(repository.engine, "before_cursor_execute", record_sql)
    assert (
        client.get(
            url,
            params={"days": 3, "limit": 2, "cursor": first.json()["meta"]["next_cursor"]},
            headers=headers,
        ).status_code
        == 422
    )
    _candidate(repository, tmp_path, descriptor, revision=yesterday, value="12", finish="failed")
    stale = client.get(
        url, params=params, headers={**headers, "if-none-match": first.headers["etag"]}
    )
    assert stale.status_code == 200 and stale.json()["meta"]["source_status"] == "stale"
    assert stale.json()["items"] == first.json()["items"]
