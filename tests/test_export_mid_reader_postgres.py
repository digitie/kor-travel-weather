"""실제 PG native/capture/승인 inventory를 통과한 중기 두 역할의 frozen 읽기."""

import asyncio
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from kma import DataGoKrClient
from kma.time_utils import latest_mid_fcst_time
from kortravelweather_dagster.kma_weather import _source_spec
from sqlalchemy import event, select, update
from test_export_external import external_repository as external_repository
from test_export_mid import _first_day, _raw

from kortravelweather.export_mid import MidScopeDescriptor, normalize_mid_source
from kortravelweather.export_mid_collector import MidExportCollector
from kortravelweather.export_mid_reader import FrozenMidForecastExports
from kortravelweather.export_snapshots import (
    ExportCursorInvalid,
    ExportSnapshotExpired,
    ExportSnapshotUnavailable,
)
from kortravelweather.export_source_cohorts import declare_source_cohort
from kortravelweather.models import WeatherLocation


def _locations(repo):
    for key in ("mid-a", "mid-b", "mid-c"):
        repo.create_location(WeatherLocation(
            location_id=key, name=key, latitude=37.5, longitude=127,
            metadata={"mid_land_region_code": "11B00000",
                      "mid_temperature_region_code": "11B10101"},
        ))


def _collect(repo, root, *, publish=("land", "temperature"), seed=True):
    if seed:
        _locations(repo)
    descriptors = [MidScopeDescriptor(
        entity_id=f"mid-{part}:{region}", source_part=part, region_code=region,
        location_ids=("mid-a", "mid-b", "mid-c"),
        normalizer_revision=f"kma-mid-{part}-v1",
    ) for part, region in (("land", "11B00000"), ("temperature", "11B10101"))]
    run = repo.start_sync_run(
        provider="python-kma-api", dataset_key="kma_mid_forecast", export_cohort_required=True,
    )
    declare_source_cohort(repo, run_id=run.run_id, provider="python-kma-api",
                          dataset_key="kma_mid_forecast", descriptors=descriptors)
    collector = MidExportCollector(repo, run_id=run.run_id, artifact_root=root)
    prepared = []
    for descriptor in descriptors:
        plan = collector.prepare(descriptor=descriptor)
        assert plan is not None
        prepared.append(plan)
    tm_fc = latest_mid_fcst_time(datetime.now(UTC))

    async def response(part):
        # 현재 회차가 공개하는 형식(06시 D+4, 18시 D+5 시작)으로 응답한다.
        raw = _raw(part, first=_first_day(tm_fc))

        def handle(incoming):
            assert incoming.url.params["tmFc"] == tm_fc
            assert incoming.url.params["regId"] == raw["regId"]
            return httpx.Response(200, json={"response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                "body": {"items": {"item": [raw]}, "totalCount": 1,
                         "pageNo": 1, "numOfRows": 10},
            }})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as session:
            client = DataGoKrClient("offline-only", session=session, retries=0)
            method = client.mid_land_forecast if part == "land" else client.mid_temperature_forecast
            return await method(reg_id=raw["regId"], tm_fc=tm_fc)

    for item in prepared:
        rows = asyncio.run(response(item.descriptor.source_part))
        record = {**_source_spec("kma_mid_forecast", item.descriptor.entity_id,
                                rows, datetime.now(UTC)), "run_id": run.run_id}
        values = list(normalize_mid_source(item.descriptor, record))
        assert collector.ingest(item, record=record, values=values) == (len(values) if seed else 0)
    repo.finish_sync_run(run.run_id, status="success")
    for item in prepared:
        if item.descriptor.source_part in publish:
            assert collector.heads.promote(item.identity, collector.artifacts) == "PUBLISHED"
    return collector, prepared


def _reader(repo, collector, **kwargs):
    return FrozenMidForecastExports(repo, collector.artifacts, signing_key="offline-key", **kwargs)


