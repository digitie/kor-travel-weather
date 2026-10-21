"""필터는 제공처의 카탈로그 namespace가 아닌 저장 값에 적용하고 LIMIT 전에 선택한다."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from kortravelweather_api.app import create_app

from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings


@pytest.fixture
def filtered_client(weather_test_database_url):
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    now = datetime.now(UTC)
    for key in ["krforest-location", "only-a", "empty", "disabled"]:
        repo.upsert_location(WeatherLocation(location_id=key, name=key, latitude=37, longitude=127,
                                            enabled=key != "disabled"))
    for provider in ["python-kma-api", "openweathermap"]:
        for location in ["krforest-location", "only-a", "disabled"]:
            if location == "only-a" and provider == "openweathermap":
                continue
            source = f"{provider}-{location}"
            repo.record_source(source_record_key=source, provider=provider, dataset_key="weather",
                               source_entity_type="response", source_entity_id=location,
                               payload={}, fetched_at=now)
            rows = []
            for style in ["observed", "short"]:
                for offset in range(3):
                    rows.append(WeatherValue(
                        location_id=location, provider=provider, dataset_key="weather",
                        weather_domain="weather", forecast_style=style, metric_key="TEMP",
                        value_number=10 if provider == "python-kma-api" else 20,
                        target_at=now + timedelta(hours=offset if style == "short" else -offset),
                        known_at=now, collected_at=now, source_record_key=source,
                    ))
            repo.upsert_values(rows)
    settings = WeatherSettings(environment="development", database_url=weather_test_database_url)
    with TestClient(create_app(settings, repo)) as client:
        yield client
    repo.engine.dispose()


def test_provider_catalog_filter_uses_values_not_location_prefix(filtered_client):
    client = filtered_client
    path = "/v1/weather/locations?provider=openweathermap&limit=1"
    body = client.get(path).json()
    assert [row["location_id"] for row in body["data"]] == ["krforest-location"]
    assert body["meta"]["page"]["total"] == 1
    assert client.get(path + "&offset=1").json()["data"] == []
    assert len(client.get("/v1/weather/locations").json()["data"]) == 3
    union = client.get(
        "/v1/weather/locations?provider=openweathermap&provider=python-kma-api",
    ).json()
    assert {r["location_id"] for r in union["data"]} == {"krforest-location", "only-a"}
    assert client.get("/v1/weather/locations?provider=missing").json()["data"] == []


@pytest.mark.parametrize("suffix", [
    "latest?limit=1", "forecast?limit=1", "forecast?history=true&limit=1",
])
def test_detail_filter_runs_before_limit(filtered_client, suffix):
    response = filtered_client.get(
        f"/v1/weather/locations/krforest-location/{suffix}&provider=openweathermap",
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data) == 1
    assert {row["provider"] for row in data} == {"openweathermap"}


def test_marker_filter_and_empty_provider(filtered_client):
    path = "/v1/weather/markers?location_id=krforest-location"
    body = filtered_client.get(path + "&provider=openweathermap").json()
    assert body["data"][0]["latest"]
    assert {r["provider"] for r in body["data"][0]["latest"]} == {"openweathermap"}
    body = filtered_client.get(path + "&provider=missing").json()
    assert body["data"][0]["latest"] == []
    assert filtered_client.get(path + "&provider=").status_code == 422
    assert filtered_client.get(path + "&provider=a" * 33).status_code == 422


def test_filter_preserves_each_marker_metric_and_all_alert_events(filtered_client):
    repo = filtered_client.app.state.repository
    now = datetime.now(UTC)
    provider = "openweathermap"
    location = "krforest-location"
    repo.record_source(source_record_key="marker-many", provider=provider, dataset_key="weather",
                       source_entity_type="response", source_entity_id="many", payload={},
                       fetched_at=now)
    rows = [WeatherValue(
        location_id=location, provider=provider, dataset_key="weather", weather_domain="weather",
        forecast_style="observed", metric_key="TEMP", value_number=15,
        target_at=now - timedelta(minutes=i), known_at=now, source_record_key="marker-many",
    ) for i in range(81)]
    rows.append(WeatherValue(
        location_id=location, provider=provider, dataset_key="weather", weather_domain="weather",
        forecast_style="observed", metric_key="SKY", value_number=3,
        target_at=now - timedelta(hours=3), known_at=now - timedelta(hours=3),
        source_record_key="marker-many",
    ))
    repo.upsert_values(rows)
    for i in range(21):
        source = f"event-{i}"
        repo.record_source(source_record_key=source, provider=provider, dataset_key="weather_alert",
                           source_entity_type="response", source_entity_id=source, payload={},
                           fetched_at=now)
        repo.upsert_values([WeatherValue(
            location_id=location, provider=provider, dataset_key="weather_alert",
            weather_domain="weather_alert", forecast_style="observed", metric_key="ALERT",
            value_text="호우주의보 발표", target_at=now, known_at=now + timedelta(microseconds=i),
            valid_until=now + timedelta(days=1), source_record_key=source,
            payload={"stn_id": str(i), "seq": "1"},
        )])
    path = f"/v1/weather/markers?location_id={location}&provider={provider}"
    response = filtered_client.get(path)
    assert response.status_code == 200, response.text
    marker = response.json()["data"][0]
    assert {r["metric_key"] for r in marker["latest"]} == {"TEMP", "SKY"}
    assert len(marker["alerts"]) == 21
    assert {r["source_record_key"] for r in marker["alerts"]} == {f"event-{i}" for i in range(21)}


def test_provider_marker_plan_uses_bounded_index_prefix(filtered_client):
    import json

    from sqlalchemy import event

    repo = filtered_client.app.state.repository
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(repo.engine, "before_cursor_execute", record)
    try:
        repo.marker_values_many(["krforest-location"], providers=["openweathermap"])
    finally:
        event.remove(repo.engine, "before_cursor_execute", record)
    statement, params = statements[0]
    with repo.engine.connect() as connection:
        # 작은 fixture도 실제 partial/expression index에 맞는지 확인한다.
        connection.exec_driver_sql("SET LOCAL enable_seqscan = off")
        plan = connection.exec_driver_sql("EXPLAIN (FORMAT JSON) " + statement, params).scalar_one()
    encoded = json.dumps(plan)
    assert "ix_weather_current_values_provider_marker" in encoded
    def nodes(node):
        yield node
        for child in node.get("Plans", []):
            yield from nodes(child)

    indexed = next(node for node in nodes(plan[0]["Plan"])
                   if node.get("Index Name") == "ix_weather_current_values_provider_marker")
    condition = indexed["Index Cond"]
    assert all(column in condition for column in ("location_id", "provider", "metric_key"))
    assert "unnest" in condition
