"""KMA 실행 경계의 숫자 진행·실패 metadata와 비밀 문자열 차단."""

from types import SimpleNamespace

import pytest
from dagster import Failure, build_asset_context
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
