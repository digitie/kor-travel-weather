from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from kortravelweather_api.app import create_app

from kortravelweather import alert_event_store
from kortravelweather.alert_events import ALERT_DATASET, ALERT_PROVIDER, canonical_hash
from kortravelweather.models import kst_now
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings


@pytest.fixture
def stack(weather_test_database_url):
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    client = TestClient(
        create_app(
            WeatherSettings(environment="development", database_url=weather_test_database_url),
            repo,
        )
    )
    try:
        yield repo, client
    finally:
        client.close()
        repo.engine.dispose()


def raw(**changes):
    return {
        "stnId": "108",
        "tmFc": kst_now().strftime("%Y%m%d%H%M"),
        "tmSeq": "1",
        "areaCode": "L1082500",
        "areaName": "부산동부",
        "warnVar": "2",
        "warnStress": "0",
        "command": "1",
        "cancel": "0",
        **changes,
    }


def publish(repo, rows):
    payload = {"rows": rows, "internal_admin_note": "not-for-public"}
    run = repo.start_sync_run(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)
    repo.publish_alert_events(
        run_id=run.run_id,
        requests_fetched=1,
        source_records=[
            {
                "source_record_key": "api_alert_" + canonical_hash(payload),
                "provider": ALERT_PROVIDER,
                "dataset_key": ALERT_DATASET,
                "source_entity_type": "weather_response",
                "source_entity_id": "getPwnCd:108",
                "payload": payload,
            }
        ],
    )


def get(client, code="26350", **params):
    return client.get("/v1/weather/alert-events", params={"bjd_code": code, **params})


def test_actual_public_http_contract_exposes_verified_event_without_private_source(stack):
    repo, client = stack
    publish(repo, [raw()])
    response = get(client, "2635010100")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["namespace"] == "kr_bjd" and data["region_status"] == "known"
    assert data["mapping_coverage"] == "partial"
    assert data["fresh"] and data["events_complete"] and not data["absence_is_clear"]
    event = data["events"][0]
    assert event["area_name"] == "부산동부" and event["action"] == "issued"
    assert event["occurrence_id"] is None and event["scope_status"] == "verified"
    assert event["first_seen_at"] and event["source_record_key"]
    assert "not-for-public" not in response.text
    assert get(client, "26110").json()["data"]["events"] == []


@pytest.mark.parametrize("code", ["2635099999", "99999", "42190"])
def test_unknown_regions_remain_unknown_instead_of_empty_verified_success(stack, code):
    repo, client = stack
    publish(repo, [raw()])
    response = get(client, code)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["region_status"] == "unknown" and not data["fresh"] and data["events"] == []
    assert not data["absence_is_clear"]


@pytest.mark.parametrize("code", ["11B10101", "L1082500", "2635", "2635010"])
def test_namespace_or_unsupported_code_lengths_are_rejected(stack, code):
    assert get(stack[1], code).status_code == 422


@pytest.mark.parametrize(
    "since",
    [
        "2026-11-05T10:00:00",
        (kst_now() - timedelta(days=32)).isoformat(),
        (kst_now() + timedelta(days=1)).isoformat(),
    ],
)
def test_unsupported_history_window_is_explicit_422(stack, since):
    response = get(stack[1], since=since)
    assert response.status_code == 422
    assert "ALERT_HISTORY_WINDOW" in response.text


def test_missing_zero_success_and_provider_failure_are_distinct(stack):
    repo, client = stack
    missing = get(client).json()["data"]
    assert missing["collection_status"] == "unavailable" and not missing["fresh"]
    publish(repo, [])
    success = get(client).json()["data"]
    assert success["collection_status"] == "success" and success["fresh"]
    assert success["events"] == [] and not success["absence_is_clear"]
    run = repo.start_sync_run(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)
    repo.finish_sync_run(run.run_id, status="failed", error="ALERT_PROVIDER_RATE_LIMIT")
    failure = get(client).json()["data"]
    assert failure["last_error_code"] == "ALERT_PROVIDER_RATE_LIMIT"
    assert failure["last_success_at"] == success["last_success_at"] and not failure["fresh"]


def test_cancellation_and_changed_release_are_not_ordinary_release(stack):
    repo, client = stack
    publish(repo, [raw(), raw(cancel="1"), raw(tmSeq="2", command="8")])
    assert {event["action"] for event in get(client).json()["data"]["events"]} == {
        "issued",
        "cancelled",
        "changed_release",
    }


def test_before_mapping_verification_is_reported_as_unverified_scope(stack):
    repo, client = stack
    publish(repo, [raw(tmFc="202611041100")])
    data = get(client).json()["data"]
    assert data["events"] == [] and data["unverified_scope_count"] == 1
    assert data["mapping_valid_from"] and not data["absence_is_clear"]


def test_stale_success_is_not_fresh(stack, monkeypatch):
    repo, client = stack
    publish(repo, [raw()])
    later = kst_now() + timedelta(minutes=16)
    monkeypatch.setattr(alert_event_store, "kst_now", lambda: later)
    data = get(client).json()["data"]
    assert not data["fresh"] and data["events"]


def test_result_bound_is_not_silently_complete(stack, monkeypatch):
    repo, client = stack
    publish(repo, [raw(), raw(tmSeq="2")])
    monkeypatch.setattr(alert_event_store, "ALERT_QUERY_LIMIT", 1)
    data = get(client).json()["data"]
    assert len(data["events"]) == 1 and not data["events_complete"]
