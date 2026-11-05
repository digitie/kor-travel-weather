from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from kortravelweather.alert_event_store import purge_alert_events
from kortravelweather.alert_events import (
    ALERT_DATASET,
    ALERT_PROVIDER,
    canonical_hash,
    normalize_alert,
)
from kortravelweather.models import kst_now
from kortravelweather.repository import (
    PURGE_SOURCE_RECORDS_SQL,
    SourceRecordRow,
    WeatherAlertEventRow,
    WeatherRepository,
)


def raw_row(**changes):
    return {
        "stnId": "108",
        "tmFc": kst_now().strftime("%Y%m%d%H%M"),
        "tmSeq": "12",
        "areaCode": "L1082500",
        "areaName": "부산동부",
        "warnVar": "2",
        "warnStress": "0",
        "command": "1",
        "cancel": "0",
        "startTime": "202611051200",
        **changes,
    }


def source(rows):
    payload = {"rows": rows, "query": {"stnId": "108", "fromTmFc": "20261103"}}
    return {
        "source_record_key": "alertsrc_" + canonical_hash(payload),
        "provider": ALERT_PROVIDER,
        "dataset_key": ALERT_DATASET,
        "source_entity_type": "weather_response",
        "source_entity_id": "getPwnCd:108",
        "payload": payload,
        "fetched_at": kst_now(),
    }


@pytest.fixture
def repo(weather_test_database_url):
    result = WeatherRepository(weather_test_database_url)
    result.create_schema()
    try:
        yield result
    finally:
        result.engine.dispose()


def publish(repo, sources):
    run = repo.start_sync_run(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)
    return repo.publish_alert_events(
        run_id=run.run_id,
        source_records=sources,
        requests_fetched=len(sources),
    )


def read(repo, bjd="26350"):
    return repo.read_alert_events(bjd_code=bjd, since=kst_now() - timedelta(days=1))


def test_raw_event_and_run_publish_atomically_with_source_lineage(repo):
    loaded, run = publish(repo, [source([raw_row()])])
    assert loaded == 1 and run.status == "success" and run.values_loaded == 1
    body = read(repo)
    assert body["fresh"] and body["events_complete"] and not body["absence_is_clear"]
    assert len(body["events"]) == 1
    event = body["events"][0]
    assert event["action"] == "issued" and event["scope_status"] == "verified"
    assert event["occurrence_id"] is None
    assert event["source_record_key"] == repo.list_sync_run_sources(run.run_id)[0]
    assert repo.get_source_record(event["source_record_key"]) is not None
    assert read(repo, "26110")["events"] == []


def test_recollection_and_page_regrouping_do_not_create_new_events(repo):
    one, two = raw_row(), raw_row(tmSeq="13", command="2", endTime="202611051400")
    publish(repo, [source([one, two])])
    before = {e["event_id"]: e["first_seen_at"] for e in read(repo)["events"]}
    loaded, run = publish(repo, [source([two]), source([one])])
    assert loaded == 0 and run.status == "success"
    assert {e["event_id"]: e["first_seen_at"] for e in read(repo)["events"]} == before


def test_source_replay_uses_existing_canonical_primary_key(repo):
    record = source([raw_row()])
    original = {**record, "source_record_key": "old_public_source_alias"}
    repo.record_source(**original)
    publish(repo, [record])
    assert read(repo)["events"][0]["source_record_key"] == original["source_record_key"]


def test_bad_later_source_rolls_back_earlier_source_and_events(repo):
    run = repo.start_sync_run(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)
    first = source([raw_row()])
    second = {**source([raw_row(tmSeq="13")]), "provider": "wrong"}
    with pytest.raises(ValueError, match="ALERT_SOURCE_SCOPE"):
        repo.publish_alert_events(
            run_id=run.run_id,
            source_records=[first, second],
            requests_fetched=2,
        )
    with repo._session_factory() as session:
        assert session.scalar(select(func.count()).select_from(WeatherAlertEventRow)) == 0
        assert session.scalar(select(func.count()).select_from(SourceRecordRow)) == 0
    assert repo.get_sync_run(run.run_id).status == "running"
    repo.finish_sync_run(run.run_id, status="failed", error="test_cleanup")


