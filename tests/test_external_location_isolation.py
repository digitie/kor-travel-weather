"""한 위치의 provider 응답 실패는 그 위치만 건너뛴다(실제 repository).

odroid 2026-11-10~21: weatherapi 150곳 sweep 중 한 위치의 HTTP 4xx("provider client 오류입니다.",
1~136번째 요청)로 이미 게시한 batch까지 run 실패가 7건 났다. 장애(연속 실패·실패 비율 초과)와
provider 전체 문제(인증·rate limit)는 그대로 sweep을 멈춘다.
"""

from __future__ import annotations

from typing import Any

import pytest
from kortravelweather_dagster import external_weather
from kortravelweather_dagster.external_weather import run_external_weather_sync
from test_chunked_publish import _PAYLOAD, _fact_count, _run_row, _setup

from kortravelweather.providers import OpenMeteoProvider, ProviderError


class _Response:
    headers = {"content-type": "application/json"}
    text = "fixture"

    def __init__(self, status: int) -> None:
        self.status_code = status

    def json(self) -> Any:
        return _PAYLOAD if self.status_code == 200 else {"error": {"code": 1006}}


class _Transport:
    """``failing`` 위치 번호(0부터)에만 ``status``를 돌려준다."""

    def __init__(self, failing: set[int], status: int = 400) -> None:
        self.failing, self.status, self.calls = failing, status, 0

    def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        index = self.calls
        self.calls += 1
        return _Response(self.status if index in self.failing else 200)


def _sync(repository, targets, transport):
    return run_external_weather_sync(
        repository=repository,
        provider=OpenMeteoProvider(transport=transport, retries=0),
        targets=targets,
        dataset_key="open_meteo_current",
    )


def test_one_bad_location_is_skipped_and_the_sweep_succeeds() -> None:
    repository, targets = _setup(10)
    result = _sync(repository, targets, _Transport({3}))
    assert result["status"] == "success"
    assert result["locations_failed"] == 1
    assert result["location_failure_counts"] == {"client:400": 1}
    assert result["values_loaded"] == 18
    assert _fact_count(repository) == 18
    assert _run_row(repository).status == "success"


def test_consecutive_failures_mean_an_outage_and_stop_the_sweep(monkeypatch) -> None:
    monkeypatch.setattr(external_weather, "MAX_CONSECUTIVE_LOCATION_FAILURES", 3)
    repository, targets = _setup(10)
    transport = _Transport({2, 3, 4})
    with pytest.raises(ProviderError) as error:
        _sync(repository, targets, transport)
    assert error.value.code == "client"
    assert transport.calls == 5
    assert _run_row(repository).status == "failed"


def test_too_many_failed_locations_fail_the_run_but_keep_published_batches() -> None:
    repository, targets = _setup(10)
    # 10곳 중 3곳(30%) 실패: 비율 상한 20% 초과. 연속은 아니다.
    with pytest.raises(ProviderError) as error:
        _sync(repository, targets, _Transport({1, 4, 7}))
    assert error.value.code == "locations_failed"
    assert _run_row(repository).status == "failed"


@pytest.mark.parametrize("status", [401, 429])
def test_provider_wide_errors_still_stop_at_once(status) -> None:
    repository, targets = _setup(5)
    transport = _Transport({1}, status=status)
    with pytest.raises(ProviderError) as error:
        _sync(repository, targets, transport)
    assert error.value.code == ("auth" if status == 401 else "rate_limit")
    assert transport.calls == 2
