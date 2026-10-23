"""기본 current가 SDK와 HTTP adapter의 실제 승인 합집합을 모두 제공한다."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient
from kortravelweather_api.app import create_app
from sqlalchemy import select
from test_export_external import FakeResponse, FixtureTransport, _external_payload
from test_export_external import external_repository as external_repository
from test_export_sdk import fixture
from test_frozen_service_exports import _collect

from kortravelweather.export_artifacts import ExportArtifactStore
from kortravelweather.export_heads import ExportHeads
from kortravelweather.export_receipts import ExportAttempt
from kortravelweather.export_sdk_collector import SdkExportCollector, sdk_descriptor
from kortravelweather.export_source_cohorts import declare_source_cohort
from kortravelweather.export_source_registry import KNOWN
from kortravelweather.models import KST, WeatherLocation
from kortravelweather.providers import (
    OpenMeteoProvider,
    OpenWeatherMapProvider,
    WeatherApiProvider,
    airkorea,
    khoa,
    krex,
    krforest,
    krforest_dust,
)
from kortravelweather.providers.base import ProviderLocation
from kortravelweather.settings import WeatherSettings


def _sdk(repo, root, kind, monkeypatch):
    import test_export_sdk

    monkeypatch.setattr(test_export_sdk, "AT", datetime.now(KST))
    descriptor, _, parsed, normalizer = fixture(kind)
    if kind == "air":
        location = WeatherLocation(
            location_id=descriptor.entity_id,
            name=descriptor.station_name,
            latitude=descriptor.latitude,
            longitude=descriptor.longitude,
            metadata={
                "measurement_point": {
                    "provider": descriptor.provider,
                    "station_id": descriptor.station_id,
                    "station_name": descriptor.station_name,
                    "sido_name": descriptor.sido_name,
                }
            },
        )
    elif kind == "dust":
        location = krforest_dust.station_location(
            SimpleNamespace(
                station_code="001",
                description="청정넷",
                latitude=37.5,
                longitude=127,
                address="서울",
                elevation=None,
            )
        )
    else:
        location = {
            "mountain": krforest.station_location,
            "krex": krex.restarea_location,
            "khoa": khoa.beach_location,
        }[kind](parsed)
    repo.upsert_location(location)
    descriptor = sdk_descriptor(
        location, provider=descriptor.provider, dataset_key=descriptor.dataset_key
    )
    run = repo.start_sync_run(
        provider=descriptor.provider,
        dataset_key=descriptor.dataset_key,
        export_cohort_required=True,
    )
    declare_source_cohort(
        repo,
        run_id=run.run_id,
        provider=descriptor.provider,
        dataset_key=descriptor.dataset_key,
        descriptors=[descriptor],
    )
    collector = SdkExportCollector(repo, run_id=run.run_id, artifact_root=root)
    plan = collector.prepare(
        location=location, provider=descriptor.provider, dataset_key=descriptor.dataset_key
    )
    fetched = datetime.now(KST)
    source = {
        "air": airkorea.measurement_source_record,
        "mountain": krforest.mountain_source_record,
        "dust": krforest_dust.dust_source_record,
        "krex": krex.restarea_source_record,
        "khoa": khoa.beach_index_source_record,
    }[kind](
        parsed,
        location_id=location.location_id,
        fetched_at=fetched,
    )
    source["run_id"] = run.run_id
    collector.ingest(
        plan,
        record=source,
        values=normalizer(
            parsed,
            location_id=location.location_id,
            source_record_key=source["source_record_key"],
            known_at=fetched,
        ),
    )
    repo.finish_sync_run(run.run_id, status="success")
    assert collector.heads.promote(plan.identity, ExportArtifactStore(root)) == "PUBLISHED"


def _http_adapter(repo, root, adapter):
    from kortravelweather_dagster.external_weather import run_external_weather_sync

    target = ProviderLocation("all-sources-" + uuid.uuid4().hex, 37.5, 127, {})
    repo.create_location(
        WeatherLocation(
            location_id=target.location_id,
            name="외부 승인 지점",
            latitude=37.5,
            longitude=127,
        )
    )
    provider = adapter(api_key="fixture-key")
    provider.transport = FixtureTransport(
        FakeResponse(_external_payload(provider.provider_key, forecast=False, at=datetime.now(UTC)))
    )
    result = run_external_weather_sync(
        repository=repo,
        provider=provider,
        targets=[target],
        dataset_key=f"{provider.provider_key}_current",
        export_artifact_root=root,
    )
    heads = ExportHeads(repo)
    with repo.engine.connect() as c:
        identity = c.execute(
            select(
                heads.lifecycle.attempts.c.attempt_id,
                heads.lifecycle.attempts.c.owner_id,
            ).where(heads.lifecycle.attempts.c.run_id == result["run_id"])
        ).one()
    assert heads.promote(ExportAttempt(*identity), ExportArtifactStore(root)) == "PUBLISHED"


def test_default_current_exports_every_enabled_observation_source_and_keeps_failure_stale(
    external_repository,
    tmp_path,
    monkeypatch,
):
    repo = external_repository
    settings = WeatherSettings(
        admin_token="all-sources-fixture",
        enabled_providers=sorted(KNOWN),
        service_exports_enabled=True,
        service_export_artifact_root=tmp_path,
    )
    client = TestClient(create_app(settings, repo))
    url, headers = "/v1/service/exports/current", {"x-admin-token": "all-sources-fixture"}
    assert client.get(url).status_code == 401
    assert client.get(url, headers=headers).status_code == 503
    _collect(repo, tmp_path)
    assert client.get(url, headers=headers).status_code == 503
    for adapter in (OpenMeteoProvider, WeatherApiProvider, OpenWeatherMapProvider):
        _http_adapter(repo, tmp_path, adapter)
    for kind in ("air", "mountain", "dust", "krex"):
        _sdk(repo, tmp_path, kind, monkeypatch)
    healthy = client.get(url, headers=headers)
    assert healthy.status_code == 200 and healthy.json()["meta"]["source_status"] == "ok"
    assert {item["provider"] for item in healthy.json()["items"]} == KNOWN - {"python-khoa-api"}
    assert (
        client.get(url, headers={**headers, "if-none-match": healthy.headers["etag"]}).status_code
        == 304
    )
    first = client.get(url, headers=headers, params={"limit": 1}).json()
    cursor = first["meta"]["next_cursor"]
    failed = repo.start_sync_run(
        provider="python-krex-api",
        dataset_key="krex_restarea_weather",
        export_cohort_required=True,
    )
    repo.finish_sync_run(failed.run_id, status="failed")
    stale = client.get(url, headers={**headers, "if-none-match": healthy.headers["etag"]})
    assert stale.status_code == 200 and stale.json()["meta"]["source_status"] == "stale"
    assert stale.json()["items"] == healthy.json()["items"]
    continued = client.get(url, headers=headers, params={"limit": 1, "cursor": cursor})
    assert continued.status_code == 200 and continued.json()["meta"]["source_status"] == "stale"


def test_khoa_external_forecast_is_public_and_current_is_not_fabricated(
    external_repository,
    tmp_path,
    monkeypatch,
):
    repo = external_repository
    _sdk(repo, tmp_path, "khoa", monkeypatch)
    client = TestClient(
        create_app(
            WeatherSettings(
                admin_token="khoa-source-fixture",
                enabled_providers=["python-khoa-api"],
                service_exports_enabled=True,
                service_export_artifact_root=tmp_path,
            ),
            repo,
        )
    )
    headers = {"x-admin-token": "khoa-source-fixture"}
    response = client.get(
        "/v1/service/exports/forecasts", params={"style": "external"}, headers=headers
    )
    assert response.status_code == 200 and response.json()["meta"]["source_status"] == "ok"
    assert len(response.json()["items"]) == 1
    assert "WAVE_HEIGHT" in response.json()["items"][0]["metrics"]
    assert client.get("/v1/service/exports/current", headers=headers).status_code == 503
