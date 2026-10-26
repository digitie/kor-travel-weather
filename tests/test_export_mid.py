"""공개 SDK 응답을 저장한 뒤 HTTP 없이 같은 중기 값을 복원한다."""

import asyncio
import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from kma import DataGoKrClient
from kortravelweather_dagster.kma_weather import _source_spec

from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_mid import MidScopeDescriptor, prove_mid_source
from kortravelweather.models import KST

FETCHED = datetime(2026, 10, 23, 9, 15, tzinfo=UTC)


def _raw(part, *, first=3):
    result = {"regId": "11B00000" if part == "land" else "11B10101"}
    if part == "land":
        for day in range(first, 11):
            for suffix in ("Am", "Pm") if day <= 7 else ("",):
                result[f"wf{day}{suffix}"] = "구름많음"
                result[f"rnSt{day}{suffix}"] = 30
    else:
        for day in range(first, 11):
            result[f"taMin{day}"] = 5
            result[f"taMax{day}"] = 20
            if first == 4:
                # 운영 getMidTa 응답(2026-10-27)은 일별 범위 key도 함께 준다.
                result[f"taMin{day}Low"], result[f"taMin{day}High"] = 4, 6
                result[f"taMax{day}Low"], result[f"taMax{day}High"] = 19, 21
    return result


