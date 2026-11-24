"""한 위치의 provider 응답 실패는 그 위치만 건너뛴다(실제 repository).

odroid 2026-11-10~21: weatherapi 150곳 sweep 중 한 위치의 HTTP 4xx("provider client 오류입니다.",
1~136번째 요청)로 이미 게시한 batch까지 run 실패가 7건 났다. 장애(연속 실패·실패 비율 초과)와
provider 전체 문제(인증·rate limit)는 그대로 sweep을 멈춘다. 멈춘 sweep도 그때까지 받은 응답은
게시한다. 위치 격리와 429 이월은 사실값·Dagster 표시만 바꾸고, Map export cohort 승인 조건은
그대로다 — 건너뛴 위치나 429가 있는 주기의 Map export는 stale로 남는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from export_clock import install_api_clock
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
    # Dagster는 deferred(갱신 대기)로 보이지만 수집 기록은 failed다. export cohort도 승인되지
    # 않는다(아래 Map export 시험).
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


def _export_payload() -> dict[str, Any]:
    from test_export_external import _external_payload

    return _external_payload("open_meteo", forecast=False, at=datetime.now(UTC))


def _promote(repository, run_id: str) -> list[str]:
    from sqlalchemy import select

    from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore
    from kortravelweather.export_heads import ExportHeads
    from kortravelweather.export_receipts import ExportAttempt

    heads = ExportHeads(repository)
    attempts = heads.lifecycle.attempts
    with repository.engine.connect() as connection:
        rows = connection.execute(
            select(attempts.c.attempt_id, attempts.c.owner_id).where(attempts.c.run_id == run_id)
        ).all()
    store = DatabaseExportArtifactStore(repository)
    return [heads.promote(ExportAttempt(*row), store) for row in rows]


def _map_current(repository, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from kortravelweather_api.app import create_app

    from kortravelweather.settings import WeatherSettings

    client = TestClient(create_app(WeatherSettings(
        environment="development", admin_token="location-isolation",
        enabled_providers=["open_meteo"], service_exports_enabled=True,
        service_export_artifact_root=tmp_path,
    ), repository))
    clock = install_api_clock(monkeypatch, client)

    def read():
        response = client.get(
            "/v1/service/exports/current", headers={"x-admin-token": "location-isolation"}
        )
        assert response.status_code == 200, response.text
        return response.json()

    return read, clock


def _export_ready(repository, targets, tmp_path, monkeypatch):
    first = _sync(
        repository, targets, _Transport(set(), payload=_export_payload()),
        export_artifact_root=tmp_path,
    )
    assert _promote(repository, first["run_id"]) == ["PUBLISHED"] * len(targets)
    read, clock = _map_current(repository, tmp_path, monkeypatch)
    healthy = read()
    assert healthy["meta"]["source_status"] == "ok"
    return read, clock, healthy


def test_skipped_location_keeps_the_map_export_stale_until_a_complete_sweep(
    tmp_path, monkeypatch
) -> None:
    repository, targets = _setup(10)
    read, clock, healthy = _export_ready(repository, targets, tmp_path, monkeypatch)
    second = _sync(
        repository, targets, _Transport({1}, payload=_export_payload()),
        export_artifact_root=tmp_path,
    )
    # 사실값·native run은 성공이다(위치 격리).
    assert second["status"] == "success" and second["locations_failed"] == 1
    assert _promote(repository, second["run_id"]).count("PUBLISHED") == 9
    clock[0] += 60
    # 그러나 roster의 건너뛴 위치는 이번 run의 승인 head가 없어 cohort가 미확인이다.
    # Map export는 이전 승인 snapshot을 stale로 낸다.
    stale = read()
    assert stale["meta"]["source_status"] == "stale"
    assert stale["items"] == healthy["items"]
    # 다음 완전 sweep이 cohort를 다시 승인한다.
    third = _sync(
        repository, targets, _Transport(set(), payload=_export_payload()),
        export_artifact_root=tmp_path,
    )
    assert _promote(repository, third["run_id"]) == ["PUBLISHED"] * len(targets)
    clock[0] += 60
    assert read()["meta"]["source_status"] == "ok"


def test_rate_limited_sweep_keeps_the_map_export_stale_until_the_next_success(
    tmp_path, monkeypatch
) -> None:
    repository, targets = _setup(3)
    read, clock, healthy = _export_ready(repository, targets, tmp_path, monkeypatch)
    with pytest.raises(ProviderError) as error:
        _sync(
            repository, targets, _Transport({2}, status=429, payload=_export_payload()),
            export_artifact_root=tmp_path,
        )
    assert error.value.code == "rate_limit"
    assert _run_row(repository).status == "failed"
    clock[0] += 60
    # failed run의 cohort는 native 성공이 아니므로 승인되지 않는다.
    stale = read()
    assert stale["meta"]["source_status"] == "stale"
    assert stale["items"] == healthy["items"]
    # 다음 주기의 성공 sweep이 cohort를 다시 승인한다.
    recovered = _sync(
        repository, targets, _Transport(set(), payload=_export_payload()),
        export_artifact_root=tmp_path,
    )
    assert _promote(repository, recovered["run_id"]) == ["PUBLISHED"] * len(targets)
    clock[0] += 60
    assert read()["meta"]["source_status"] == "ok"
