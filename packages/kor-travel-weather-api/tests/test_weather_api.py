from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from kortravelweather_api.app import create_app
from sqlalchemy import text

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)


def test_health_and_location_admin(api_client: TestClient) -> None:
    assert api_client.get("/health").status_code == 200
    created = api_client.post(
        "/v1/admin/locations",
        json={
            "location_id": "seoul",
            "name": "서울",
            "latitude": 37.5665,
            "longitude": 126.978,
            "nx": 60,
            "ny": 127,
        },
    )
    assert created.status_code == 201
    listed = api_client.get("/v1/weather/locations")
    assert listed.status_code == 200
    assert listed.json()["data"][0]["location_id"] == "seoul"


def test_create_location_is_insert_only(api_client: TestClient) -> None:
    body = {
        "location_id": "same-id",
        "name": "첫 번째",
        "latitude": 37.5,
        "longitude": 127,
        "nx": 60,
        "ny": 127,
    }
    assert api_client.post("/v1/admin/locations", json=body).status_code == 201
    duplicate = {**body, "name": "두 번째"}
    response = api_client.post("/v1/admin/locations", json=duplicate)
    assert response.status_code == 409
    assert api_client.get("/v1/admin/locations").json()["data"][0]["name"] == "첫 번째"


def test_create_location_rejects_invalid_id_as_problem(api_client: TestClient) -> None:
    response = api_client.post(
        "/v1/admin/locations",
        json={
            "location_id": "bad/id",
            "name": "잘못된 ID",
            "latitude": 37.5,
            "longitude": 127,
            "nx": 60,
            "ny": 127,
        },
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_public_does_not_expose_admin_metadata_or_disabled_locations(
    api_client: TestClient,
) -> None:
    created = api_client.post(
        "/v1/admin/locations",
        json={
            "location_id": "secret-place",
            "name": "비공개",
            "latitude": 37.5,
            "longitude": 127,
            "nx": 60,
            "ny": 127,
            "metadata": {"internal_token": "do-not-leak"},
        },
    )
    assert created.status_code == 201
    assert created.json()["data"]["metadata"]["internal_token"] == "do-not-leak"
    assert (
        "metadata" not in api_client.get("/v1/weather/locations/secret-place").json()["data"]
        or api_client.get("/v1/weather/locations/secret-place").json()["data"]["metadata"] == {}
    )
    assert (
        api_client.patch("/v1/admin/locations/secret-place", json={"enabled": False}).status_code
        == 200
    )
    assert api_client.get("/v1/weather/locations/secret-place").status_code == 404
    assert api_client.get("/v1/weather/locations").json()["data"] == []


def test_admin_cannot_move_anchor_after_fact(api_client: TestClient) -> None:
    api_client.post(
        "/v1/admin/locations",
        json={
            "location_id": "anchor",
            "name": "Anchor",
            "latitude": 37.5,
            "longitude": 127,
            "nx": 60,
            "ny": 127,
        },
    )
    repository = api_client.app.state.repository
    repository.record_source(
        source_record_key="anchor-source",
        provider="p",
        dataset_key="d",
        source_entity_type="weather_response",
        source_entity_id="anchor",
        payload={"rows": []},
    )
    repository.upsert_values(
        [
            WeatherValue(
                location_id="anchor",
                provider="p",
                dataset_key="d",
                weather_domain="d",
                forecast_style=ForecastStyle.SHORT,
                metric_key="TMP",
                target_at=datetime(2026, 1, 1, tzinfo=UTC),
                value_number=Decimal("1"),
                source_record_key="anchor-source",
            )
        ]
    )
    assert api_client.patch("/v1/admin/locations/anchor", json={"latitude": 38}).status_code == 409


def test_forecast_order_is_422(api_client: TestClient) -> None:
    response = api_client.get(
        "/v1/weather/locations/missing/forecast",
        params={"from": "2026-01-02T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["detail"] == "from/to 시간 범위가 올바르지 않습니다."
    assert "pydantic.dev" not in response.text


def test_resolve_merges_nearby_station_and_kma_source_bundle(api_client: TestClient) -> None:
    repository = api_client.app.state.repository
    station = WeatherLocation(
        location_id="airkorea-seoul",
        name="서울 측정소",
        latitude=37.5,
        longitude=127.0,
        metadata={
            "measurement_point": {
                "provider": "python-airkorea-api",
                "station_name": "서울",
                "address": "서울",
            }
        },
    )
    kma = WeatherLocation(
        location_id="kma-seoul",
        name="서울 KMA",
        latitude=37.51,
        longitude=127.01,
        nx=60,
        ny=127,
    )
    repository.create_location(station)
    repository.create_location(kma)
    for key, provider, location_id, style, metric in (
        ("resolve-air", "python-airkorea-api", station.location_id, ForecastStyle.OBSERVED, "PM10"),
        ("resolve-kma", "python-kma-api", kma.location_id, ForecastStyle.SHORT, "TMP"),
    ):
        dataset_key = (
            "airkorea_realtime_measurement"
            if provider.startswith("python-air")
            else "kma_short_forecast"
        )
        weather_domain = "air_quality" if provider.startswith("python-air") else "weather"
        repository.record_source(
            source_record_key=key,
            provider=provider,
            dataset_key=dataset_key,
            source_entity_type="weather_response",
            source_entity_id=location_id,
            payload={"rows": [{"metric": metric}]},
        )
        repository.upsert_values(
            [
                WeatherValue(
                    location_id=location_id,
                    provider=provider,
                    dataset_key=dataset_key,
                    weather_domain=weather_domain,
                    forecast_style=style,
                    metric_key=metric,
                    target_at=datetime(2026, 8, 31, 12, tzinfo=UTC),
                    value_number=Decimal("12"),
                    source_record_key=key,
                )
            ]
        )
    response = api_client.get(
        "/v1/weather/resolve", params={"lat": 37.5, "lon": 127.0, "radius_km": 25}
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["location"]["location_id"] == station.location_id
    assert {item["location_id"] for item in body["source_locations"]} == {
        station.location_id,
        kma.location_id,
    }
    assert {item["provider"] for item in body["latest"] + body["forecast"]} == {
        "python-airkorea-api",
        "python-kma-api",
    }


def test_marker_batch_returns_weather_and_alert_state(api_client: TestClient) -> None:
    repository = api_client.app.state.repository
    location = WeatherLocation(
        location_id="marker-seoul",
        name="마커 서울",
        latitude=37.5,
        longitude=127,
    )
    repository.create_location(location)
    repository.record_source(
        source_record_key="marker-source",
        provider="python-kma-api",
        dataset_key="kma_ultra_short_nowcast",
        source_entity_type="weather_response",
        source_entity_id=location.location_id,
        payload={"rows": [{"metric": "PTY"}]},
    )
    repository.upsert_values(
        [
            WeatherValue(
                location_id=location.location_id,
                provider="python-kma-api",
                dataset_key="kma_ultra_short_nowcast",
                weather_domain="weather",
                forecast_style=ForecastStyle.NOWCAST,
                metric_key="PTY",
                target_at=datetime(2026, 8, 31, 12, tzinfo=UTC),
                value_number=Decimal("1"),
                source_record_key="marker-source",
            )
        ]
    )
    response = api_client.get("/v1/weather/markers", params={"location_id": location.location_id})
    assert response.status_code == 200
    assert response.json()["data"][0]["latest"][0]["metric_key"] == "PTY"


def test_openapi_error_contract_matches_problem_handler(api_client: TestClient) -> None:
    schema = api_client.app.openapi()
    forecast_errors = schema["paths"]["/v1/weather/locations/{location_id}/forecast"]["get"][
        "responses"
    ]
    assert forecast_errors["422"]["content"]["application/problem+json"]["schema"] == {
        "$ref": "#/components/schemas/Problem"
    }
    admin_errors = schema["paths"]["/v1/admin/locations"]["post"]["responses"]
    assert "application/problem+json" in admin_errors["401"]["content"]
    assert "application/problem+json" in admin_errors["409"]["content"]
    assert "Problem" in schema["components"]["schemas"]
    assert schema["components"]["securitySchemes"]["AdminToken"] == {
        "type": "apiKey",
        "in": "header",
        "name": "x-admin-token",
        "description": "관리자 API token (server-side only)",
    }
    assert schema["paths"]["/v1/admin/locations"]["post"]["security"] == [{"AdminToken": []}]


def test_checked_in_openapi_matches_runtime(api_client: TestClient) -> None:
    checked_in = json.loads(
        Path(__file__).resolve().parents[1].joinpath("openapi.json").read_text()
    )
    assert api_client.app.openapi() == checked_in


def test_production_requires_admin_token(tmp_path) -> None:
    settings = WeatherSettings(
        environment="production", database_url=TEST_DATABASE_URL
    )
    try:
        create_app(settings, WeatherRepository(settings.database_url))
    except RuntimeError as exc:
        assert "ADMIN_TOKEN" in str(exc)
    else:
        raise AssertionError("production app must fail closed without admin token")


def test_production_rejects_weak_admin_token() -> None:
    settings = WeatherSettings(
        _env_file=None,
        environment="production",
        database_url=TEST_DATABASE_URL,
        admin_token="change-this-token",
    )
    try:
        create_app(settings, WeatherRepository(settings.database_url))
    except RuntimeError as exc:
        assert "무작위" in str(exc)
    else:
        raise AssertionError("production app must reject weak admin tokens")


def test_provider_credential_admin_api_encrypts_and_redacts() -> None:
    encryption_key = Fernet.generate_key().decode("ascii")
    settings = WeatherSettings(
        _env_file=None,
        environment="development",
        database_url=TEST_DATABASE_URL,
        credential_encryption_key=encryption_key,
    )
    repository = WeatherRepository(settings.database_url)
    repository.create_schema()
    repository.delete_provider_credential("weatherapi")
    client = TestClient(create_app(settings, repository))
    secret = "weatherapi-admin-secret-1234"

    stored = client.put("/v1/admin/provider-credentials/weatherapi", json={"api_key": secret})
    assert stored.status_code == 200
    assert secret not in stored.text
    metadata = stored.json()["data"]
    assert metadata["provider"] == "weatherapi"
    assert metadata["configured"] is True
    assert metadata["source"] == "database"
    assert metadata["last4"] == "1234"
    assert metadata["fingerprint"] == f"sha256:{hashlib.sha256(secret.encode()).hexdigest()}"

    with repository.engine.connect() as connection:
        ciphertext = connection.execute(
            text(
                "SELECT ciphertext FROM weather_provider_credentials "
                "WHERE provider = 'weatherapi'"
            )
        ).scalar_one()
    assert secret not in ciphertext
    assert repository.get_provider_credential("weatherapi", encryption_key) == secret

    listed = client.get("/v1/admin/provider-credentials")
    assert listed.status_code == 200
    listed_metadata = next(
        item for item in listed.json()["data"] if item["provider"] == "weatherapi"
    )
    assert listed_metadata == metadata
    assert secret not in listed.text

    deleted = client.delete("/v1/admin/provider-credentials/weatherapi")
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {
        "provider": "weatherapi",
        "configured": False,
        "source": "none",
        "fingerprint": None,
        "last4": None,
        "updated_at": None,
    }


def test_provider_credential_environment_fallback_and_missing_encryption_key() -> None:
    settings = WeatherSettings(
        _env_file=None,
        environment="development",
        database_url=TEST_DATABASE_URL,
        weatherapi_api_key="environment-secret",
    )
    repository = WeatherRepository(settings.database_url)
    repository.create_schema()
    repository.delete_provider_credential("weatherapi")
    client = TestClient(create_app(settings, repository))

    listed = client.get("/v1/admin/provider-credentials")
    assert listed.status_code == 200
    metadata = next(item for item in listed.json()["data"] if item["provider"] == "weatherapi")
    assert metadata["configured"] is True
    assert metadata["source"] == "environment"
    assert metadata["last4"] == "cret"
    assert "environment-secret" not in listed.text

    short_settings = WeatherSettings(
        _env_file=None,
        environment="development",
        database_url=TEST_DATABASE_URL,
        weatherapi_api_key="abc",
    )
    short_client = TestClient(create_app(short_settings, repository))
    short_metadata = next(
        item
        for item in short_client.get("/v1/admin/provider-credentials").json()["data"]
        if item["provider"] == "weatherapi"
    )
    assert short_metadata["configured"] is True
    assert short_metadata["last4"] is None

    missing_key_settings = WeatherSettings(
        _env_file=None,
        environment="development",
        database_url=TEST_DATABASE_URL,
    )
    missing_key_client = TestClient(create_app(missing_key_settings, repository))
    response = missing_key_client.put(
        "/v1/admin/provider-credentials/weatherapi", json={"api_key": "must-not-persist"}
    )
    assert response.status_code == 503
    assert "must-not-persist" not in response.text
    assert repository.get_provider_credential_metadata("weatherapi") is None


def test_provider_credential_rejects_short_key() -> None:
    encryption_key = Fernet.generate_key().decode("ascii")
    settings = WeatherSettings(
        _env_file=None,
        environment="development",
        database_url=TEST_DATABASE_URL,
        credential_encryption_key=encryption_key,
    )
    repository = WeatherRepository(settings.database_url)
    repository.create_schema()
    repository.delete_provider_credential("weatherapi")
    client = TestClient(create_app(settings, repository))
    response = client.put(
        "/v1/admin/provider-credentials/weatherapi", json={"api_key": "abcd"}
    )
    assert response.status_code == 422
    assert repository.get_provider_credential_metadata("weatherapi") is None


def test_admin_session_revocation_is_durable_and_token_free() -> None:
    settings = WeatherSettings(
        _env_file=None,
        environment="development",
        database_url=TEST_DATABASE_URL,
    )
    repository = WeatherRepository(settings.database_url)
    repository.create_schema()
    client = TestClient(create_app(settings, repository))
    session = "opaque-session-value.for-test"

    revoked = client.post("/v1/admin/session-revocations/revoke", json={"session": session})
    assert revoked.status_code == 200
    assert session not in revoked.text
    checked = client.post("/v1/admin/session-revocations/check", json={"session": session})
    assert checked.status_code == 200
    assert checked.json() == {"revoked": True}
    other = client.post(
        "/v1/admin/session-revocations/check", json={"session": "another-session"}
    )
    assert other.status_code == 200
    assert other.json() == {"revoked": False}


def test_postgresql_repository_is_visible_to_testclient() -> None:
    settings = WeatherSettings(environment="development", database_url=TEST_DATABASE_URL)
    repository = WeatherRepository(settings.database_url)
    repository.create_schema()
    response = create_app(settings, repository).state.repository.list_locations()
    assert response == []
    client = TestClient(create_app(settings, repository))
    assert client.get("/v1/weather/locations").status_code == 200


def test_revisions_are_deduped_for_public_latest(tmp_path) -> None:
    settings = WeatherSettings(
        environment="development", database_url=TEST_DATABASE_URL
    )
    repo = WeatherRepository(settings.database_url)
    repo.create_schema()
    repo.upsert_location(
        WeatherLocation(location_id="x", name="X", latitude=37, longitude=127, nx=1, ny=1)
    )
    common = dict(
        location_id="x",
        provider="python-kma-api",
        dataset_key="kma_short_forecast",
        weather_domain="kma_short_forecast",
        forecast_style=ForecastStyle.SHORT,
        metric_key="TMP",
        issued_at=datetime(2026, 1, 1, tzinfo=UTC),
        valid_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
        target_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )
    first = WeatherValue(
        **common, value_number=Decimal("1"), payload={"v": 1}, source_record_key="sr-1"
    )
    second = WeatherValue(
        **common, value_number=Decimal("2"), payload={"v": 2}, source_record_key="sr-2"
    )
    repo.record_source(
        source_record_key="sr-1",
        provider="python-kma-api",
        dataset_key="kma_short_forecast",
        source_entity_type="weather_response",
        source_entity_id="x",
        payload={"rows": [1]},
    )
    repo.record_source(
        source_record_key="sr-2",
        provider="python-kma-api",
        dataset_key="kma_short_forecast",
        source_entity_type="weather_response",
        source_entity_id="x",
        payload={"rows": [2]},
    )
    repo.upsert_values([first, second])
    assert len(repo.latest_values("x")) == 1
    assert repo.latest_values("x")[0].value_number == Decimal("2")
    assert len(repo.timeline("x", include_revisions=True)) == 2


def test_admin_run_sources_exposes_redacted_lineage(api_client: TestClient) -> None:
    repo = api_client.app.state.repository
    run = repo.start_sync_run(provider="p", dataset_key="d", locations_total=1)
    repo.ingest_batch(
        source_records=[
            {
                "source_record_key": "run-source",
                "provider": "p",
                "dataset_key": "d",
                "source_entity_type": "weather_response",
                "source_entity_id": "grid:1:1",
                "payload": {
                    "rows": [{"TMP": "1"}],
                    "response_metadata": {
                        "endpoint": "/x",
                        "request_url": "https://example.test?serviceKey=URLSECRET",
                        "request_params": {"apiKey": "SECRET", "nested": {"token": "SECRET2"}},
                    },
                },
                "run_id": run.run_id,
            }
        ]
    )
    response = api_client.get(f"/v1/admin/sync-runs/{run.run_id}/sources")
    assert response.status_code == 200
    source = response.json()["data"][0]
    assert source["source_record_key"] == "run-source"
    assert source["row_count"] == 1
    assert "rows" not in source
    assert source["response_metadata"]["request_params"] == {
        "apiKey": "[REDACTED]",
        "nested": {"token": "[REDACTED]"},
    }
    assert source["response_metadata"]["request_url"] == (
        "https://example.test?serviceKey=[REDACTED]"
    )


def test_location_list_reports_total_for_pagination(api_client: TestClient) -> None:
    repository = api_client.app.state.repository
    for index in range(101):
        repository.upsert_location(
            WeatherLocation(
                location_id=f"location-{index}",
                name=f"Location {index:03d}",
                latitude=37,
                longitude=127,
                nx=1,
                ny=1,
            )
        )
    first = api_client.get("/v1/weather/locations?limit=100")
    second = api_client.get("/v1/weather/locations?limit=100&offset=100")
    assert first.status_code == 200 and len(first.json()["data"]) == 100
    assert first.json()["meta"]["page"]["total"] == 101
    assert len(second.json()["data"]) == 1
