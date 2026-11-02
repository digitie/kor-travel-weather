from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from kortravelweather.models import WeatherLocation
from kortravelweather.repository import WeatherLocationRow


@pytest.mark.parametrize(
    ("query", "expected_ids", "total"),
    [
        ("?limit=2", ["busan", "seoul-a"], 4),
        ("?limit=1&offset=2", ["seoul-z"], 4),
        ("?search=%20서울%20&limit=1&offset=1", ["seoul-z"], 2),
        ("?search=seoul-a", ["seoul-a"], 1),
        ("?search=missing", [], 0),
        ("?enabled=false&limit=100", ["busan", "seoul-a", "seoul-z", "sokcho"], 4),
    ],
)
def test_public_location_list_avoids_orm_metadata_and_keeps_contract(
    api_client: TestClient, query: str, expected_ids: list[str], total: int
) -> None:
    repo = api_client.app.state.repository
    locations = [
        WeatherLocation(
            location_id="seoul-z",
            name="서울",
            latitude=37.5,
            longitude=127,
            nx=60,
            ny=127,
            region_code="11",
            metadata={"private": "x" * 100_000},
        ),
        WeatherLocation(
            location_id="seoul-a",
            name="서울",
            latitude=37.500001,
            longitude=127.000001,
            metadata={"private": "hidden"},
        ),
        WeatherLocation(location_id="busan", name="부산", latitude=35, longitude=129),
        WeatherLocation(
            location_id="sokcho",
            name="속초",
            latitude=38.2,
            longitude=128.6,
            nx=87,
            ny=141,
            region_code="42",
        ),
        WeatherLocation(
            location_id="disabled",
            name="서울",
            latitude=37.5,
            longitude=127,
            enabled=False,
            metadata={"private": "disabled"},
        ),
    ]
    for location in locations:
        repo.upsert_location(location)

    statements: list[tuple[str, ...]] = []
    loaded: list[str] = []

    def after_execute(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(tuple(column[0] for column in cursor.description))

    def on_load(row: WeatherLocationRow, context: Any) -> None:
        loaded.append(row.location_id)

    event.listen(repo.engine, "after_cursor_execute", after_execute)
    event.listen(WeatherLocationRow, "load", on_load)
    try:
        response = api_client.get("/v1/weather/locations" + query)
    finally:
        event.remove(repo.engine, "after_cursor_execute", after_execute)
        event.remove(WeatherLocationRow, "load", on_load)

    assert response.status_code == 200
    payload = response.json()
    assert [row["location_id"] for row in payload["data"]] == expected_ids
    by_id = {location.location_id: location for location in locations}
    assert payload["data"] == [
        by_id[location_id].model_dump(mode="json")
        | {"metadata": {}, "sido_name": None, "sigungu_name": None}
        for location_id in expected_ids
    ]
    assert payload["meta"]["page"]["total"] == total
    assert payload["meta"]["page"]["returned"] == len(expected_ids)
    # 공개 DTO에 없는 JSON·날짜를 전송/디코딩하거나 ORM 인스턴스를 만들지 않는다.
    assert loaded == []
    # 목록·count와 비어 있지 않은 목록의 일괄 역주소 조회만 허용한다.
    assert len(statements) == (3 if expected_ids else 2)
    assert sum("payload" in columns for columns in statements) == 0
    assert all(
        not {"metadata", "created_at", "updated_at"}.intersection(columns) for columns in statements
    )

    admin = api_client.get("/v1/admin/locations?search=seoul-a").json()["data"]
    assert admin[0]["metadata"] == {"private": "hidden"}
