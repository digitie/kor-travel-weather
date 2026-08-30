from __future__ import annotations

import json

import pytest

from airkorea import (
    AirKoreaClient,
    jsonable,
    redact_sensitive,
    run_debug_method,
    save_debug_fixture,
    slugify,
)
from tests.conftest import FakeResponse, FakeSession, payload


def measurement_row() -> dict[str, object]:
    return {
        "stationName": "종로구",
        "mangName": "도시대기",
        "sidoName": "서울",
        "dataTime": "2026-04-30 14:00",
        "khaiValue": "74",
        "khaiGrade": "2",
        "so2Value": "0.003",
        "coValue": "0.4",
        "o3Value": "0.031",
        "no2Value": "0.025",
        "pm10Value": "35",
        "pm10Value24": "42",
        "pm25Value": "17",
        "pm25Value24": "19",
        "so2Grade": "1",
        "coGrade": "1",
        "o3Grade": "2",
        "no2Grade": "2",
        "pm10Grade": "2",
        "pm10Grade1h": "1",
        "pm25Grade": "2",
        "pm25Grade1h": "1",
    }


def test_run_debug_method_captures_safe_request_response() -> None:
    session = FakeSession([FakeResponse(json_data=payload([measurement_row()]))])
    client = AirKoreaClient(service_key="decoded-key", session=session, retries=0)

    debug_run = run_debug_method(
        client,
        "station_measurements",
        {"station_name": "종로구", "num_of_rows": 1},
    )

    assert debug_run.error is None
    assert debug_run.request["method"] == "GET"
    assert debug_run.request["query"]["stationName"] == "종로구"
    assert "serviceKey" not in debug_run.request["query"]
    assert debug_run.response["body"]["items"][0]["stationName"] == "종로구"
    assert jsonable(debug_run.processed)[0]["station_name"] == "종로구"
    assert debug_run.catalog[0]["dataset_name"] == "한국환경공단_에어코리아_대기오염정보"
    assert debug_run.catalog[0]["service_key_url"].startswith("https://www.data.go.kr/")
    assert debug_run.trace[0].startswith("Catalog: 한국환경공단_에어코리아_대기오염정보")
    assert debug_run.trace[-1].endswith("-> HTTP 200")


def test_run_debug_method_returns_validation_error_without_http_call() -> None:
    client = AirKoreaClient(service_key="decoded-key", session=FakeSession([]), retries=0)

    debug_run = run_debug_method(client, "sido_measurements", {"sido_name": "서울특별시"})

    assert debug_run.error is not None
    assert debug_run.error["type"] == "ValueError"
    assert debug_run.request == {}
    assert debug_run.response == {}


def test_save_debug_fixture_redacts_and_prevents_overwrite(tmp_path) -> None:
    session = FakeSession([FakeResponse(json_data=payload([measurement_row()]))])
    client = AirKoreaClient(service_key="decoded-key", session=session, retries=0)
    debug_run = run_debug_method(
        client,
        "station_measurements",
        {"station_name": "종로구", "num_of_rows": 1},
    )

    fixture_path = save_debug_fixture(
        base_dir=tmp_path,
        case_name="종로구 정상 케이스",
        description="측정소 실시간 정상 응답",
        debug_run=debug_run,
        input_data={**debug_run.input, "serviceKey": "do-not-save"},
        library_version="0.0.test",
    )

    with fixture_path.open("r", encoding="utf-8") as file:
        fixture = json.load(file)

    assert fixture_path.parent.name == "station_measurements"
    assert fixture_path.name == f"{slugify('종로구 정상 케이스')}.json"
    assert fixture["input"]["serviceKey"] == "<REDACTED>"
    assert "serviceKey" not in fixture["request"]["query"]
    assert fixture["catalog"][0]["service_key_url"].startswith("https://www.data.go.kr/")
    assert fixture["processed"][0]["station_name"] == "종로구"

    with pytest.raises(FileExistsError):
        save_debug_fixture(base_dir=tmp_path, case_name="종로구 정상 케이스", debug_run=debug_run)


def test_redact_sensitive_masks_nested_credentials() -> None:
    redacted = redact_sensitive(
        {
            "headers": {"Authorization": "Bearer secret", "Accept": "application/json"},
            "query": {"ServiceKey": "secret", "addr": "서울"},
        }
    )

    assert redacted["headers"]["Authorization"] == "<REDACTED>"
    assert redacted["headers"]["Accept"] == "application/json"
    assert redacted["query"]["ServiceKey"] == "<REDACTED>"
