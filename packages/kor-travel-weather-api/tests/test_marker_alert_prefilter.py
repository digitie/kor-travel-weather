"""마커 특보 조회는 다른 구역 특보를 모델로 만들기 전에 거르되, 응답은 그대로다.

odroid(2026-11-10): 60개 위치 마커 한 묶음의 특보 그룹이 발표 관서 단위로 복제된 1,620행을
전부 모델로 만든 뒤 구역 판정에서 버려 9~16초가 걸렸다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue


def _alert(
    location_id: str, index: int, area: str | None, at: datetime, title: str,
) -> WeatherValue:
    raw = {"areaCode": area} if area else {}
    return WeatherValue(
        location_id=location_id, provider="python-kma-api", dataset_key="kma_weather_alerts",
        weather_domain="weather_alert", forecast_style=ForecastStyle.OBSERVED, metric_key="ALERT",
        target_at=at - timedelta(minutes=index), value_text=title,
        payload={"stn_id": "108", "raw": raw, "n": index},
        source_record_key=f"alert-src-{location_id}",
    )


def _seed(api_client: TestClient) -> datetime:
    repo = api_client.app.state.repository
    now = datetime.now(UTC).replace(microsecond=0)
    repo.upsert_location(WeatherLocation(
        location_id="coded", name="구역 있음", latitude=37.5, longitude=127,
        metadata={"kma_alert_area_codes": ["land-a"]},
    ))
    repo.upsert_location(WeatherLocation(
        location_id="uncoded", name="구역 없음", latitude=37.6, longitude=127.1,
    ))
    values = []
    for location_id in ("coded", "uncoded"):
        repo.record_source(
            source_record_key=f"alert-src-{location_id}", provider="python-kma-api",
            dataset_key="kma_weather_alerts", source_entity_type="weather_response",
            source_entity_id=location_id, payload={"rows": []},
        )
        values += [
            # 같은 관서 목록이 위치마다 복제된다: 다른 구역 5건, 이 구역 발표·해제 각 1건.
            *(_alert(location_id, i, f"sea-{i}", now, f"풍랑주의보{i} 발표") for i in range(5)),
            _alert(location_id, 10, "land-a", now, "강풍주의보 발표"),
            _alert(location_id, 11, "land-a", now - timedelta(hours=1), "호우주의보 해제"),
            _alert(location_id, 12, "land-a", now - timedelta(hours=2), "호우주의보 발표"),
        ]
    repo.upsert_values(values)
    return now


def test_alert_candidates_of_other_areas_are_not_hydrated(api_client: TestClient) -> None:
    _seed(api_client)
    repo = api_client.app.state.repository
    rows = repo.marker_values_many(["coded", "uncoded"], limit_per_location=80, kinds=["alert"])
    areas = {key: sorted(v.payload["raw"].get("areaCode", "") for v in vals)
             for key, vals in rows.items()}
    # 구역이 있는 위치: 이 구역 행(발표 2·해제 1)만 남고 다른 구역 5건은 모델이 되지 않는다.
    assert areas["coded"] == ["land-a", "land-a", "land-a"]
    # 구역 미확인 위치: 응답에 쓰이지 않으므로 표식용 1건만 남는다.
    assert len(rows["uncoded"]) == 1


def test_marker_alerts_and_unverified_flag_are_unchanged(api_client: TestClient) -> None:
    _seed(api_client)
    response = api_client.get(
        "/v1/weather/markers?location_id=coded&location_id=uncoded&kind=alert"
    )
    assert response.status_code == 200, response.text
    data = {item["location_id"]: item for item in response.json()["data"]}
    # 이 구역의 활성 특보만: 강풍주의보(발표). 호우주의보는 더 새 해제가 지운다.
    assert [alert["value_text"] for alert in data["coded"]["alerts"]] == ["강풍주의보 발표"]
    assert data["coded"]["alert_scope_unverified"] is False
    assert data["uncoded"]["alerts"] == []
    assert data["uncoded"]["alert_scope_unverified"] is True


def test_mixed_and_ordering_cases_keep_matched_alerts_and_flag(api_client: TestClient) -> None:
    """구역이 있는 위치에 구역 없는 행이 섞여도 matched는 남고 표식은 참이다.

    미확인 행이 matched보다 먼저 정렬돼도(더 최근) matched를 버리지 않는다.
    다른 제공처의 특보는 위치 일치로 matched다.
    """
    repo = api_client.app.state.repository
    now = datetime.now(UTC).replace(microsecond=0)
    repo.upsert_location(WeatherLocation(
        location_id="mixed", name="혼합", latitude=37.5, longitude=127,
        metadata={"kma_alert_area_codes": ["land-a"]},
    ))
    repo.record_source(
        source_record_key="alert-src-mixed", provider="python-kma-api",
        dataset_key="kma_weather_alerts", source_entity_type="weather_response",
        source_entity_id="mixed", payload={"rows": []},
    )
    repo.record_source(
        source_record_key="other-alert-src", provider="other-provider",
        dataset_key="other_weather_alerts", source_entity_type="weather_response",
        source_entity_id="mixed", payload={"rows": []},
    )
    repo.upsert_values([
        _alert("mixed", 0, None, now, "건조주의보 발표"),  # 가장 최근, 구역 없음
        _alert("mixed", 5, "land-a", now, "강풍주의보 발표"),
        *(_alert("mixed", 10 + i, f"sea-{i}", now, f"풍랑주의보{i} 발표") for i in range(3)),
        WeatherValue(
            location_id="mixed", provider="other-provider", dataset_key="other_weather_alerts",
            weather_domain="weather_alert", forecast_style=ForecastStyle.OBSERVED,
            metric_key="ALERT", target_at=now - timedelta(minutes=2), value_text="폭염경보",
            payload={"stn_id": "x"}, source_record_key="other-alert-src",
        ),
    ])
    response = api_client.get("/v1/weather/markers?location_id=mixed&kind=alert")
    assert response.status_code == 200, response.text
    item = response.json()["data"][0]
    texts = sorted(alert["value_text"] for alert in item["alerts"])
    assert texts == ["강풍주의보 발표", "폭염경보"]
    assert item["alert_scope_unverified"] is True


def test_own_area_alert_is_not_crowded_out_by_newer_fanned_alerts(api_client: TestClient) -> None:
    """위치별 특보 예산은 구역을 거른 뒤에 적용한다(2026-11-10, 안전성 수정).

    예전에는 다른 구역의 더 최근 복제 특보 26건이 후보 예산(80 // 3종류 그룹)을 채워,
    그보다 오래된 이 구역의 활성 특보가 마커에서 사라졌다.
    """
    repo = api_client.app.state.repository
    now = datetime.now(UTC).replace(microsecond=0)
    repo.upsert_location(WeatherLocation(
        location_id="crowded", name="밀린 위치", latitude=37.5, longitude=127,
        metadata={"kma_alert_area_codes": ["land-a"]},
    ))
    repo.record_source(
        source_record_key="alert-src-crowded", provider="python-kma-api",
        dataset_key="kma_weather_alerts", source_entity_type="weather_response",
        source_entity_id="crowded", payload={"rows": []},
    )
    repo.upsert_values([
        *(_alert("crowded", i, f"sea-{i}", now, f"풍랑주의보{i} 발표") for i in range(40)),
        # 복제 특보 40건보다 오래됐지만 아직 활성인 이 구역 특보
        _alert("crowded", 60, "land-a", now, "한파주의보 발표"),
    ])
    six = "&".join(f"kind={kind}" for kind in (
        "observed", "nowcast", "ultra_short", "short", "mid", "alert",
    ))
    response = api_client.get(f"/v1/weather/markers?location_id=crowded&{six}")
    assert response.status_code == 200, response.text
    alerts = response.json()["data"][0]["alerts"]
    assert [alert["value_text"] for alert in alerts] == ["한파주의보 발표"]
