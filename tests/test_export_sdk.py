"""공개 parser 기반 원문 재해석은 값 자체의 digest를 증거로 삼지 않는다."""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from airkorea import parse_measurement
from khoa import Observatory, parse_beach_index_place
from krex import parse_rest_area_weather
from krforest.parser import parse_forest_dust_measurement, parse_mountain_weather
from pydantic import ValidationError

from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_sdk import SdkScopeDescriptor, normalize_sdk_source
from kortravelweather.models import KST
from kortravelweather.providers import airkorea, khoa, krex, krforest, krforest_dust

AT = datetime(2026, 10, 23, 12, tzinfo=KST)


def fixture(kind):
    observed = AT - timedelta(hours=1)
    if kind == "air":
        raw = {"stationName": "종로구", "sidoName": "서울",
               "dataTime": observed.strftime("%Y-%m-%d %H:%M"),
               "pm10Value": "35", "pm25Value": "17"}
        parsed = parse_measurement(raw)
        source = airkorea.measurement_source_record(parsed, location_id="airkorea-a", fetched_at=AT)
        desc = SdkScopeDescriptor(
            provider=airkorea.AIRKOREA_PROVIDER, dataset_key=airkorea.AIRKOREA_MEASUREMENT_DATASET,
            entity_type="airkorea_station", entity_id="airkorea-a", location_ids=("airkorea-a",),
            forecast_style="observed", latitude=37.5, longitude=127,
            station_id="a", station_name="종로구", sido_name="서울",
        )
        adapter = airkorea.measurement_to_weather_values
    elif kind == "mountain":
        raw = {"obsId": "M001", "obsName": "태백산", "latitude": 37.5, "longitude": 127,
               "tm": observed.strftime("%Y-%m-%d %H:%M"),
               "tm2m": "-2", "hm2m": "75", "ws2m": "1.2"}
        parsed = parse_mountain_weather(raw)
        source = krforest.mountain_source_record(parsed, location_id="krforest-M001", fetched_at=AT)
        desc = SdkScopeDescriptor(
            provider=krforest.KRFOREST_PROVIDER, dataset_key=krforest.KRFOREST_MOUNTAIN_DATASET,
            entity_id="krforest-M001", location_ids=("krforest-M001",), forecast_style="observed",
            latitude=37.5, longitude=127, station_id="M001", station_name="태백산",
        )
        adapter = krforest.mountain_weather_to_weather_values
    elif kind == "dust":
        raw = {"obsrr_tpcd": "001", "obsrt_dtm": observed.strftime("%Y-%m-%d %H:%M"),
               "obsrt_pm10_val": "35", "obsrt_tmprt": "-2"}
        parsed = parse_forest_dust_measurement(raw)
        source = krforest_dust.dust_source_record(
            parsed, location_id="krforest-dust-001", fetched_at=AT
        )
        desc = SdkScopeDescriptor(
            provider=krforest_dust.KRFOREST_PROVIDER,
            dataset_key=krforest_dust.KRFOREST_DUST_DATASET,
            entity_id="krforest-dust-001", location_ids=("krforest-dust-001",),
            forecast_style="observed", latitude=37.5, longitude=127,
            station_id="001", station_name="청정넷",
        )
        adapter = krforest_dust.dust_to_weather_values
    elif kind == "krex":
        raw = {"sdate": observed.strftime("%Y%m%d"), "stdHour": str(observed.hour),
               "unitCode": "002", "unitName": "죽전",
               "xValue": "127", "yValue": "37.5", "tempValue": "-2", "humidityValue": "65",
               "windValue": "1.2", "weatherContents": "맑음"}
        parsed = parse_rest_area_weather(raw)
        source = krex.restarea_source_record(parsed, location_id="krex-002", fetched_at=AT)
        desc = SdkScopeDescriptor(
            provider=krex.KREX_PROVIDER, dataset_key=krex.KREX_RESTAREA_DATASET,
            entity_id="krex-002", location_ids=("krex-002",), forecast_style="observed",
            latitude=37.5, longitude=127, station_id="002", station_name="죽전",
        )
        adapter = krex.restarea_weather_to_weather_values
    else:
        rows = [{"bbchNm": "대천", "lat": 36.31, "lot": 126.513,
                 "predcYmd": (AT + timedelta(days=1)).date().isoformat(),
                 "predcNoonSeCd": "오후" if AT.hour >= 3 else "오전",
                 "maxWvhgt": "0.7", "avgArtmp": "17.2"}]
        parsed = parse_beach_index_place(
            Observatory(id="beach", name="대천", latitude=36.31, longitude=126.513), rows
        )
        source = khoa.beach_index_source_record(parsed, location_id="khoa-beach", fetched_at=AT)
        desc = SdkScopeDescriptor(
            provider=khoa.KHOA_PROVIDER, dataset_key=khoa.KHOA_BEACH_INDEX_DATASET,
            entity_id="khoa-beach", location_ids=("khoa-beach",), forecast_style="short",
            latitude=36.31, longitude=126.513, station_id="beach", station_name="대천",
        )
        adapter = khoa.beach_index_to_weather_values
    return desc, source, parsed, adapter


