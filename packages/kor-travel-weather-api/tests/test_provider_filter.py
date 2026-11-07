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


def test_weather_only_uses_same_provider_row_before_count_and_pagination(filtered_client):
    repo = filtered_client.app.state.repository
    now = datetime.now(UTC)
    for key, metric, domain, dataset in [
        ("cross-provider", "ALERT", "weather", "weather"),
        ("warning-domain", "TEXT", "WeatherWarning", "weather"),
        ("alert-dataset", "TEXT", "weather", "regional_ALERT"),
    ]:
        repo.upsert_location(WeatherLocation(location_id=key, name=key,
                                            latitude=37, longitude=127))
        repo.record_source(source_record_key=key, provider="python-kma-api", dataset_key=dataset,
                           source_entity_type="response", source_entity_id=key, payload={},
                           fetched_at=now)
        repo.upsert_values([WeatherValue(
            location_id=key, provider="python-kma-api", dataset_key=dataset,
            weather_domain=domain, forecast_style="observed", metric_key=metric,
            value_text="특보", target_at=now, known_at=now, source_record_key=key,
        )])
    repo.record_source(source_record_key="other-weather", provider="openweathermap",
                       dataset_key="weather", source_entity_type="response",
                       source_entity_id="cross-provider", payload={}, fetched_at=now)
    repo.upsert_values([WeatherValue(
        location_id="cross-provider", provider="openweathermap", dataset_key="weather",
        weather_domain="weather", forecast_style="short", metric_key="TEMP", value_number=20,
        target_at=now + timedelta(days=2), known_at=now - timedelta(days=1),
        source_record_key="other-weather",
    )])
    path = "/v1/weather/locations?provider=python-kma-api"
    assert filtered_client.get(path).json()["meta"]["page"]["total"] == 5
    body = filtered_client.get(path + "&weather_only=true&limit=1&offset=1").json()
    assert body["meta"]["page"]["total"] == 2
    assert [r["location_id"] for r in body["data"]] == ["only-a"]
    body = filtered_client.get(path + "&weather_only=true&search=cross-provider").json()
    assert body["data"] == [] and body["meta"]["page"]["total"] == 0
    # 일반값은 미래 예보도 포함한다. 수집 시각 6시간으로 자르면 안 된다.
    body = filtered_client.get("/v1/weather/locations?weather_only=true").json()
    expected = {"krforest-location", "only-a", "cross-provider"}
    assert {r["location_id"] for r in body["data"]} == expected
    body = filtered_client.get(path + "&provider=openweathermap&weather_only=true").json()
    assert {r["location_id"] for r in body["data"]} == expected


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


@pytest.mark.parametrize("style", ["nowcast", "ultra_short", "short", "mid", "observed"])
def test_kind_selects_before_detail_limit_and_catalog_count(filtered_client, style):
    repo = filtered_client.app.state.repository
    now = datetime.now(UTC)
    repo.upsert_values([WeatherValue(
        location_id="krforest-location", provider="openweathermap", dataset_key="weather",
        weather_domain="weather", forecast_style=style, metric_key="TEMP", value_number=7,
        target_at=now + timedelta(hours=1), known_at=now, collected_at=now,
        source_record_key="openweathermap-krforest-location",
    )])
    query = f"provider=openweathermap&kind={style}"
    catalog = filtered_client.get(f"/v1/weather/locations?{query}&limit=1").json()
    assert catalog["meta"]["page"]["total"] == 1
    assert catalog["data"][0]["location_id"] == "krforest-location"
    for history in ["false", "true"]:
        response = filtered_client.get(
            f"/v1/weather/locations/krforest-location/forecast?{query}&history={history}&limit=1",
        )
        assert response.status_code == 200, response.text
        rows = response.json()["data"]
        assert len(rows) == 1
        assert {row["forecast_style"] for row in rows} == {style}
        assert {row["provider"] for row in rows} == {"openweathermap"}


