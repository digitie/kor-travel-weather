"""중기 reader의 실제 앱 인증·gzip·조건부 응답·frozen cursor 계약."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from kortravelweather_api.app import create_app
from sqlalchemy import update
from test_export_external import external_repository as external_repository
from test_export_mid_reader_postgres import _collect

from kortravelweather.export_mid_reader import FrozenMidForecastExports
from kortravelweather.settings import WeatherSettings

URL = "/v1/service/exports/forecasts"
HEADERS = {"x-admin-token": "mid-reader-offline-token"}


def _client(repo, root, *, token="mid-reader-offline-token", enabled=True):
    return TestClient(create_app(WeatherSettings(
        admin_token=token, enabled_providers=["python-kma-api"],
        service_exports_enabled=enabled, service_export_artifact_root=root,
    ), repo))


def test_mid_rest_authorization_missing_pair_gzip_304_and_stale_old_cursor(
    external_repository, tmp_path,
):
    repo = external_repository
    client, params = _client(repo, tmp_path), {"style": "mid", "limit": 1}
    assert client.get(URL, params=params).status_code == 401
    assert client.get(URL, params=params, headers={"x-admin-token": "wrong"}).status_code == 401
    assert client.get(URL, params=params, headers=HEADERS).status_code == 503
    collector, _ = _collect(repo, tmp_path)
    response = client.get(URL, params=params, headers={**HEADERS, "accept-encoding": "gzip"})
    assert response.status_code == 200 and response.headers["content-encoding"] == "gzip"
    body, etag = response.json(), response.headers["etag"]
    assert body["meta"]["source_status"] == "ok" and body["meta"]["item_count"] == 1
    assert "구름많음" in body["items"][0]["metrics"]["SKY"]
    assert client.get(
        URL, params=params, headers={**HEADERS, "if-none-match": etag},
    ).status_code == 304
    cursor = body["meta"]["next_cursor"]
    next_page = client.get(URL, params={**params, "cursor": cursor}, headers=HEADERS)
    assert next_page.status_code == 200
    assert next_page.json()["items"][0]["location_id"] != body["items"][0]["location_id"]
    run = repo.start_sync_run(
        provider="python-kma-api", dataset_key="kma_mid_forecast", export_cohort_required=True,
    )
    repo.finish_sync_run(run.run_id, status="failed")
    failed = client.get(URL, params=params, headers={**HEADERS, "if-none-match": etag})
    assert failed.status_code == 200 and failed.json()["meta"]["source_status"] == "stale"
    assert failed.json()["items"] == body["items"] and failed.headers["etag"] != etag
    assert client.get(URL, params={**params, "cursor": cursor}, headers=HEADERS).json()[
        "meta"
    ]["source_status"] == "stale"
    assert client.get(URL, params={**params, "cursor": cursor + "bad"},
                      headers=HEADERS).status_code == 422
    assert client.get(URL, params={**params, "cursor": cursor, "limit": 2},
                      headers=HEADERS).status_code == 422
    assert client.get(URL, params={**params, "cursor": cursor, "provider": "open_meteo"},
                      headers=HEADERS).status_code == 422
    handle = FrozenMidForecastExports(
        repo, collector.artifacts, signing_key=HEADERS["x-admin-token"],
    )
    with repo.engine.begin() as connection:
        connection.execute(update(handle.snapshots).where(
            handle.snapshots.c.snapshot_id == body["meta"]["snapshot_id"],
        ).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert client.get(URL, params={**params, "cursor": cursor}, headers=HEADERS).status_code == 410


@pytest.mark.parametrize("token,enabled", [(None, True), ("mid-reader-offline-token", False)])
def test_mid_rest_is_unavailable_when_feature_or_auth_configuration_is_missing(
    external_repository, tmp_path, token, enabled,
):
    client = _client(external_repository, tmp_path, token=token, enabled=enabled)
    assert client.get(URL, params={"style": "mid"}, headers=HEADERS).status_code == 503