@pytest.mark.parametrize("kind", ["air", "mountain", "dust", "krex", "khoa"])
def test_public_raw_replay_matches_original_adapter_without_mutation(kind):
    desc, source, parsed, adapter = fixture(kind)
    before = deepcopy(source)
    expected = adapter(parsed, location_id=desc.entity_id,
                       source_record_key=source["source_record_key"], known_at=AT)
    assert normalize_sdk_source(desc, source) == expected
    assert source == before
    assert all(value.normalization_version == "kma-v1" for value in expected)


@pytest.mark.parametrize("kind", ["air", "mountain", "dust", "krex", "khoa"])
def test_forged_raw_key_and_naive_fetch_are_rejected(kind):
    desc, source, _, _ = fixture(kind)
    with pytest.raises(ExportArtifactError):
        normalize_sdk_source(desc, {**source, "source_record_key": "forged"})
    with pytest.raises(ExportArtifactError):
        normalize_sdk_source(desc, {**source, "fetched_at": AT.replace(tzinfo=None)})


@pytest.mark.parametrize("kind", ["air", "mountain", "dust", "krex"])
def test_missing_or_old_observation_does_not_use_fetch_as_observation(kind):
    desc, source, _, _ = fixture(kind)
    with pytest.raises(ExportArtifactError):
        normalize_sdk_source(desc, {**source, "fetched_at": AT + timedelta(days=2)})
    raw = source["payload"]["measurement"] if kind == "air" else source["payload"]["rows"][0]
    for key in ("dataTime", "tm", "obsrt_dtm", "sdate", "stdHour"):
        raw.pop(key, None)
    with pytest.raises(ExportArtifactError):
        normalize_sdk_source(desc, source)


@pytest.mark.parametrize("kind", ["air", "mountain", "dust", "krex", "khoa"])
def test_wrong_anchor_is_rejected_before_source_digest_can_authorize_it(kind):
    desc, source, _, _ = fixture(kind)
    updates = {"station_name": "다른관측소"} if kind in {"air", "khoa"} else {"station_id": "other"}
    with pytest.raises(ExportArtifactError):
        normalize_sdk_source(desc.model_copy(update=updates), source)


def test_descriptor_dataset_provider_and_entity_are_not_interchangeable():
    desc, _, _, _ = fixture("air")
    for change in ({"provider": "python-krex-api"}, {"entity_type": "weather_response"},
                   {"forecast_style": "short"}, {"location_ids": ("other",)}):
        with pytest.raises(ValidationError):
            SdkScopeDescriptor.model_validate({**desc.model_dump(), **change})


def test_airkorea_hourly_observation_does_not_inherit_low_frequency_36h_window():
    desc, source, _, _ = fixture("air")
    with pytest.raises(ExportArtifactError):
        normalize_sdk_source(desc, {**source, "fetched_at": AT + timedelta(hours=3)})
    desc, source, _, _ = fixture("krex")
    assert normalize_sdk_source(desc, {**source, "fetched_at": AT + timedelta(hours=3)})


