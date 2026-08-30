from __future__ import annotations

import asyncio

import httpx
import pytest

from airkorea._http import AsyncHttpClient, HttpClient
from airkorea.exceptions import (
    AirKoreaAuthError,
    AirKoreaNetworkError,
    AirKoreaNoDataError,
    AirKoreaParseError,
    AirKoreaRateLimitError,
    AirKoreaRequestError,
    AirKoreaServerError,
)
from tests.conftest import AsyncFakeSession, FakeResponse, FakeSession, error_payload, payload


class TimeoutSession:
    def get(self, url, *, params, timeout):  # type: ignore[no-untyped-def]
        raise httpx.TimeoutException("slow")


class AsyncTimeoutSession:
    async def get(self, url, *, params, timeout):  # type: ignore[no-untyped-def]
        raise httpx.TimeoutException("slow")


def test_request_adds_common_params() -> None:
    session = FakeSession([FakeResponse(json_data=payload([]))])
    client = HttpClient("decoded-key", session=session, retries=0)

    body = client.get_body("https://example.test/base", "endpoint", {"pageNo": 1})

    assert body["items"] == []
    assert session.last_call.url == "https://example.test/base/endpoint"
    assert session.last_call.params["serviceKey"] == "decoded-key"
    assert session.last_call.params["returnType"] == "json"
    assert session.last_call.params["pageNo"] == 1


def test_service_key_strips_pasted_whitespace() -> None:
    session = FakeSession([FakeResponse(json_data=payload([]))])
    client = HttpClient(" decoded-\nkey\t ", session=session, retries=0)

    client.get_body("https://example.test/base", "endpoint", {})

    assert session.last_call.params["serviceKey"] == "decoded-key"


def test_request_can_override_common_param_names() -> None:
    session = FakeSession([FakeResponse(json_data=payload([]))])
    client = HttpClient("decoded-key", session=session, retries=0)

    client.get_body(
        "https://example.test/base",
        "endpoint",
        {},
        service_key_param="ServiceKey",
        format_param="type",
        format_value="JSON",
    )

    assert session.last_call.params["ServiceKey"] == "decoded-key"
    assert session.last_call.params["type"] == "JSON"
    assert "serviceKey" not in session.last_call.params
    assert "returnType" not in session.last_call.params


def test_missing_service_key_is_auth_error() -> None:
    with pytest.raises(AirKoreaAuthError):
        HttpClient("")


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AirKoreaAuthError),
        (403, AirKoreaAuthError),
        (404, AirKoreaRequestError),
        (429, AirKoreaRateLimitError),
        (500, AirKoreaServerError),
    ],
)
def test_http_status_mapping(status: int, expected: type[Exception]) -> None:
    session = FakeSession([FakeResponse(json_data={}, status_code=status, text="error")])
    client = HttpClient("KEY", session=session, retries=0)

    with pytest.raises(expected):
        client.get_body("https://example.test", "endpoint", {})


def test_5xx_retries_then_succeeds() -> None:
    session = FakeSession(
        [
            FakeResponse(status_code=500, text="server down"),
            FakeResponse(json_data=payload([]), status_code=200),
        ]
    )
    client = HttpClient("KEY", session=session, retries=1, retry_backoff=0)

    assert client.get_body("https://example.test", "endpoint", {})["items"] == []
    assert len(session.calls) == 2


def test_network_timeout_maps_to_network_error() -> None:
    client = HttpClient("KEY", session=TimeoutSession(), retries=0)

    with pytest.raises(AirKoreaNetworkError):
        client.get_body("https://example.test", "endpoint", {})


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("03", AirKoreaNoDataError),
        ("20", AirKoreaAuthError),
        ("22", AirKoreaRateLimitError),
        ("30", AirKoreaAuthError),
        ("31", AirKoreaAuthError),
        ("04", AirKoreaServerError),
        ("99", AirKoreaServerError),
        ("12", AirKoreaRequestError),
    ],
)
def test_result_code_mapping(code: str, expected: type[Exception]) -> None:
    session = FakeSession([FakeResponse(json_data=error_payload(code, "ERROR"))])
    client = HttpClient("KEY", session=session, retries=0)

    with pytest.raises(expected):
        client.get_body("https://example.test", "endpoint", {})


def test_plain_text_service_key_error_maps_to_auth() -> None:
    session = FakeSession(
        [
            FakeResponse(
                json_error=True,
                text="<OpenAPI_ServiceResponse>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</OpenAPI_ServiceResponse>",
            )
        ]
    )
    client = HttpClient("KEY", session=session, retries=0)

    with pytest.raises(AirKoreaAuthError):
        client.get_body("https://example.test", "endpoint", {})


def test_json_parse_failure_maps_to_parse_error() -> None:
    session = FakeSession([FakeResponse(json_error=True, text="not json")])
    client = HttpClient("KEY", session=session, retries=0)

    with pytest.raises(AirKoreaParseError):
        client.get_body("https://example.test", "endpoint", {})


@pytest.mark.parametrize(
    "json_data",
    [
        [],
        {"response": []},
        {"response": {"header": []}},
        {"response": {"header": {"resultCode": "00"}, "body": []}},
    ],
)
def test_malformed_envelope_raises_parse_error(json_data: object) -> None:
    session = FakeSession([FakeResponse(json_data=json_data)])
    client = HttpClient("KEY", session=session, retries=0)

    with pytest.raises(AirKoreaParseError):
        client.get_body("https://example.test", "endpoint", {})


def test_async_request_adds_common_params() -> None:
    async def run() -> None:
        session = AsyncFakeSession([FakeResponse(json_data=payload([]))])
        client = AsyncHttpClient("decoded-key", session=session, retries=0)

        body = await client.get_body("https://example.test/base", "endpoint", {"pageNo": 1})

        assert body["items"] == []
        assert session.last_call.url == "https://example.test/base/endpoint"
        assert session.last_call.params["serviceKey"] == "decoded-key"
        assert session.last_call.params["returnType"] == "json"
        assert session.last_call.params["pageNo"] == 1

    asyncio.run(run())


def test_async_5xx_retries_then_succeeds() -> None:
    async def run() -> None:
        session = AsyncFakeSession(
            [
                FakeResponse(status_code=500, text="server down"),
                FakeResponse(json_data=payload([]), status_code=200),
            ]
        )
        client = AsyncHttpClient("KEY", session=session, retries=1, retry_backoff=0)

        assert (await client.get_body("https://example.test", "endpoint", {}))["items"] == []
        assert len(session.calls) == 2

    asyncio.run(run())


def test_async_network_timeout_maps_to_network_error() -> None:
    async def run() -> None:
        client = AsyncHttpClient("KEY", session=AsyncTimeoutSession(), retries=0)

        with pytest.raises(AirKoreaNetworkError):
            await client.get_body("https://example.test", "endpoint", {})

    asyncio.run(run())
