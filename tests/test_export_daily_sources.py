"""다중 제공처 요약의 단위와 실제 승인 coverage 경계를 검증한다."""

from test_export_daily import KEY, NOW, point
from test_export_external import external_repository as external_repository

from kortravelweather.export_daily import merge_observations, summarize


def test_sdk_celsius_and_multiple_providers_do_not_duplicate_hour_or_rain():
    points = [
        point(6, "TEMP", "12", provider="python-krforest-api", unit="℃"),
        point(6, "TEMP", "14", provider="python-krex-api", unit="℃"),
        point(6, "RAINFALL", "300", provider="python-krex-api", unit="mm"),
    ]
    state = merge_observations({}, points * 2, key=KEY, now=NOW)
    result = summarize(*KEY, state, now=NOW)
    assert result["temp_avg"] == 12 and result["sample_count"] == 1
    assert result["source_providers"] == ["python-krforest-api"]
    assert result["precip_sum_mm"] is None and result["precip_occurred"] is None


def _drain(repo, root):
    from kortravelweather.export_artifacts import ExportArtifactStore
    from kortravelweather.export_daily_store import refresh_daily_summaries

    for _ in range(32):
        if not refresh_daily_summaries(repo, ExportArtifactStore(root), limit=8)["has_more"]:
            return
    raise AssertionError("작은 시험 source 집합이 drain되지 않았습니다.")


def test_daily_inventory_requires_reducer_for_each_source_and_allows_forecast_only_location(
    external_repository, tmp_path, monkeypatch,
):
    from fastapi.testclient import TestClient
    from kortravelweather_api.app import create_app
    from sqlalchemy import select, update
    from test_all_source_exports import _sdk

    from kortravelweather.export_daily_store import daily_coverage_table
    from kortravelweather.export_heads import ExportHeads
    from kortravelweather.settings import WeatherSettings

    repo = external_repository
    for kind in ("mountain", "dust", "krex", "khoa"):
        _sdk(repo, tmp_path, kind, monkeypatch)
    _drain(repo, tmp_path)
    client = TestClient(create_app(WeatherSettings(
        admin_token="daily-sdk-source", enabled_providers=[
            "python-krforest-api", "python-krex-api", "python-khoa-api",
        ], service_exports_enabled=True, service_export_artifact_root=tmp_path,
    ), repo))
    headers = {"x-admin-token": "daily-sdk-source"}
    path = "/v1/service/exports/daily-summaries"
    healthy = client.get(path, params={"days": 1}, headers=headers)
    assert healthy.status_code == 200 and healthy.json()["meta"]["source_status"] == "ok"
    table = daily_coverage_table(ExportHeads(repo).metadata)
    with repo.engine.connect() as connection:
        observed_keys = set(connection.scalars(select(table.c.location_id)))
    khoa_key, = {row["location_id"] for row in healthy.json()["items"]} - observed_keys
    no_observation = client.get(path, params={"days": 1, "location_id": khoa_key}, headers=headers)
    assert no_observation.status_code == 200
    assert no_observation.json()["meta"]["source_collected_at"] is None
    assert all(row["sample_count"] == 0 for row in no_observation.json()["items"])
    import kortravelweather.export_daily_reader as daily_reader

    with monkeypatch.context() as bounded:
        bounded.setattr(daily_reader, "MAX_SELECTED_ROWS", 1)
        assert client.get(path, params={"days": 3}, headers=headers).status_code == 503
    with repo.engine.begin() as connection:
        connection.execute(update(table).where(table.c.provider == "python-krex-api").values(
            generation_id="previous-approved-generation",
        ))
    lagging = client.get(path, params={"days": 1}, headers={
        **headers, "if-none-match": healthy.headers["etag"],
    })
    assert lagging.status_code == 200 and lagging.json()["meta"]["source_status"] == "stale"
    assert lagging.json()["items"] == healthy.json()["items"]
    with repo.engine.begin() as connection:
        krex_key = connection.scalar(select(table.c.location_id).where(
            table.c.provider == "python-krex-api",
        ).limit(1))
        connection.execute(table.delete().where(table.c.provider == "python-krex-api"))
    retained = client.get(path, params={"days": 1}, headers=headers)
    assert retained.status_code == 200 and retained.json()["meta"]["source_status"] == "stale"
    assert retained.json()["items"] == healthy.json()["items"]
    assert client.get(
        path, params={"days": 1, "location_id": krex_key}, headers=headers,
    ).status_code == 503


