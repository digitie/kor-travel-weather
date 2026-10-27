"""외부 SDK 예보의 승인 위치 union과 frozen cursor 건강 상태."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from khoa import Observatory, parse_beach_index_place
from test_export_external import external_repository as external_repository

from kortravelweather.export_artifacts import ExportArtifactStore
from kortravelweather.export_forecasts import FrozenForecastExports
from kortravelweather.export_heads import ExportHeads
from kortravelweather.export_sdk_collector import SdkExportCollector, sdk_descriptor
from kortravelweather.export_source_cohorts import declare_source_cohort
from kortravelweather.providers.khoa import (
    KHOA_BEACH_INDEX_DATASET,
    KHOA_PROVIDER,
    beach_index_source_record,
    beach_index_to_weather_values,
    beach_location,
)


def test_khoa_approved_rotation_union_and_failed_old_cursor_remain_stale(
    external_repository, tmp_path,
):
    repo = external_repository
    heads = ExportHeads(repo)
    places = [parse_beach_index_place(Observatory(
        id=f"reader-beach-{index}", name=f"해변{index}", latitude=36.31, longitude=126.513,
    ), [{
        "bbchNm": f"해변{index}", "lat": 36.31, "lot": 126.513,
        "predcYmd": (datetime.now(UTC) + timedelta(days=1)).date().isoformat(),
        "predcNoonSeCd": "오전", "maxWvhgt": "0.7", "avgArtmp": "17.2",
    }]) for index in range(2)]
    locations = [beach_location(place) for place in places]
    for location in locations:
        repo.upsert_location(location)

    def publish(selected):
        run = repo.start_sync_run(
            provider=KHOA_PROVIDER, dataset_key=KHOA_BEACH_INDEX_DATASET,
            export_cohort_required=True,
        )
        declare_source_cohort(
            repo, run_id=run.run_id, provider=KHOA_PROVIDER, dataset_key=KHOA_BEACH_INDEX_DATASET,
            descriptors=[sdk_descriptor(location, provider=KHOA_PROVIDER,
                                        dataset_key=KHOA_BEACH_INDEX_DATASET)
                         for location in selected],
        )
        collector = SdkExportCollector(repo, run_id=run.run_id, artifact_root=tmp_path)
        prepared = [collector.prepare(
            location=location, provider=KHOA_PROVIDER, dataset_key=KHOA_BEACH_INDEX_DATASET,
        ) for location in selected]
        for location, plan in zip(selected, prepared, strict=True):
            place = next(place for place in places
                         if beach_location(place).location_id == location.location_id)
            fetched = datetime.now(UTC)
            source = beach_index_source_record(place, location_id=location.location_id,
                                                fetched_at=fetched)
            source["run_id"] = run.run_id
            collector.ingest(plan, record=source, values=beach_index_to_weather_values(
                place, location_id=location.location_id,
                source_record_key=source["source_record_key"], known_at=fetched,
            ))
        repo.finish_sync_run(run.run_id, status="success")
        for plan in prepared:
            assert heads.promote(plan.identity, ExportArtifactStore(tmp_path)) == "PUBLISHED"

    publish(locations)
    reader = FrozenForecastExports(
        repo, ExportArtifactStore(tmp_path), signing_key="khoa-forecast-fixture",
        style="external", providers=[KHOA_PROVIDER], source_inventory=True,
        expected_sources=[(KHOA_PROVIDER, KHOA_BEACH_INDEX_DATASET)],
    )
    first, _ = reader.page(limit=1, cursor=None)
    cursor = first["meta"]["next_cursor"]
    assert first["meta"]["source_status"] == "ok" and cursor
    publish(locations[:1])
    rotated, healthy_etag = reader.page(limit=5000, cursor=None)
    assert rotated["meta"]["source_status"] == "ok" and len(rotated["items"]) == 2
    failed = repo.start_sync_run(
        provider=KHOA_PROVIDER, dataset_key=KHOA_BEACH_INDEX_DATASET, export_cohort_required=True,
    )
    repo.finish_sync_run(failed.run_id, status="failed")
    stale, changed = reader.page(limit=5000, cursor=None)
    assert stale["meta"]["source_status"] == "stale" and changed != healthy_etag
    continued, _ = reader.page(limit=1, cursor=cursor)
    assert continued["meta"]["source_status"] == "stale"


class _Files:
    def __init__(self, points):
        self.points = points

    def guard(self, **_):
        return nullcontext()

    def read(self, digest, **_):
        return iter(self.points[digest])


def test_location_with_only_past_targets_is_omitted_and_page_is_stale():
    """한 위치의 최신 승인 예보가 모두 지나도 style 전체를503으로 막지 않는다."""
    now = datetime.now(UTC)
    points = [{
        "location_id": key, "provider": "python-kma-api", "forecast_style": "ultra_short",
        "target_at": (now + timedelta(hours=hours)).isoformat(),
        "known_at": (now - timedelta(minutes=30)).isoformat(),
        "metric_key": "T1H", "value_number": "18.4", "value_text": None,
    } for key, hours in (("expired", -2), ("expired", -1), ("live", 1), ("live", 2))]
    reader = FrozenForecastExports(
        SimpleNamespace(), _Files({"chunk": points}), signing_key="omit-fixture",
        style="ultra_short",
    )
    keys = ["expired", "live"]
    scope = {
        "namespace": "n" * 64, "source_scope": "s" * 64, "generation_id": "g",
        "nx": 60, "ny": 127, "provider": "python-kma-api", "forecast_style": "ultra_short",
        "location_ids": keys, "approved_location_ids": keys,
        "dataset_key": "kma_ultra_short_forecast", "source_part": "main",
        "refs": [{"artifact_digest": "chunk", "byte_count": 100, "point_count": 4,
                  "location_ids": keys}],
        "published_at": now.isoformat(), "source_collected_at": now.isoformat(),
        "data_revision_at": now.isoformat(),
    }
    frozen = {
        "snapshot_id": "00000000-0000-0000-0000-000000000002",
        "manifest": {"selection": reader.selection, "as_of": now.isoformat(), "scopes": [scope],
                     "next_since": now.isoformat(), "unconfirmed_sources": False},
        "source_collected_at": now, "created_at": now,
    }
    reader._freeze_forecasts = lambda: frozen
    reader._health = lambda *_: False
    page, _ = reader.page(limit=5000, cursor=None)
    assert [item["location_id"] for item in page["items"]] == ["live"]
    assert page["meta"]["source_status"] == "stale" and page["meta"]["has_more"] is False
    # 빠진 위치는 cursor 위치만 넘긴다. 다음 페이지가 같은 위치에서 멈추지 않는다.
    first, _ = reader.page(limit=1, cursor=None)
    assert [item["location_id"] for item in first["items"]] == ["live"]
    assert first["meta"]["has_more"] is False and first["meta"]["source_status"] == "stale"