@pytest.mark.parametrize("field,coordinate", [
    (field, value) for field in ("lat", "lot")
    for value in ("NaN", "+Infinity", "-Infinity", True, False, 181, -181)
])
def test_khoa_raw_coordinate_rejection_is_not_hidden_by_source_key_mismatch(field, coordinate):
    desc, source, _, _ = fixture("khoa")
    if isinstance(coordinate, bool):
        desc = desc.model_copy(update={
            "latitude" if field == "lat" else "longitude": float(coordinate),
        })
    rows = source["payload"]["rows"][0]["rows"]
    rows[0][field] = coordinate
    parsed = parse_beach_index_place(Observatory(
        id=desc.station_id, name=desc.station_name,
        latitude=desc.latitude, longitude=desc.longitude,
    ), rows)
    forged = khoa.beach_index_source_record(parsed, location_id=desc.entity_id, fetched_at=AT)
    # SDK는 anchor 문맥만 신뢰하므로 정상 forecast와 정확한 원문 key를 반환한다.
    assert parsed.forecasts and forged["source_record_key"] != source["source_record_key"]
    with pytest.raises(ExportArtifactError, match="좌표"):
        normalize_sdk_source(desc, forged)


def test_khoa_unknown_slot_duplicate_and_forecast_without_future_are_not_approved():
    desc, source, _, _ = fixture("khoa")
    row = source["payload"]["rows"][0]["rows"][0]
    for rows in ([{**row, "predcNoonSeCd": "unknown"}], [row, row]):
        modified = deepcopy(source)
        modified["payload"]["rows"][0]["rows"] = rows
        with pytest.raises(ExportArtifactError):
            normalize_sdk_source(desc, modified)
    with pytest.raises(ExportArtifactError):
        normalize_sdk_source(desc, {**source, "fetched_at": AT + timedelta(days=2)})


@pytest.mark.parametrize("kind", ["air", "mountain", "dust", "krex", "khoa"])
def test_actual_sdk_capture_native_success_promotes(
    tmp_path, monkeypatch, kind
):
    import os
    import sys

    from sqlalchemy import select

    from kortravelweather.export_artifacts import ExportArtifactStore
    from kortravelweather.export_heads import ExportHeads, parse_scope_descriptor
    from kortravelweather.export_sdk_collector import SdkExportCollector
    from kortravelweather.models import WeatherLocation
    from kortravelweather.repository import WeatherRepository

    repo = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    repo.create_schema()
    monkeypatch.setattr(sys.modules[__name__], "AT", datetime.now(KST))
    desc, _, _, _ = fixture(kind)
    point = {"provider": desc.provider, "station_id": desc.station_id,
             "station_name": desc.station_name, "sido_name": desc.sido_name}
    location = WeatherLocation(
        location_id=desc.entity_id, name=desc.station_name, latitude=desc.latitude,
        longitude=desc.longitude, metadata={"measurement_point": point},
    )
    repo.upsert_location(location)
    run = repo.start_sync_run(provider=desc.provider, dataset_key=desc.dataset_key)
    collector = SdkExportCollector(repo, run_id=run.run_id, artifact_root=tmp_path)
    prepared = collector.prepare(
        location=location, provider=desc.provider, dataset_key=desc.dataset_key
    )
    assert prepared is not None and parse_scope_descriptor(prepared.descriptor.model_dump()) == desc
    # 선언 이후 실제 응답을 받는 순서를 보존한다.
    monkeypatch.setattr(sys.modules[__name__], "AT", datetime.now(KST))
    _, source, parsed, normalizer = fixture(kind)
    source["run_id"] = run.run_id
    values = normalizer(parsed, location_id=desc.entity_id,
                        source_record_key=source["source_record_key"], known_at=AT)
    collector._bind_source(prepared, source)
    assert collector.ingest(prepared, record=source, values=values) == len(values)
    repo.finish_sync_run(run.run_id, status="success", values_loaded=len(values))
    heads = ExportHeads(repo)
    assert heads.promote(prepared.identity, ExportArtifactStore(tmp_path)) == "PUBLISHED"
    with repo.engine.connect() as c:
        count = c.scalar(select(heads.generations.c.point_count).join(
            heads.heads, heads.heads.c.generation_id == heads.generations.c.generation_id
        ).where(
            heads.heads.c.namespace == prepared.descriptor.namespace
        ))
    assert count == len(values)
    repo.engine.dispose()