def _rehash(record):
    text = json.dumps(
        record["payload"], ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    record["source_record_key"] = "sr_" + hashlib.sha256(text.encode()).hexdigest()[:48]


def _source(part, *, tm_fc="202610231800", first=3, mutate=None, fetched=FETCHED):
    raw = _raw(part, first=first)
    if mutate:
        mutate(raw)

    async def load():
        def handle(incoming):
            assert incoming.url.params["tmFc"] == tm_fc
            assert incoming.url.params["regId"] == raw["regId"]
            return httpx.Response(
                200,
                json={
                    "response": {
                        "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                        "body": {
                            "items": {"item": [raw]},
                            "totalCount": 1,
                            "pageNo": 1,
                            "numOfRows": 10,
                        },
                    }
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as session:
            client = DataGoKrClient("offline-test", session=session, retries=0)
            method = client.mid_land_forecast if part == "land" else client.mid_temperature_forecast
            return await method(reg_id=raw["regId"], tm_fc=tm_fc)

    rows = asyncio.run(load())
    descriptor = MidScopeDescriptor(
        entity_id=f"mid-{part}:{raw['regId']}",
        source_part=part,
        region_code=raw["regId"],
        location_ids=("a", "b"),
        normalizer_revision=f"kma-mid-{part}-v1",
    )
    record = _source_spec("kma_mid_forecast", descriptor.entity_id, rows, fetched)
    return descriptor, record


@pytest.fixture(params=["land", "temperature"])
def source(request):
    return _source(request.param)


def test_public_sdk_raw_replay_and_stable_namespace(source):
    descriptor, record = source
    assert "tmFc" not in record["payload"]["rows"][0]
    assert record["payload"]["response_metadata"].get("pagination") is None
    proof = prove_mid_source(descriptor, record)
    assert proof.revision == datetime(2026, 10, 23, 9, tzinfo=UTC)
    assert proof.source_collected_at == FETCHED
    assert len(proof.values) == (52 if descriptor.source_part == "land" else 32)
    assert {v.location_id for v in proof.values} == {"a", "b"}
    assert all(v.source_record_key == record["source_record_key"] for v in proof.values)
    old_namespace = descriptor.namespace
    later = copy.deepcopy(record)
    later["payload"]["response_metadata"]["request_params"]["tmFc"] = "202610240600"
    later["fetched_at"] += timedelta(hours=12)
    _rehash(later)
    assert prove_mid_source(descriptor, later).revision > proof.revision
    assert descriptor.namespace == old_namespace


@pytest.mark.parametrize(
    "kind",
    [
        "empty",
        "duplicate",
        "missing",
        "bad_number",
        "key",
        "region",
        "endpoint",
        "future",
        "old",
        "naive",
        "bad_issue",
        "echo",
    ],
)
def test_fail_closed_with_correctly_rehashed_mutations(source, kind):
    descriptor, original = source
    record = copy.deepcopy(original)
    payload = record["payload"]
    row = payload["rows"][0]
    metadata = payload["response_metadata"]
    field = "rnSt3Am" if descriptor.source_part == "land" else "taMin3"
    if kind == "empty":
        payload["rows"] = []
    elif kind == "duplicate":
        payload["rows"].append(copy.deepcopy(row))
    elif kind == "missing":
        row.pop(field)
    elif kind == "bad_number":
        row[field] = "NaN"
    elif kind == "region":
        row["regId"] = "different"
    elif kind == "endpoint":
        metadata["endpoint"] = "MidFcstInfoService/other"
    elif kind == "future":
        record["fetched_at"] -= timedelta(hours=1)
    elif kind == "old":
        record["fetched_at"] += timedelta(hours=19)
    elif kind == "naive":
        record["fetched_at"] = FETCHED.replace(tzinfo=None)
    elif kind == "bad_issue":
        metadata["request_params"]["tmFc"] = "202610231700"
    elif kind == "echo":
        row["tmFc"] = "202610230600"
    _rehash(record)
    if kind == "key":
        record["source_record_key"] = "sr_tampered"
    with pytest.raises(ExportArtifactError):
        prove_mid_source(descriptor, record)


@pytest.mark.parametrize("value", [True, "Infinity", "-Infinity", -999, 101])
def test_precip_probability_rejects_nonfinite_sentinel_and_out_of_range(value):
    descriptor, record = _source("land")
    record["payload"]["rows"][0]["rnSt3Am"] = value
    _rehash(record)
    with pytest.raises(ExportArtifactError):
        prove_mid_source(descriptor, record)


def test_temperature_minimum_cannot_exceed_maximum():
    descriptor, record = _source("temperature")
    record["payload"]["rows"][0]["taMin3"] = 30
    _rehash(record)
    with pytest.raises(ExportArtifactError):
        prove_mid_source(descriptor, record)


@pytest.mark.parametrize(
    "value",
    [
        False,
        True,
        0,
        42,
        {"x": 1},
        ["맑음"],
        " ",
        "x" * 121,
        "NaN",
        "Infinity",
        "-Infinity",
        "-999",
        "0",
        "null",
        "N/A",
        "-",
    ],
)
@pytest.mark.parametrize("snake", [False, True])
def test_sky_requires_real_nonblank_source_string(value, snake):
    descriptor, record = _source("land")
    raw = record["payload"]["rows"][0]
    if snake:
        raw.pop("wf3Am")
        raw["wf_3_am"] = value
    else:
        raw["wf3Am"] = value
    _rehash(record)
    with pytest.raises(ExportArtifactError):
        prove_mid_source(descriptor, record)


def test_official_availability_delay_uses_public_sdk(source):
    from kma.time_utils import base_available_at

    descriptor, record = source
    operation = "getMidLandFcst" if descriptor.source_part == "land" else "getMidTa"
    available = base_available_at(operation, "20261023", "1800")
    record["fetched_at"] = available - timedelta(microseconds=1)
    with pytest.raises(ExportArtifactError):
        prove_mid_source(descriptor, record)
    record["fetched_at"] = available
    assert prove_mid_source(descriptor, record).values


@pytest.mark.parametrize(
    "change",
    [
        {"complete": False},
        {"received_item_count": 0},
        {"page_count": 2},
        {"expected_total_count": 2},
        {"complete": "true"},
    ],
)
def test_optional_pagination_must_agree_with_single_row(source, change):
    descriptor, record = source
    record["payload"]["response_metadata"]["pagination"] = {
        "expected_total_count": 1,
        "received_item_count": 1,
        "page_count": 1,
        "complete": True,
        **change,
    }
    _rehash(record)
    with pytest.raises(ExportArtifactError):
        prove_mid_source(descriptor, record)


@pytest.mark.parametrize(
    "change",
    [
        {"location_ids": ("b", "a")},
        {"location_ids": ("a", "a")},
        {"location_ids": tuple(f"loc-{i:04}" for i in range(1001))},
        {"entity_id": "mid-temperature:other"},
        {"normalizer_revision": "other"},
    ],
)
def test_descriptor_rejects_noncanonical_scope(source, change):
    descriptor, _ = source
    with pytest.raises(ValueError):
        MidScopeDescriptor.model_validate({**descriptor.model_dump(), **change})


#: 운영 실측 회차(2026-10-26 18시: taMin5~10, 2026-10-27 06시: taMin4~10). 06시 발표는
#: D+4, 18시 발표는 D+5부터 공개한다. (tmFc, 실제 조회 시각)
MORNING = ("202610240600", datetime(2026, 10, 23, 22, tzinfo=UTC))
EVENING = ("202610231800", FETCHED)


@pytest.mark.parametrize("part", ["land", "temperature"])
@pytest.mark.parametrize("issue,first", [(MORNING, 4), (EVENING, 5)])
def test_current_response_starts_where_kma_publishes_for_that_issue(part, issue, first):
    """D+3 key가 없는 현재 응답도 그 회차가 공개한 범위 전체가 완결이면 승인한다."""
    tm_fc, fetched = issue
    descriptor, record = _source(part, tm_fc=tm_fc, first=first, fetched=fetched)
    proof = prove_mid_source(descriptor, record)
    days = 11 - first
    per_location = 2 * (days + (8 - first)) if part == "land" else 2 * days
    assert len(proof.values) == per_location * len(descriptor.location_ids)
    assert {v.payload["day_offset"] for v in proof.values} == set(range(first, 11))
    issued = proof.revision.astimezone(KST).date()
    first_day = min(v.target_at.astimezone(KST).date() for v in proof.values)
    assert first_day == issued + timedelta(days=first)


def _drop(*keys):
    def mutate(raw):
        for key in keys:
            raw.pop(key)

    return mutate


@pytest.mark.parametrize(
    "part,issue,first,mutate",
    [
        # 공개 범위 중간의 하루가 통째로 빠진 응답
        ("temperature", MORNING, 4, _drop("taMin6", "taMax6")),
        ("land", MORNING, 4, _drop("wf6Am", "wf6Pm", "rnSt6Am", "rnSt6Pm")),
        # 한 metric만 빠진 반쪽 응답
        ("temperature", MORNING, 4, _drop("taMin4")),
        ("land", EVENING, 5, _drop("rnSt5Pm")),
        # 그 회차가 공개하지 않는 시작일
        ("temperature", MORNING, 5, None),
        ("land", MORNING, 5, None),
        ("temperature", EVENING, 4, None),
        ("land", EVENING, 4, None),
        # 마지막 날(D+10)이 없는 응답
        ("temperature", MORNING, 4, _drop("taMin10", "taMax10")),
        ("land", EVENING, 5, _drop("wf10", "rnSt10")),
    ],
)
def test_current_response_still_requires_every_published_slot(part, issue, first, mutate):
    tm_fc, fetched = issue
    descriptor, record = _source(part, tm_fc=tm_fc, first=first, mutate=mutate, fetched=fetched)
    with pytest.raises(ExportArtifactError):
        prove_mid_source(descriptor, record)