def test_predeclared_unapproved_observation_is_not_forecast_only_zero(
    external_repository, tmp_path, monkeypatch,
):
    from fastapi.testclient import TestClient
    from kortravelweather_api.app import create_app
    from test_all_source_exports import _sdk
    from test_export_sdk import fixture

    from kortravelweather.export_sdk_collector import sdk_descriptor
    from kortravelweather.export_source_cohorts import declare_source_cohort
    from kortravelweather.providers import krex
    from kortravelweather.settings import WeatherSettings

    repo = external_repository
    _sdk(repo, tmp_path, "krex", monkeypatch)
    _drain(repo, tmp_path)
    _, _, parsed, _ = fixture("krex")
    new = krex.restarea_location(parsed).model_copy(update={"location_id": "new-krex"})
    repo.upsert_location(new)
    descriptor = sdk_descriptor(
        new, provider="python-krex-api", dataset_key="krex_restarea_weather",
    )
    run = repo.start_sync_run(provider=descriptor.provider, dataset_key=descriptor.dataset_key,
                              export_cohort_required=True)
    declare_source_cohort(repo, run_id=run.run_id, provider=descriptor.provider,
                          dataset_key=descriptor.dataset_key, descriptors=[descriptor])
    repo.finish_sync_run(run.run_id, status="failed")
    client = TestClient(create_app(WeatherSettings(
        admin_token="pending-daily", enabled_providers=["python-krex-api"],
        service_exports_enabled=True, service_export_artifact_root=tmp_path,
    ), repo))
    response = client.get("/v1/service/exports/daily-summaries", params={"location_id": "new-krex"},
                          headers={"x-admin-token": "pending-daily"})
    assert response.status_code == 503


def test_new_approved_location_waits_for_reducer_without_losing_previous_full_roster(
    external_repository, tmp_path,
):
    from datetime import UTC, datetime, timedelta

    from fastapi.testclient import TestClient
    from kortravelweather_api.app import create_app
    from sqlalchemy import update
    from test_export_heads import _candidate, _descriptor

    from kortravelweather.export_heads import ExportHeads
    from kortravelweather.models import WeatherLocation
    from kortravelweather.settings import WeatherSettings

    repo = external_repository
    descriptor = _descriptor(repo)
    original = descriptor.location_ids[0]
    for key in ("daily-b", "daily-c", "daily-d"):
        repo.create_location(WeatherLocation(
            location_id=key, name=key, latitude=37.5, longitude=127, nx=60, ny=127,
            enabled=key != "daily-d",
        ))
    descriptor = descriptor.model_copy(update={
        "entity_id": "grid:60:127",
        "location_ids": tuple(sorted([original, "daily-b", "daily-c"])),
    })
    heads, identity, capture, *_ = _candidate(repo, tmp_path, descriptor, cohort=True)
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    _drain(repo, tmp_path)
    client = TestClient(create_app(WeatherSettings(
        admin_token="daily-fallback", enabled_providers=["python-kma-api"],
        service_exports_enabled=True, service_export_artifact_root=tmp_path,
    ), repo))
    headers = {"x-admin-token": "daily-fallback"}
    path, params = "/v1/service/exports/daily-summaries", {"days": 1}
    healthy = client.get(path, params=params, headers=headers)
    assert healthy.status_code == 200 and healthy.json()["meta"]["source_status"] == "ok"
    assert len(healthy.json()["items"]) == 9
    first = client.get(path, params={**params, "limit": 1}, headers=headers)
    cursor = first.json()["meta"]["next_cursor"]
    catalog = ExportHeads(repo).metadata.tables["weather_locations"]
    with repo.engine.begin() as connection:
        connection.execute(update(catalog).where(
            catalog.c.location_id == "daily-d",
        ).values(enabled=True))
    descriptor = descriptor.model_copy(update={
        "location_ids": tuple(sorted([*descriptor.location_ids, "daily-d"])),
    })
    heads, identity, capture, *_ = _candidate(repo, tmp_path, descriptor, cohort=True)
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    pending = client.get(path, params=params, headers={
        **headers, "if-none-match": healthy.headers["etag"],
    })
    assert pending.status_code == 200 and pending.json()["meta"]["source_status"] == "stale"
    assert pending.json()["items"] == healthy.json()["items"]
    old = client.get(path, params={**params, "limit": 1, "cursor": cursor}, headers=headers)
    assert old.status_code == 200 and old.json()["meta"]["source_status"] == "stale"
    assert client.get(
        path, params={**params, "location_id": "daily-d"}, headers=headers,
    ).status_code == 503
    from kortravelweather.export_snapshots import export_snapshot_tables

    snapshots, _ = export_snapshot_tables(heads.metadata)
    with repo.engine.begin() as connection:
        connection.execute(update(snapshots).values(expires_at=datetime.now(UTC)-timedelta(seconds=1)))
    refreshed = client.get(path, params=params, headers=headers)
    assert refreshed.status_code == 200 and refreshed.json()["items"] == healthy.json()["items"]
    assert refreshed.json()["meta"]["snapshot_id"] != pending.json()["meta"]["snapshot_id"]
    _drain(repo, tmp_path)
    ready = client.get(path, params=params, headers=headers)
    assert ready.status_code == 200 and ready.json()["meta"]["source_status"] == "ok"
    assert len(ready.json()["items"]) == 12


