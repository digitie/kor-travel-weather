from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from kortravelweather_api.app import create_app

from kortravelweather.settings import WeatherSettings


class UnavailableRepository:
    def create_schema(self) -> None:
        pass

    def __getattr__(self, name: str):
        raise AssertionError(f"export 계약 준비 단계는 DB/provider를 읽으면 안 됨: {name}")


@pytest.fixture
def export_client() -> TestClient:
    return TestClient(create_app(
        WeatherSettings(environment="development", admin_token="private-admin-token"),
        UnavailableRepository(),
    ))


@pytest.mark.parametrize("resource", ["locations", "current", "status"])
def test_export_uses_existing_auth_before_data_access(export_client, resource):
    url = f"/v1/service/exports/{resource}"
    for headers in (
        {}, {"x-admin-token": "wrong"}, {"authorization": "Bearer private-admin-token"},
    ):
        response = export_client.get(url, headers=headers)
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/problem+json")
        assert "private-admin-token" not in response.text


@pytest.mark.parametrize("resource", ["locations", "current", "status"])
def test_no_success_head_is_failed_problem_never_empty_success(export_client, resource):
    response = export_client.get(f"/v1/service/exports/{resource}", headers={
        "x-admin-token": "private-admin-token", "if-none-match": "*",
    })
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["x-source-status"] == "failed"
    assert "no-store" in response.headers["cache-control"]
    assert "etag" not in response.headers
    body = response.json()
    assert body["code"] == "EXPORT_NOT_READY"
    assert "items" not in body
    assert body["meta"]["source_status"] == "failed"
    assert body["meta"]["source_collected_at"] is None
    assert body["meta"]["item_count"] == 0
    assert body["meta"]["snapshot_id"] is None
    assert datetime.fromisoformat(body["meta"]["generated_at"]).tzinfo is not None
    assert "private-admin-token" not in response.text


@pytest.mark.parametrize("query", ["limit=0", "limit=5001", "cursor=", "cursor=" + "a" * 4097])
def test_export_bounds_are_validated(export_client, query):
    response = export_client.get("/v1/service/exports/current?" + query,
                                 headers={"x-admin-token": "private-admin-token"})
    assert response.status_code == 422


def test_service_openapi_has_existing_security_and_typed_failed_response(export_client):
    document = export_client.get("/openapi.json").json()
    for resource in ("locations", "current", "status"):
        operation = document["paths"][f"/v1/service/exports/{resource}"]["get"]
        assert operation["security"] == [{"AdminToken": []}]
        assert "401" in operation["responses"]
        unavailable = operation["responses"]["503"]["content"]
        assert list(unavailable) == ["application/problem+json"]
        assert unavailable["application/problem+json"]["schema"] == {
            "$ref": "#/components/schemas/ExportUnavailable",
        }
        limit = next(p for p in operation["parameters"] if p["name"] == "limit")
        assert limit["schema"]["maximum"] == 5000
    unavailable = document["components"]["schemas"]["ExportUnavailable"]
    assert unavailable["properties"]["meta"] == {"$ref": "#/components/schemas/ExportMeta"}
    assert document["components"]["securitySchemes"]["AdminToken"]["name"] == "x-admin-token"
