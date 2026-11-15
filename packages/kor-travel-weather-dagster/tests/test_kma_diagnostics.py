"""KMA 실행 경계의 숫자 진행·실패 metadata와 비밀 문자열 차단."""

from types import SimpleNamespace

import pytest
from dagster import Failure, build_asset_context
from kma.exceptions import KmaError
from kortravelweather_dagster import definitions
from kortravelweather_dagster.kma_weather import KmaAllValuesSkippedError, WeatherTarget
from test_kma_weather import FakeClient, FakeRepository, _target


def test_grid_asset_failure_exposes_bounded_numeric_diagnostics_not_raw_error(monkeypatch):
    repository = FakeRepository()
    secret = "fixture-private-error-content"
    monkeypatch.setattr(definitions, "_run_repository", lambda _context: repository)
    monkeypatch.setattr(definitions, "_kma_grid_targets", lambda *args, **kwargs: (
        [WeatherTarget(_target().location)], {}, set(), None,
    ))

    def fail(**_kwargs):
        exc = RuntimeError(f"provider URL could contain {secret}")
        exc.kma_stage = "publication"
        exc.kma_diagnostics = {
            "fetch_ms": 12, "normalize_ms": 2, "publish_ms": 34,
            "published_values": 100, "completed_grids": 1, "raw_payload": secret,
        }
        raise exc

    monkeypatch.setattr(definitions, "run_weather_sync", fail)
    resource = SimpleNamespace(create_client=lambda **kwargs: FakeClient())
    with build_asset_context(resources={
        "kma_client": resource, "weather_repository": repository,
    }) as context, pytest.raises(Failure) as caught:
        definitions.kma_ultra_short_nowcast_sync(context)
    metadata = {key: value.value for key, value in caught.value.metadata.items()}
    assert metadata == {
        "fetch_ms": 12, "normalize_ms": 2, "publish_ms": 34,
        "published_values": 100, "completed_grids": 1,
        "stage": "publication", "error_type": "RuntimeError",
    }
    assert secret not in str(caught.value) + str(metadata)


@pytest.mark.parametrize("failure_kind,status,error_code,retryable,hint", [
    ("auth", 403, "PROVIDER_AUTH", False, "CHECK_RUN_DETAIL"),
    ("quota", 429, "PROVIDER_RATE_LIMIT", True, "RETRY_AFTER_BACKOFF"),
    ("auth", None, "PROVIDER_AUTH", False, "CHECK_RUN_DETAIL"),
    ("auth", True, "PROVIDER_AUTH", False, "CHECK_RUN_DETAIL"),
    ("auth", 99, "PROVIDER_AUTH", False, "CHECK_RUN_DETAIL"),
    ("auth", 600, "PROVIDER_AUTH", False, "CHECK_RUN_DETAIL"),
    ("auth", "403", "PROVIDER_AUTH", False, "CHECK_RUN_DETAIL"),
    ("auth", 403.0, "PROVIDER_AUTH", False, "CHECK_RUN_DETAIL"),
    ("server", 503, None, None, None),
])
def test_real_sdk_failure_classification_retains_only_safe_metadata(
    monkeypatch, failure_kind, status, error_code, retryable, hint,
):
    from dagster._utils.error import serializable_error_info_from_exc_info

    repository = FakeRepository()
    secret = "fixture-private-provider-key"
    private_url = f"https://example.invalid/provider?serviceKey={secret}"
    monkeypatch.setattr(definitions, "_run_repository", lambda _context: repository)
    monkeypatch.setattr(definitions, "_kma_grid_targets", lambda *args, **kwargs: (
        [WeatherTarget(_target().location)], {}, set(), None,
    ))

    class Client(FakeClient):
        async def now(self, **_kwargs):
            # 실제 공개 SDK 예외를 요청→수집→asset 경계로 통과시킨다.
            raise KmaError(
                f"provider returned private detail: {private_url}",
                provider="data.go.kr", endpoint=private_url,
                failure_kind=failure_kind, status_code=status,
                retryable=not bool(retryable),
            )

    resource = SimpleNamespace(create_client=lambda **kwargs: Client())
    with build_asset_context(resources={
        "kma_client": resource, "weather_repository": repository,
    }) as context, pytest.raises(Failure) as caught:
        definitions.kma_ultra_short_nowcast_sync(context)
    metadata = {key: value.value for key, value in caught.value.metadata.items()}
    assert repository.runs[-1].status == "failed" and repository.values == []
    assert caught.value.description == (error_code or "KMA_COLLECTION_FAILED")
    assert metadata["stage"] == "provider_fetch" and metadata["error_type"] == "KmaError"
    assert metadata["published_values"] == metadata["completed_grids"] == 0
    assert all(type(metadata[key]) is int and metadata[key] >= 0
               for key in ("fetch_ms", "normalize_ms", "publish_ms"))
    if type(status) is int and 100 <= status <= 599:
        assert metadata["http_status"] == status
    else:
        assert "http_status" not in metadata
    if error_code:
        assert metadata["error_code"] == error_code
        assert metadata["retryable"] is retryable
        assert metadata["recovery_hint"] == hint
    else:
        assert not {"error_code", "retryable", "recovery_hint"} & metadata.keys()
    serialized = serializable_error_info_from_exc_info((
        type(caught.value), caught.value, caught.value.__traceback__,
    ))
    assert serialized.cause is None and serialized.context is None
    assert private_url not in serialized.to_string() + str(metadata)
    assert secret not in serialized.to_string() + str(metadata)


def test_all_values_skipped_keeps_safe_specific_reason_and_counts(monkeypatch):
    repository = FakeRepository()
    secret = "fixture-private-all-skipped-content"
    monkeypatch.setattr(definitions, "_run_repository", lambda _context: repository)
    monkeypatch.setattr(definitions, "_kma_grid_targets", lambda *args, **kwargs: (
        [WeatherTarget(_target().location)], {}, set(), None,
    ))

    def fail(**_kwargs):
        exc = KmaAllValuesSkippedError(
            f"diagnostic must not copy {secret}", values_attempted=10, values_skipped=10,
            missing_values=8, invalid_values=2,
        )
        exc.kma_diagnostics = {
            "fetch_ms": 12, "normalize_ms": 2, "publish_ms": 0,
            "published_values": 0, "completed_grids": 1,
        }
        raise exc

    monkeypatch.setattr(definitions, "run_weather_sync", fail)
    resource = SimpleNamespace(create_client=lambda **kwargs: FakeClient())
    with build_asset_context(resources={
        "kma_client": resource, "weather_repository": repository,
    }) as context, pytest.raises(Failure, match="모두 건너뛰") as caught:
        definitions.kma_ultra_short_nowcast_sync(context)
    metadata = {key: value.value for key, value in caught.value.metadata.items()}
    assert metadata["error_code"] == "KMA_ALL_VALUES_SKIPPED"
    assert {key: metadata[key] for key in (
        "values_attempted", "values_skipped", "missing_values", "invalid_values",
    )} == {
        "values_attempted": 10, "values_skipped": 10, "missing_values": 8, "invalid_values": 2,
    }
    assert secret not in str(caught.value) + str(metadata)
