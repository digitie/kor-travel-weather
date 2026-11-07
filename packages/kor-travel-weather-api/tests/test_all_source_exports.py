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


def _age_source(repo, provider, age):
    from sqlalchemy import update

    from kortravelweather.export_inventory import export_inventory_table

    table = export_inventory_table(ExportHeads(repo).metadata)
    with repo.engine.begin() as c:
        c.execute(update(table).where(table.c.provider == provider).values(
            source_collected_at=datetime.now(UTC) - age,
        ))


def test_expired_source_is_left_out_of_current_and_kma_rotation_age_is_not(
    external_repository,
    tmp_path,
    monkeypatch,
):
    """만료한 원천은 값·meta에서 빠지고 200 stale이다(503 아님). 회전 나이의 KMA는 남는다."""
    from datetime import timedelta

    from kortravelweather.export_inventory_selection import source_expiry

    repo = external_repository
    client = TestClient(create_app(WeatherSettings(
        admin_token="expired-source-fixture",
        enabled_providers=sorted(KNOWN),
        service_exports_enabled=True,
        service_export_artifact_root=tmp_path,
    ), repo))
    url, headers = "/v1/service/exports/current", {"x-admin-token": "expired-source-fixture"}
    _collect(repo, tmp_path)
    for adapter in (OpenMeteoProvider, WeatherApiProvider, OpenWeatherMapProvider):
        _http_adapter(repo, tmp_path, adapter)
    for kind in ("air", "mountain", "dust", "krex"):
        _sdk(repo, tmp_path, kind, monkeypatch)
    healthy = client.get(url, headers=headers)
    assert healthy.status_code == 200 and healthy.json()["meta"]["source_status"] == "ok"
    # KMA 회전 slot은 다음 sweep까지 수 시간(운영 약 6시간) 묵는다. 신선도 상한(3시간)은 넘어
    # stale이지만 만료되지는 않는다 — 값은 그대로 나간다.
    _age_source(repo, "python-kma-api", timedelta(hours=6, minutes=30))
    rotating = client.get(url, headers=headers)
    assert rotating.status_code == 200 and rotating.json()["meta"]["source_status"] == "stale"
    assert "python-kma-api" in {item["provider"] for item in rotating.json()["items"]}
    _age_source(repo, "python-kma-api", timedelta(0))
    # 응답에서 계속 빠지는 원천: 만료를 넘으면 그 원천 없이 200 stale, meta는 살아 있는 원천 기준.
    dead = source_expiry("krex_restarea_weather") + timedelta(hours=1)
    _age_source(repo, "python-krex-api", dead)
    expired = client.get(url, headers=headers)
    assert expired.status_code == 200 and expired.json()["meta"]["source_status"] == "stale"
    providers = {item["provider"] for item in expired.json()["items"]}
    assert providers == KNOWN - {"python-khoa-api", "python-krex-api"}
    collected = datetime.fromisoformat(expired.json()["meta"]["source_collected_at"])
    assert datetime.now(UTC) - collected < timedelta(hours=1)


def test_slow_sdk_source_keeps_serving_but_leaves_freshness_after_the_window(
    external_repository,
    tmp_path,
    monkeypatch,
):
    """신선도 창(24시간) 밖·만료(72시간) 전 SDK 승인은 값은 내되 meta·stale을 붙잡지 않는다.

    2026-11-07 운영: 응답에서 빠진 산악기상 세 곳의 11-04 승인이 current·daily meta를 57시간
    붙잡았다(소유자 결정: 값은 72시간까지, 신선도는 24시간 안 승인만).
    """
    from datetime import timedelta

    from kortravelweather.export_inventory_selection import FRESHNESS_WINDOW, source_expiry

    repo = external_repository
    client = TestClient(create_app(WeatherSettings(
        admin_token="freshness-window-fixture",
        enabled_providers=sorted(KNOWN),
        service_exports_enabled=True,
        service_export_artifact_root=tmp_path,
    ), repo))
    url, headers = "/v1/service/exports/current", {"x-admin-token": "freshness-window-fixture"}
    _collect(repo, tmp_path)
    for adapter in (OpenMeteoProvider, WeatherApiProvider, OpenWeatherMapProvider):
        _http_adapter(repo, tmp_path, adapter)
    for kind in ("air", "mountain", "dust", "krex"):
        _sdk(repo, tmp_path, kind, monkeypatch)
    assert client.get(url, headers=headers).json()["meta"]["source_status"] == "ok"
    slow = FRESHNESS_WINDOW + timedelta(hours=6)
    assert slow < source_expiry("krex_restarea_weather")
    _age_source(repo, "python-krex-api", slow)
    page = client.get(url, headers=headers)
    assert page.status_code == 200
    assert "python-krex-api" in {item["provider"] for item in page.json()["items"]}  # 값은 낸다
    collected = datetime.fromisoformat(page.json()["meta"]["source_collected_at"])
    # 오래된 승인이 meta를 끌어내리지 않는다
    assert datetime.now(UTC) - collected < timedelta(hours=1)
    assert page.json()["meta"]["source_status"] == "ok"
    # 원천 하나가 통째로 창 밖이면(응답 전체가 끊김) 값은 내더라도 stale이다.
    _age_source(repo, "python-krforest-api", slow)
    silent = client.get(url, headers=headers)
    assert silent.status_code == 200 and silent.json()["meta"]["source_status"] == "stale"


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
