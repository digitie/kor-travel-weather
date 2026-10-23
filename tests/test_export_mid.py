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

FETCHED = datetime(2026, 10, 23, 9, 15, tzinfo=UTC)


def _raw(part):
    result = {"regId": "11B00000" if part == "land" else "11B10101"}
    if part == "land":
        for day in range(3, 11):
            for suffix in ("Am", "Pm") if day <= 7 else ("",):
                result[f"wf{day}{suffix}"] = "구름많음"
                result[f"rnSt{day}{suffix}"] = 30
    else:
        for day in range(3, 11):
            result[f"taMin{day}"] = 5
            result[f"taMax{day}"] = 20
    return result


def _rehash(record):
    text = json.dumps(
        record["payload"], ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    record["source_record_key"] = "sr_" + hashlib.sha256(text.encode()).hexdigest()[:48]


def _source(part):
    raw = _raw(part)

    async def load():
        def handle(incoming):
            assert incoming.url.params["tmFc"] == "202610231800"
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
            return await method(reg_id=raw["regId"], tm_fc="202610231800")

    rows = asyncio.run(load())
    descriptor = MidScopeDescriptor(
        entity_id=f"mid-{part}:{raw['regId']}",
        source_part=part,
        region_code=raw["regId"],
        location_ids=("a", "b"),
        normalizer_revision=f"kma-mid-{part}-v1",
    )
    record = _source_spec("kma_mid_forecast", descriptor.entity_id, rows, FETCHED)
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
        {"location_ids": tuple(f"loc-{i:03}" for i in range(51))},
        {"entity_id": "mid-temperature:other"},
        {"normalizer_revision": "other"},
    ],
)
def test_descriptor_rejects_noncanonical_scope(source, change):
    descriptor, _ = source
    with pytest.raises(ValueError):
        MidScopeDescriptor.model_validate({**descriptor.model_dump(), **change})
