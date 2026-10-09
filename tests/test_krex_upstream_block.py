"""data.ex.co.kr의 edge 차단은 krex만 건너뛰고, 다른 실패는 그대로 실패한다.

From 2026-10-08 every krex request from n150 got the edge's block page, and
``regional_weather_job`` went red twice a day although its other three
assets had published.  These pin both halves of the fix: the block is a
counted, visible skip, and nothing else is mistaken for one.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from krex.exceptions import (
    KrexAuthError,
    KrexBadRequestError,
    KrexInvalidParameterError,
    KrexServerError,
)

from kortravelweather.metrics import REGISTRY
from kortravelweather.providers.krex import (
    KREX_PROVIDER,
    KREX_RESTAREA_DATASET,
    UPSTREAM_BLOCKED,
    upstream_block,
)
from kortravelweather.settings import WeatherSettings

#: The body n150 got, as ``python-krex-api`` keeps it (first 200 characters).
BLOCK_PAGE = (
    '<!DOCTYPE HTML PUBLIC "-//IETF/DTD HTML 2.0//EN">\n'
    "<HTML><HEAD>\n<TITLE>400 Bad Request</TITLE>\n</HEAD><BODY>\n"
    "<H1>Request Blocked</H1>\n</BODY></HTML>\n<br><br><br>\n"
)[:200]


def _blocked() -> KrexBadRequestError:
    return KrexBadRequestError(BLOCK_PAGE, http_status=400, params={"key": "<REDACTED>"})


def test_the_edge_block_page_is_classified() -> None:
    assert upstream_block(_blocked()) == {
        "failure_kind": UPSTREAM_BLOCKED,
        "http_status": 400,
    }
    # A WAF that answers 403 with the same page is the same block; the
    # library raises that as an auth error.
    forbidden = KrexAuthError(f"HTTP 403: {BLOCK_PAGE}", http_status=403)
    assert upstream_block(forbidden) == {"failure_kind": UPSTREAM_BLOCKED, "http_status": 403}


@pytest.mark.parametrize(
    "exc",
    [
        # The API's own refusal of a bad parameter: JSON, a result code, 200.
        KrexInvalidParameterError(
            "data.ex.co.kr returned INVALID_PARAMETER_VALUE: stdHour",
            code="INVALID_PARAMETER_VALUE",
        ),
        # A 400 that is not the block page.
        KrexBadRequestError(
            "<HTML><TITLE>400 Bad Request</TITLE><H1>Bad Request</H1></HTML>", http_status=400
        ),
        # A missing or wrong key.
        KrexAuthError("HTTP 401: Unauthorized", http_status=401),
        KrexAuthError("HTTP 403: Forbidden", http_status=403),
        # A gateway error that happens to quote the words.
        KrexServerError("HTTP 502: Request Blocked", http_status=502),
        # Not a krex error at all.
        RuntimeError("Request Blocked"),
    ],
    ids=["api-invalid-parameter", "plain-400", "auth-401", "auth-403", "server-502", "not-krex"],
)
def test_nothing_else_is_taken_for_a_block(exc: BaseException) -> None:
    assert upstream_block(exc) is None


class _Restarea:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.calls = 0

    async def latest_weather(self, *, lookback_hours: int = 24) -> Any:
        self.calls += 1
        raise self._exc


class _Client:
    def __init__(self, exc: BaseException) -> None:
        self.restarea = _Restarea(exc)
        self.closed = False

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.closed = True


class _NoRepository:
    """A blocked run fetched nothing, so it must not open a sync run."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the repository was used ({name}) for a blocked run")


def _requests(outcome: str) -> float:
    return (
        REGISTRY.get_sample_value(
            "ktw_provider_requests_total",
            {"provider": KREX_PROVIDER, "dataset": KREX_RESTAREA_DATASET, "outcome": outcome},
        )
        or 0.0
    )