def test_never_approved_anchor_makes_full_roster_stale_instead_of_503(
    external_repository, tmp_path, monkeypatch,
):
    """한 번도 승인되지 않은 관측 anchor가 이전 집합 없는 전체 목록을 영구503으로 막지 않는다.

    개별 위치 최초 조회의 필수 원천 미승인은503을 유지하고, 선택 원천(청정넷)은 stale이다.
    """
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from kortravelweather_api.app import create_app
    from krforest.parser import parse_mountain_weather
    from test_all_source_exports import _sdk

    from kortravelweather.models import KST
    from kortravelweather.providers import krforest, krforest_dust
    from kortravelweather.settings import WeatherSettings

    repo = external_repository
    _sdk(repo, tmp_path, "mountain", monkeypatch)
    _drain(repo, tmp_path)
    observed = datetime.now(KST) - timedelta(hours=1)
    # 산악 응답에서 계속 빠지는 지점과 측정값이 없는 청정넷 지점을 흉내 낸다.
    mountain = krforest.station_location(parse_mountain_weather({
        "obsId": "M999", "obsName": "미승인산", "latitude": 35.1, "longitude": 128.2,
        "tm": observed.strftime("%Y-%m-%d %H:%M"), "tm2m": "-2", "hm2m": "70", "ws2m": "1",
    }))
    dust = krforest_dust.station_location(SimpleNamespace(
        station_code="999", description="미승인 청정넷", latitude=35.2, longitude=128.3,
        address="경남", elevation=None,
    ))
    for location in (mountain, dust):
        repo.upsert_location(location)
    client = TestClient(create_app(WeatherSettings(
        admin_token="never-approved", enabled_providers=["python-krforest-api"],
        service_exports_enabled=True, service_export_artifact_root=tmp_path,
    ), repo))
    headers = {"x-admin-token": "never-approved"}
    path = "/v1/service/exports/daily-summaries"
    full = client.get(path, params={"days": 1}, headers=headers)
    assert full.status_code == 200, full.text
    assert full.json()["meta"]["source_status"] == "stale"
    pending = [item for item in full.json()["items"]
               if item["location_id"] in {mountain.location_id, dust.location_id}]
    assert len(pending) == 6 and all(item["sample_count"] == 0 for item in pending)
    assert len({item["location_id"] for item in full.json()["items"]}) == 3
    assert client.get(path, params={"days": 1, "location_id": mountain.location_id},
                      headers=headers).status_code == 503
    optional = client.get(path, params={"days": 1, "location_id": dust.location_id},
                          headers=headers)
    assert optional.status_code == 200 and optional.json()["meta"]["source_status"] == "stale"
    assert all(item["sample_count"] == 0 for item in optional.json()["items"])


