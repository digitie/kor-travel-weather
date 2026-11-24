"""관리 지도 호출 줄이기(호출 검토 B8·B12) 회귀 시험.

- B8: ``GET /v1/weather/markers?view=compact``는 기본(full)과 **같은 행**을 지도 표시 필드만 담아
  돌려준다. 기본 응답과 OpenAPI 200 schema(PinVi 계약)는 바뀌지 않는다.
- B12: ``GET /v1/weather/locations``는 페이지 내용 ETag를 주고, ``If-None-Match``가 같으면 본문 없이
  304다. 요청마다 바뀌는 meta는 ETag에 들어가지 않는다.
"""

from __future__ import annotations

import json

from kortravelweather_api.routers import weather
from test_public_read_limits import _client, _seed

MARKERS = "/v1/weather/markers?location_id=air-a&location_id=kma-a"
KINDS = "&kind=observed&kind=short&kind=alert"
VALUE_FIELDS = set(weather.WeatherMarkerValueOut.model_fields)
ALERT_FIELDS = set(weather.WeatherMarkerAlertOut.model_fields)


def _project(row: dict, fields: set[str]) -> dict:
    return {key: row[key] for key in fields}


def test_compact_markers_keep_the_same_rows_with_only_map_fields(repository):
    _seed(repository)
    client = _client(repository)
    for suffix in ("", KINDS):
        full = client.get(MARKERS + suffix).json()["data"]
        compact = client.get(MARKERS + suffix + "&view=compact").json()["data"]
        assert [item["location_id"] for item in compact] == [item["location_id"] for item in full]
        for whole, small in zip(full, compact, strict=True):
            assert set(small) == set(weather.WeatherMarkerCompactOut.model_fields)
            assert small["measurement_point"] == whole["measurement_point"]
            assert small["alert_scope_unverified"] == whole["alert_scope_unverified"]
            for key in ("latest", "forecast"):
                assert small[key] == [_project(row, VALUE_FIELDS) for row in whole[key]], key
            assert small["alerts"] == [_project(row, ALERT_FIELDS) for row in whole["alerts"]]
        if suffix:
            # 종류를 고르면 예보 행도 같은 행으로 온다(빈 목록끼리 같다는 시험이 되지 않게).
            assert any(item["forecast"] for item in compact)
            assert any(item["alerts"] for item in compact)


def test_compact_markers_are_smaller_and_default_stays_full(repository):
    _seed(repository)
    client = _client(repository)
    identity = {"accept-encoding": "identity"}
    full = client.get(MARKERS + KINDS, headers=identity)
    explicit = client.get(MARKERS + KINDS + "&view=full", headers=identity)
    compact = client.get(MARKERS + KINDS + "&view=compact", headers=identity)
    assert full.status_code == explicit.status_code == compact.status_code == 200
    # 기본값은 예전 full 그대로다(값 행 전체 필드).
    first = full.json()["data"][0]["latest"][0]
    assert set(first) == set(weather.WeatherValueOut.model_fields)
    assert json.loads(explicit.content)["data"] == json.loads(full.content)["data"]
    # 로컬 seed(2곳)의 본문 크기 비교일 뿐이며 운영 크기를 주장하지 않는다.
    assert len(compact.content) < len(full.content) / 2


def test_unknown_marker_view_is_rejected(repository):
    _seed(repository)
    response = _client(repository).get(MARKERS + "&view=tiny")
    assert response.status_code == 422


def test_openapi_keeps_the_full_marker_schema_and_publishes_the_compact_one(repository):
    schema = _client(repository).get("/openapi.json").json()
    ok = schema["paths"]["/v1/weather/markers"]["get"]["responses"]["200"]
    assert ok["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Envelope_list_WeatherMarkerOut__"
    }
    assert ok["x-compact-schema"] == {
        "$ref": f"#/components/schemas/{weather.MARKER_COMPACT_SCHEMA}"
    }
    components = schema["components"]["schemas"]
    envelope = components[weather.MARKER_COMPACT_SCHEMA]
    assert envelope["properties"]["data"]["items"] == {
        "$ref": "#/components/schemas/WeatherMarkerCompactOut"
    }
    full_value = set(components["WeatherValueOut"]["properties"])
    assert set(components["WeatherMarkerValueOut"]["properties"]) < full_value
    assert set(components["WeatherMarkerAlertOut"]["properties"]) < full_value
    view = next(
        param for param in schema["paths"]["/v1/weather/markers"]["get"]["parameters"]
        if param["name"] == "view"
    )
    assert view["schema"]["default"] == "full"


def test_location_pages_carry_a_content_etag_and_answer_304(repository):
    _seed(repository)
    client = _client(repository)
    path = "/v1/weather/locations?limit=1&offset=0"
    first = client.get(path, headers={"x-request-id": "one"})
    again = client.get(path, headers={"x-request-id": "two", "accept-encoding": "gzip"})
    etag = first.headers["etag"]
    assert etag.startswith('W/"')
    # request_id·generated_at·duration_ms·압축 여부는 ETag를 바꾸지 않는다.
    assert again.headers["etag"] == etag
    assert first.json()["meta"]["page"]["total"] == 2

    unchanged = client.get(path, headers={"if-none-match": etag})
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == etag
    # 쉼표 목록·강한 형식도 약한 비교로 맞춘다.
    listed = client.get(path, headers={"if-none-match": f'"other", {etag.removeprefix("W/")}'})
    assert listed.status_code == 304

    # 다른 페이지·다른 필터는 다른 ETag이고, 맞지 않는 If-None-Match는 200 본문을 준다.
    second_page = client.get("/v1/weather/locations?limit=1&offset=1")
    assert second_page.headers["etag"] != etag
    stale = client.get("/v1/weather/locations?limit=1&offset=1", headers={"if-none-match": etag})
    assert stale.status_code == 200
    assert stale.json()["data"][0]["location_id"] == "kma-a"


def test_location_etag_changes_when_the_page_or_total_changes(repository):
    _seed(repository)
    client = _client(repository)
    path = "/v1/weather/locations?limit=1&offset=0"
    before = client.get(path).headers["etag"]
    # 첫 페이지 행은 그대로지만 total이 바뀐다(뒤 페이지가 생겼다).
    from kortravelweather.models import WeatherLocation

    repository.create_location(WeatherLocation(
        location_id="zzz-new", name="새 위치", latitude=36.0, longitude=128.0,
    ))
    after_total = client.get(path, headers={"if-none-match": before})
    assert after_total.status_code == 200
    assert after_total.json()["meta"]["page"]["total"] == 3
    assert after_total.headers["etag"] != before
