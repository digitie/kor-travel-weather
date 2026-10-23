"""외부 SDK 예보의 승인 위치 union과 frozen cursor 건강 상태."""

from datetime import UTC, datetime, timedelta

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
