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
