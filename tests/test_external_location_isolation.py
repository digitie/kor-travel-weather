"""한 위치의 provider 응답 실패는 그 위치만 건너뛴다(실제 repository).

odroid 2026-11-10~21: weatherapi 150곳 sweep 중 한 위치의 HTTP 4xx("provider client 오류입니다.",
1~136번째 요청)로 이미 게시한 batch까지 run 실패가 7건 났다. 장애(연속 실패·실패 비율 초과)와
provider 전체 문제(인증·rate limit)는 그대로 sweep을 멈춘다. 멈춘 sweep도 그때까지 받은 응답은
게시한다.
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

    def __init__(self, status: int, payload: Any = _PAYLOAD) -> None:
        self.status_code = status
        self.payload = payload

    def json(self) -> Any:
        return self.payload if self.status_code == 200 else {"error": {"code": 1006}}


class _Transport:
    """``failing`` 위치 번호(0부터)에만 ``status``를 돌려준다."""

    def __init__(self, failing: set[int], status: int = 400, payload: Any = _PAYLOAD) -> None:
        self.failing, self.status, self.payload, self.calls = failing, status, payload, 0

    def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        index = self.calls
        self.calls += 1
        return _Response(self.status if index in self.failing else 200, self.payload)


def _sync(repository, targets, transport, **kwargs):
    return run_external_weather_sync(
        repository=repository,
        provider=OpenMeteoProvider(transport=transport, retries=0),
        targets=targets,
        dataset_key="open_meteo_current",
        **kwargs,
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
    # 장애 앞 두 곳의 응답은 batch 한도(25곳)에 닿지 않았어도 게시한다.
    assert _fact_count(repository) == 4
    assert _run_row(repository).status == "failed"


def test_too_many_failed_locations_fail_the_run_but_keep_received_responses() -> None:
    repository, targets = _setup(10)
    # 10곳 중 3곳(30%) 실패: 비율 상한 20% 초과. 연속은 아니다.
    with pytest.raises(ProviderError) as error:
        _sync(repository, targets, _Transport({1, 4, 7}))
    assert error.value.code == "locations_failed"
    # 성공한 7곳(위치당 2값)은 batch 한도 전이라 쌓여만 있었다. 실패로 닫기 전에 게시한다.
    assert _fact_count(repository) == 14
    assert _run_row(repository).status == "failed"


@pytest.mark.parametrize("status", [401, 429])
def test_provider_wide_errors_still_stop_at_once(status) -> None:
    repository, targets = _setup(5)
    transport = _Transport({1}, status=status)
    with pytest.raises(ProviderError) as error:
        _sync(repository, targets, transport)
    assert error.value.code == ("auth" if status == 401 else "rate_limit")
    assert transport.calls == 2
    assert _fact_count(repository) == 2


def test_rate_limit_after_some_locations_keeps_them_and_closes_the_run_failed() -> None:
    repository, targets = _setup(10)
    # 앞 여섯 곳(batch 한도 25곳 미만 — 중간 게시 없음)은 quota를 쓴 정상 응답이다.
    transport = _Transport({6}, status=429)
    with pytest.raises(ProviderError) as error:
        _sync(repository, targets, transport)
    assert error.value.code == "rate_limit"
    assert transport.calls == 7
    assert _fact_count(repository) == 12
    run = _run_row(repository)
    # Dagster는 deferred(갱신 대기)로 보이지만 수집 기록은 failed다.
    assert run.status == "failed"
    assert run.values_loaded == 12
    assert "rate limit" in (run.error or "")


def test_lease_loss_does_not_publish_what_was_staged(monkeypatch) -> None:
    repository, targets = _setup(5)
    # 위치마다 fetch 앞·stage 뒤 두 번 lease를 갱신한다. 셋째 위치 앞에서 lease를 잃는다.
    beats = iter([True, True, True, True])
    monkeypatch.setattr(repository, "heartbeat_sync_run", lambda _run_id: next(beats, False))
    with pytest.raises(RuntimeError, match="lease"):
        _sync(repository, targets, _Transport(set()))
    # provider가 멈춘 것이 아니다. 다른 worker가 run을 넘겨받았을 수 있으므로 쓰지 않는다.
    assert _fact_count(repository) == 0