def test_lost_run_cannot_publish_after_recovery(repo):
    run = repo.start_sync_run(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)
    repo.finish_sync_run(run.run_id, status="failed", error="recovered")
    with pytest.raises(ValueError, match="ALERT_RUN_OWNERSHIP_LOST"):
        repo.publish_alert_events(
            run_id=run.run_id,
            source_records=[source([raw_row()])],
            requests_fetched=1,
        )
    assert read(repo)["events"] == []
    assert not read(repo)["fresh"]


def test_empty_success_differs_from_missing_or_failed_collection(repo):
    missing = read(repo)
    assert missing["collection_status"] == "unavailable" and not missing["fresh"]
    publish(repo, [source([])])
    success = read(repo)
    assert success["collection_status"] == "success" and success["fresh"]
    assert success["events"] == [] and success["events_complete"]
    failed = repo.start_sync_run(provider=ALERT_PROVIDER, dataset_key=ALERT_DATASET)
    repo.finish_sync_run(failed.run_id, status="failed", error="secret/provider/token")
    failure = read(repo)
    assert failure["last_success_at"] == success["last_success_at"]
    assert failure["last_error_code"] == "ALERT_COLLECTION_FAILED" and not failure["fresh"]
    assert "secret" not in str(failure)


def test_unknown_partial_area_never_becomes_whole_district_alert(repo):
    publish(repo, [source([raw_row(areaCode="L1110100", areaName="인천")])])
    assert read(repo, "28110")["events"] == []
    unknown = read(repo, "99999")
    assert unknown["region_status"] == "unknown" and unknown["events"] == []
    assert not unknown["fresh"] and unknown["mapping_coverage"] == "partial"


def test_events_are_immutable_and_keep_source_alive_during_gc(repo):
    publish(repo, [source([raw_row()])])
    event = read(repo)["events"][0]
    with pytest.raises(DBAPIError, match="immutable"), repo._session_factory.begin() as session:
        session.execute(text("UPDATE weather_alert_events SET area_code='L1082700'"))
    with repo._session_factory.begin() as session:
        session.execute(text("DELETE FROM weather_sync_run_sources"))
        session.execute(text("SET LOCAL kortravelweather.purge = 'on'"))
        removed = session.execute(
            text(PURGE_SOURCE_RECORDS_SQL),
            {"cutoff": kst_now() + timedelta(days=1)},
        ).rowcount
        assert removed == 0
    assert repo.get_source_record(event["source_record_key"]) is not None


def test_retention_keeps_recent_event_even_when_other_history_cutoff_is_future(repo):
    publish(repo, [source([raw_row()])])
    with repo._session_factory.begin() as session:
        session.execute(text("SET LOCAL kortravelweather.purge = 'on'"))
        assert purge_alert_events(session, cutoff=kst_now() + timedelta(days=1)) == 0
    assert len(read(repo)["events"]) == 1


@pytest.mark.parametrize(
    "since",
    [
        kst_now().replace(tzinfo=None),
        kst_now() - timedelta(days=32),
        kst_now() + timedelta(days=1),
    ],
)
def test_history_window_errors_are_explicit(repo, since):
    with pytest.raises(ValueError, match="ALERT_HISTORY_WINDOW"):
        repo.read_alert_events(bjd_code="26350", since=since)


def test_cancel_and_change_release_are_preserved_as_different_observations(repo):
    publish(
        repo,
        [
            source(
                [
                    raw_row(),
                    raw_row(cancel="1"),
                    raw_row(tmSeq="13", command="8", endTime="202611051400"),
                ]
            )
        ],
    )
    assert {event["action"] for event in read(repo)["events"]} == {
        "issued",
        "cancelled",
        "changed_release",
    }
    assert normalize_alert(raw_row()).occurrence_id is None


def test_expired_event_and_unreferenced_source_are_collected_after_31_days(repo, monkeypatch):
    from kortravelweather import alert_event_store as module

    publish(repo, [source([raw_row()])])
    future = kst_now() + timedelta(days=32)
    monkeypatch.setattr(module, "kst_now", lambda: future)
    with repo._session_factory.begin() as session:
        session.execute(text("SET LOCAL kortravelweather.purge = 'on'"))
        assert purge_alert_events(session, cutoff=future) == 1
        session.execute(text("DELETE FROM weather_sync_run_sources"))
        assert session.execute(text(PURGE_SOURCE_RECORDS_SQL), {"cutoff": future}).rowcount == 1