def test_kind_forecast_marker_keeps_future_values_separate_from_current(filtered_client):
    response = filtered_client.get(
        "/v1/weather/markers?location_id=krforest-location&provider=openweathermap&kind=short",
    )
    assert response.status_code == 200, response.text
    marker = response.json()["data"][0]
    assert marker["latest"] == []
    assert marker["forecast"]
    assert {row["forecast_style"] for row in marker["forecast"]} == {"short"}
    assert marker["alerts"] == []
    assert [row["target_at"] for row in marker["forecast"]] == sorted(
        row["target_at"] for row in marker["forecast"]
    )
    union = filtered_client.get(
        "/v1/weather/markers?location_id=krforest-location&kind=observed&kind=short",
    ).json()["data"][0]
    assert {row["forecast_style"] for row in union["latest"]} == {"observed"}
    assert {row["forecast_style"] for row in union["forecast"]} == {"short"}


def test_kind_marker_preserves_infrequent_metric_before_limit(filtered_client):
    repo = filtered_client.app.state.repository
    now = datetime.now(UTC)
    rows = [WeatherValue(
        location_id="krforest-location", provider="openweathermap", dataset_key="weather",
        weather_domain="weather", forecast_style="observed", metric_key="TEMP", value_number=15,
        target_at=now - timedelta(minutes=i), known_at=now,
        source_record_key="openweathermap-krforest-location",
    ) for i in range(81)]
    rows.append(WeatherValue(
        location_id="krforest-location", provider="openweathermap", dataset_key="weather",
        weather_domain="weather", forecast_style="observed", metric_key="SKY", value_number=3,
        target_at=now - timedelta(hours=3), known_at=now,
        source_record_key="openweathermap-krforest-location",
    ))
    repo.upsert_values(rows)
    marker = filtered_client.get(
        "/v1/weather/markers?location_id=krforest-location&provider=openweathermap&kind=observed",
    ).json()["data"][0]
    assert {row["metric_key"] for row in marker["latest"]} == {"TEMP", "SKY"}
    assert len(marker["latest"]) == 2


def test_alert_kind_is_disjoint_from_observed_and_matches_same_provider(filtered_client):
    repo = filtered_client.app.state.repository
    now = datetime.now(UTC)
    repo.upsert_location(WeatherLocation(location_id="alert-only", name="특보 전용",
                                        latitude=37, longitude=127,
                                        metadata={"kma_alert_area_codes": ["fixture-area"]}))
    repo.record_source(source_record_key="kind-alert", provider="python-kma-api",
                       dataset_key="regional_WARNING", source_entity_type="response",
                       source_entity_id="kind-alert", payload={}, fetched_at=now)
    repo.upsert_values([WeatherValue(
        location_id="alert-only", provider="python-kma-api", dataset_key="regional_WARNING",
        weather_domain="weather", forecast_style="observed", metric_key="TEXT",
        value_text="호우주의보 발표", target_at=now, known_at=now,
        payload={"areaCode": "fixture-area"},
        valid_until=now + timedelta(hours=6), source_record_key="kind-alert",
    )])
    path = "/v1/weather/locations?provider=python-kma-api&search=alert-only"
    assert filtered_client.get(path + "&kind=observed").json()["data"] == []
    assert filtered_client.get(path + "&kind=alert").json()["meta"]["page"]["total"] == 1
    assert filtered_client.get(path + "&kind=alert&provider=missing").json()["data"]
    assert filtered_client.get(
        "/v1/weather/locations?provider=openweathermap&kind=alert",
    ).json()["data"] == []
    rows = filtered_client.get(
        "/v1/weather/locations/alert-only/latest?kind=observed&limit=1",
    ).json()["data"]
    assert rows == []
    marker = filtered_client.get(
        "/v1/weather/markers?location_id=alert-only&kind=alert",
    ).json()["data"][0]
    assert marker["latest"] == [] and marker["forecast"] == []
    assert len(marker["alerts"]) == 1


@pytest.mark.parametrize("path", ["locations", "locations/krforest-location/latest",
                                  "locations/krforest-location/forecast", "markers"])
def test_unknown_kind_is_rejected(filtered_client, path):
    assert filtered_client.get(f"/v1/weather/{path}?kind=unknown").status_code == 422


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