def _run(client: _Client) -> dict[str, Any]:
    from kortravelweather_dagster.regional_sources import run_krex_restarea_sync

    return run_krex_restarea_sync(
        repository=_NoRepository(),  # type: ignore[arg-type]
        client=client,
        max_records=10,
        max_values=1000,
        settings=WeatherSettings(enabled_providers=[KREX_PROVIDER]),
    )


def test_a_blocked_run_is_a_counted_skip_not_a_failure() -> None:
    client = _Client(_blocked())
    blocked_before, errors_before = _requests("blocked"), _requests("error")

    result = _run(client)

    assert result["skipped"] is True
    assert result["failure_kind"] == UPSTREAM_BLOCKED
    assert result["http_status"] == 400
    assert result["provider"] == KREX_PROVIDER
    assert result["dataset_key"] == KREX_RESTAREA_DATASET
    assert result["records_fetched"] == 0 and result["values_loaded"] == 0
    assert "Request Blocked" in result["reason"]
    # One request, then stop: retrying a block only feeds the edge's count.
    assert client.restarea.calls == 1
    assert client.closed is True
    # Visible as its own outcome, and not as an error.
    assert _requests("blocked") == blocked_before + 1
    assert _requests("error") == errors_before


@pytest.mark.parametrize(
    "exc",
    [
        KrexInvalidParameterError("data.ex.co.kr returned INVALID_PARAMETER_VALUE: x"),
        KrexAuthError("HTTP 401: Unauthorized", http_status=401),
        KrexServerError("HTTP 503: unavailable", http_status=503),
    ],
    ids=["invalid-parameter", "auth", "server"],
)
def test_any_other_krex_failure_still_fails_the_step(exc: BaseException) -> None:
    client = _Client(exc)
    blocked_before, errors_before = _requests("blocked"), _requests("error")

    with pytest.raises(type(exc)):
        _run(client)

    assert client.closed is True
    assert _requests("error") == errors_before + 1
    assert _requests("blocked") == blocked_before


def test_a_classifier_that_raises_leaves_the_provider_error_intact() -> None:
    from kortravelweather.metrics import provider_request

    def broken(_: BaseException) -> bool:
        raise ValueError("classifier bug")

    with pytest.raises(KrexServerError), provider_request(
        KREX_PROVIDER, KREX_RESTAREA_DATASET, blocked=broken
    ):
        raise KrexServerError("HTTP 500", http_status=500)


def test_the_asset_finishes_green_and_says_why(monkeypatch) -> None:
    """The asset boundary, where the step's colour and metadata are decided."""
    from dagster import build_asset_context

    monkeypatch.setenv("KOR_TRAVEL_WEATHER_ENABLED_PROVIDERS", f'["{KREX_PROVIDER}"]')
    from kortravelweather_dagster.definitions import krex_restarea_sync

    client = _Client(_blocked())

    class _KrexResource:
        @staticmethod
        def create_client(**_: Any) -> Any:
            return client

    class _Repository:
        orchestrator_run_id: str | None = None

    class _RepositoryResource:
        @staticmethod
        def create_repository(**_: Any) -> Any:
            return _Repository()

    context = build_asset_context(
        resources={"krex_client": _KrexResource(), "weather_repository": _RepositoryResource()}
    )
    result = krex_restarea_sync(context)

    assert result["failure_kind"] == UPSTREAM_BLOCKED
    assert result["skipped"] is True
    assert client.closed is True


def test_the_fetch_is_one_event_loop_even_when_blocked() -> None:
    """The block surfaces from inside ``asyncio.run``; the client is still
    closed in that same loop (the rate limiter binds to it)."""
    from kortravelweather_dagster.regional_sources import _fetch_restarea_weather

    client = _Client(_blocked())
    with pytest.raises(KrexBadRequestError):
        asyncio.run(_fetch_restarea_weather(client, max_records=1, lookback_hours=1))
    assert client.closed is True