def test_actual_mid_heads_pair_null_align_and_paginate_without_history_read(
    external_repository, tmp_path,
):
    repo = external_repository
    collector, _ = _collect(repo, tmp_path)
    handle = _reader(repo, collector)

    def no_history_query(_connection, _cursor, statement, _parameters, _context, _many):
        assert not re.search(r"\bweather_(values|current_values)\b", statement)

    event.listen(repo.engine, "before_cursor_execute", no_history_query)
    first, etag = handle.page(limit=1, cursor=None)
    assert first["meta"]["source_status"] == "ok"
    assert first["meta"]["has_more"] and first["meta"]["item_count"] == 1
    assert first["items"][0]["metrics"]["SKY"][0] == "구름많음"
    assert any(value is None for value in first["items"][0]["metrics"]["TMN"])
    times = first["items"][0]["target_at"]
    assert times == sorted(times) and all(len(a) == len(times)
                                        for a in first["items"][0]["metrics"].values())
    cursor, keys = first["meta"]["next_cursor"], [first["items"][0]["location_id"]]
    while cursor:
        page, _ = handle.page(limit=1, cursor=cursor)
        assert page["meta"]["source_status"] == "ok"
        keys.extend(item["location_id"] for item in page["items"])
        cursor = page["meta"]["next_cursor"]
    assert set(keys) == {"mid-a", "mid-b", "mid-c"} and len(keys) == 3
    again, same_etag = handle.page(limit=1, cursor=None)
    assert again["meta"]["snapshot_id"] == first["meta"]["snapshot_id"]
    assert same_etag == etag
    with pytest.raises(ExportCursorInvalid):
        handle.page(limit=2, cursor=first["meta"]["next_cursor"])
    with pytest.raises(ExportCursorInvalid):
        handle.page(limit=1, cursor=first["meta"]["next_cursor"] + "bad")
    event.remove(repo.engine, "before_cursor_execute", no_history_query)


def test_actual_partial_late_publication_preserves_old_vector_stale_then_delta(
    external_repository, tmp_path,
):
    repo = external_repository
    collector, _ = _collect(repo, tmp_path)
    handle = _reader(repo, collector)
    first, healthy_etag = handle.page(limit=1, cursor=None)
    old_cursor = first["meta"]["next_cursor"]
    since = datetime.fromisoformat(first["meta"]["next_since"])
    later, prepared = _collect(repo, tmp_path, seed=False, publish=("land",))
    incomplete, stale_etag = handle.page(limit=1, cursor=None)
    assert incomplete["items"] == first["items"]
    assert incomplete["meta"]["source_status"] == "stale"
    assert incomplete["meta"]["snapshot_id"] == first["meta"]["snapshot_id"]
    assert stale_etag != healthy_etag
    cursor_page, _ = handle.page(limit=1, cursor=old_cursor)
    assert cursor_page["meta"]["source_status"] == "stale"
    assert later.heads.promote(prepared[1].identity, later.artifacts) == "PUBLISHED"
    complete, _ = handle.page(limit=5000, cursor=None)
    assert complete["meta"]["source_status"] == "ok" and len(complete["items"]) == 3
    assert complete["meta"]["snapshot_id"] != first["meta"]["snapshot_id"]
    # 같은 의미의 파일은 재사용하고 fact clock과 실제 새 수집 clock을 구분한다.
    assert complete["items"][0]["known_at"] == first["items"][0]["known_at"]
    assert complete["meta"]["source_collected_at"] > first["meta"]["source_collected_at"]
    delta, _ = _reader(repo, later, since=since).page(limit=5000, cursor=None)
    assert len(delta["items"]) == 3 and delta["meta"]["source_status"] == "ok"
    assert datetime.fromisoformat(delta["meta"]["next_since"]) > since
    empty, _ = _reader(repo, later, since=datetime.fromisoformat(
        delta["meta"]["next_since"],
    )).page(limit=5000, cursor=None)
    assert empty["items"] == [] and not empty["meta"]["has_more"]


