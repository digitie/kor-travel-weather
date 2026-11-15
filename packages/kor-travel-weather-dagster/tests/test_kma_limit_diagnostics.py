"""일일 할당량과 초당 제한은 같은 공개 오류 코드 아래 다른 복구 지침을 갖는다."""

import pytest
from dagster._utils.error import serializable_error_info_from_exc_info
from kma.exceptions import KmaError
from kortravelweather_dagster.alert_events import AlertCollectionError, collect_alert_pages


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,status,scope,retryable,hint", [
    ("quota", 200, "daily", False, "WAIT_QUOTA_RESET"),
    ("quota", None, "daily", False, "WAIT_QUOTA_RESET"),
    ("rate_limit", 200, "per_second", True, "RETRY_AFTER_BACKOFF"),
    ("rate_limit", 429, "per_second", True, "RETRY_AFTER_BACKOFF"),
    (None, 429, "per_second", True, "RETRY_AFTER_BACKOFF"),
    ("auth", 403, None, None, None),
    ("server", 503, None, None, None),
])
async def test_alert_sdk_limit_metadata_has_fixed_scope_and_no_private_content(
    kind, status, scope, retryable, hint,
):
    secret = "fixture-private-provider-key"
    url = f"https://example.invalid/provider?serviceKey={secret}"
    calls = 0

    class Client:
        async def request_with_metadata(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise KmaError(
                f"private provider detail {url}", endpoint=url, failure_kind=kind,
                status_code=status, retryable=not bool(retryable),
            )

    with pytest.raises(AlertCollectionError) as caught:
        await collect_alert_pages(Client())
    exc = caught.value
    assert calls == 1  # 앱이 SDK 재시도를 반복하지 않는다.
    assert exc.diagnostics["stage"] == "page_request"
    assert exc.diagnostics["requested_page"] == 1
    assert exc.diagnostics["requests_fetched"] == 0
    if scope:
        assert exc.code == "ALERT_PROVIDER_RATE_LIMIT"
        assert exc.diagnostics["rate_limit_scope"] == scope
        assert exc.diagnostics["retryable"] is retryable
        assert exc.diagnostics["recovery_hint"] == hint
    else:
        expected = "ALERT_PROVIDER_AUTH" if kind == "auth" else "ALERT_PROVIDER_UNAVAILABLE"
        assert exc.code == expected
        assert not {"rate_limit_scope", "retryable", "recovery_hint"} & exc.diagnostics.keys()
    serialized = serializable_error_info_from_exc_info((type(exc), exc, exc.__traceback__))
    assert serialized.cause is None and serialized.context is None
    assert secret not in serialized.to_string() + str(exc.diagnostics)
    assert url not in serialized.to_string() + str(exc.diagnostics)


@pytest.mark.parametrize("value", [1, 0, "true", "false", "private", None])
def test_alert_limit_allowlist_rejects_unknown_strings_and_non_boolean_retryable(value):
    error = AlertCollectionError("ALERT_PROVIDER_RATE_LIMIT", diagnostics={
        "rate_limit_scope": "private-scope", "retryable": value,
        "recovery_hint": "private-remedy", "raw_url": "private",
    })
    assert error.diagnostics == {"error_code": "ALERT_PROVIDER_RATE_LIMIT"}