def test_daily_fallback_moves_its_date_window_to_the_current_kst_date(
    external_repository, tmp_path, monkeypatch,
):
    """긴 fallback 동안에도 오늘 구간이 빠지지 않는다. roster·coverage digest는 유지한다."""
    from datetime import UTC, datetime, timedelta

    from fastapi.testclient import TestClient
    from kortravelweather_api.app import create_app
    from sqlalchemy import select, update
    from test_export_heads import _candidate, _descriptor

    from kortravelweather.export_daily import KST
    from kortravelweather.export_daily_reader import DailySummaryExports
    from kortravelweather.export_heads import ExportHeads
    from kortravelweather.export_snapshots import ExportSnapshotUnavailable, export_snapshot_tables
    from kortravelweather.settings import WeatherSettings

    repo = external_repository
    heads, identity, capture, *_ = _candidate(repo, tmp_path, _descriptor(repo), cohort=True)
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    _drain(repo, tmp_path)
    client = TestClient(create_app(WeatherSettings(
        admin_token="daily-window", enabled_providers=["python-kma-api"],
        service_exports_enabled=True, service_export_artifact_root=tmp_path,
    ), repo))
    headers = {"x-admin-token": "daily-window"}
    path, params = "/v1/service/exports/daily-summaries", {"days": 2}
    healthy = client.get(path, params=params, headers=headers)
    assert healthy.status_code == 200
    today = datetime.now(KST).date()
    yesterday = today - timedelta(days=1)
    snapshots, _ = export_snapshot_tables(ExportHeads(repo).metadata)
    with repo.engine.begin() as connection:
        row = connection.execute(select(snapshots).where(
            snapshots.c.snapshot_id == healthy.json()["meta"]["snapshot_id"],
        )).mappings().one()
        # 어제 발급한 뒤 reducer 지연 등으로 계속 fallback 중인 상태를 만든다.
        connection.execute(update(snapshots).where(
            snapshots.c.snapshot_id == row["snapshot_id"],
        ).values(manifest={
            **row["manifest"], "first_date": (yesterday - timedelta(days=1)).isoformat(),
            "last_date": yesterday.isoformat(),
        }, expires_at=datetime.now(UTC) - timedelta(seconds=1)))

    def unavailable(self):
        raise ExportSnapshotUnavailable("시험용 reducer 지연")

    monkeypatch.setattr(DailySummaryExports, "_freeze_daily_ready", unavailable)
    fallback = client.get(path, params=params, headers=headers)
    assert fallback.status_code == 200 and fallback.json()["meta"]["source_status"] == "stale"
    assert {item["date"] for item in fallback.json()["items"]} == {
        yesterday.isoformat(), today.isoformat(),
    }
    with repo.engine.connect() as connection:
        manifest = connection.scalar(select(snapshots.c.manifest).where(
            snapshots.c.snapshot_id == fallback.json()["meta"]["snapshot_id"],
        ))
    assert (manifest["first_date"], manifest["last_date"]) == (
        yesterday.isoformat(), today.isoformat(),
    )
    assert manifest["fallback_unconfirmed"] is True
    assert manifest["locations"] == row["manifest"]["locations"]
    assert manifest["coverage"] == row["manifest"]["coverage"]
    # 오늘 창의 유효한 fallback handle은 다시 복제하지 않는다.
    again = client.get(path, params=params, headers=headers)
    assert again.json()["meta"]["snapshot_id"] == fallback.json()["meta"]["snapshot_id"]