def test_actual_failed_native_intent_and_catalog_change_never_look_healthy(
    external_repository, tmp_path,
):
    repo = external_repository
    collector, _ = _collect(repo, tmp_path)
    handle = _reader(repo, collector)
    first, etag = handle.page(limit=1, cursor=None)
    run = repo.start_sync_run(
        provider="python-kma-api", dataset_key="kma_mid_forecast", export_cohort_required=True,
    )
    repo.finish_sync_run(run.run_id, status="failed", error="offline failure")
    failed, stale_etag = handle.page(limit=1, cursor=None)
    assert failed["items"] == first["items"] and failed["meta"]["source_status"] == "stale"
    assert etag != stale_etag
    old, _ = handle.page(limit=1, cursor=first["meta"]["next_cursor"])
    assert old["meta"]["source_status"] == "stale"
    # 별도 selection에 이전 완전 snapshot이 없으면 실패를 빈 정상 집합으로 만들지 않는다.
    with pytest.raises(ExportSnapshotUnavailable):
        _reader(repo, collector, since=datetime.fromisoformat(
            first["meta"]["next_since"],
        )).page(limit=1, cursor=None)
    catalog = collector.heads.metadata.tables["weather_locations"]
    with repo.engine.begin() as connection:
        connection.execute(catalog.update().where(catalog.c.location_id == "mid-c").values(
            latitude=38.5,
        ))
    lost, _ = handle.page(limit=1, cursor=None)
    assert lost["meta"]["source_status"] == "stale"
    assert lost["meta"]["snapshot_id"] == first["meta"]["snapshot_id"]


def test_actual_incompatible_issue_and_ttl_are_fail_closed(external_repository, tmp_path):
    repo = external_repository
    collector, _ = _collect(repo, tmp_path)
    handle = _reader(repo, collector)
    first, _ = handle.page(limit=1, cursor=None)
    # 고립된 시험 DB에서 metadata 회차 불일치를 주입한다. 원천 HTTP나 운영값은 건드리지 않는다.
    generations = collector.heads.generations
    plans = collector.heads.plans
    with repo.engine.begin() as connection:
        temperature = connection.scalar(select(generations.c.generation_id).join(
            plans, plans.c.attempt_id == generations.c.attempt_id,
        ).where(plans.c.descriptor["source_part"].as_string() == "temperature"))
        connection.execute(update(generations).where(
            generations.c.generation_id == temperature,
        ).values(data_revision_at=datetime.now(UTC) - timedelta(days=1)))
        connection.execute(update(collector.heads.heads).where(
            collector.heads.heads.c.generation_id == temperature,
        ).values(data_revision_at=datetime.now(UTC) - timedelta(days=1)))
    incompatible, _ = handle.page(limit=1, cursor=None)
    assert incompatible["meta"]["source_status"] == "stale"
    assert incompatible["items"] == first["items"]
    with repo.engine.begin() as connection:
        connection.execute(update(handle.snapshots).where(
            handle.snapshots.c.snapshot_id == first["meta"]["snapshot_id"],
        ).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    with pytest.raises(ExportSnapshotExpired):
        handle.page(limit=1, cursor=first["meta"]["next_cursor"])


def test_actual_missing_role_without_previous_vector_returns_unavailable(
    external_repository, tmp_path,
):
    repo = external_repository
    collector, prepared = _collect(repo, tmp_path, publish=("land",))
    with pytest.raises(ExportSnapshotUnavailable):
        _reader(repo, collector).page(limit=5000, cursor=None)
    assert collector.heads.promote(prepared[1].identity, collector.artifacts) == "PUBLISHED"
    page, _ = _reader(repo, collector).page(limit=5000, cursor=None)
    assert page["meta"]["source_status"] == "ok" and len(page["items"]) == 3
