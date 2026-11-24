"""API 시작 warmup과 ``/ready``(호출 검토 §4.3 경로 B).

실제 PostgreSQL과 lifespan(``with TestClient``)으로 확인한다. 단계 하나를 막아 진행 중·예산 초과·
사용자 요청 겹침·종료를 재현한다.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient
from kortravelweather_api import prewarm
from kortravelweather_api.app import create_app
from kortravelweather_api.prewarm import STEP_NAMES, ApiPrewarm
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from kortravelweather.models import WeatherKind, WeatherLocation
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings

WRITE = re.compile(r"^\s*(insert|update|delete|alter|create|drop|truncate|merge)\b", re.I)


def _settings(database_url: str, *, enabled: bool = True) -> WeatherSettings:
    return WeatherSettings(
        _env_file=None, environment="development", database_url=database_url,
        api_prewarm_enabled=enabled,
    )


@pytest.fixture(autouse=True)
def _enabled_prewarm_logger(monkeypatch):
    """같은 세션의 Alembic fileConfig(migration 시험)가 이미 있던 logger를 끈다.

    실패 로그를 보는 시험이 전체 실행에서도 기록을 받도록 이 파일 안에서만 다시 켠다.
    """
    monkeypatch.setattr(prewarm.logger, "disabled", False)


@pytest.fixture
def repository(weather_test_database_url):
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    yield repo
    repo.engine.dispose()


def _seed(repo: WeatherRepository, count: int) -> None:
    for index in range(count):
        repo.upsert_location(WeatherLocation(
            location_id=f"loc-{index:03d}", name=f"위치 {index}",
            latitude=33 + index * 0.01, longitude=126 + index * 0.01,
        ))


def _wait_ready(client: TestClient, seconds: float = 30) -> dict:
    deadline = monotonic() + seconds
    while True:
        response = client.get("/ready")
        if response.status_code == 200:
            return response.json()
        assert response.status_code == 503, response.text
        assert response.json()["warm"] == "running"
        assert monotonic() < deadline, "warmup이 시간 안에 끝나지 않았습니다."
        sleep(0.02)


def _until(predicate, seconds: float = 10) -> None:
    deadline = monotonic() + seconds
    while not predicate():
        assert monotonic() < deadline, "조건이 시간 안에 참이 되지 않았습니다."
        sleep(0.01)


def _outcomes(body: dict) -> dict[str, str]:
    return {step["name"]: step["outcome"] for step in body["steps"]}


def _timeout_ms(value: str) -> float:
    number, unit = re.fullmatch(r"(\d+)(ms|s|min)?", value).groups()
    return float(number) * {"ms": 1, "s": 1000, "min": 60_000, None: 1}[unit]


def test_ready_reports_disabled_by_default_and_health_is_unchanged(
    repository, weather_test_database_url,
):
    app = create_app(
        WeatherSettings(
            _env_file=None, environment="development", database_url=weather_test_database_url,
        ),
        repository,
    )
    statements = []
    event.listen(repository.engine, "before_cursor_execute",
                 lambda *args: statements.append(args[2]))
    with TestClient(app) as client:
        ready = client.get("/ready")
        health = client.get("/health")
    assert ready.status_code == 200
    assert ready.headers["cache-control"] == "no-store"
    assert ready.json() == {
        "ready": True, "warm": "disabled", "reason": None, "elapsed_ms": None, "steps": [],
    }
    assert health.json()["status"] == "ok"
    assert statements == []  # 꺼져 있으면 시작 때 DB를 읽지 않는다.


@pytest.mark.parametrize(("environment", "raw", "active"), [
    ("production", None, True),
    ("production", "", True),
    ("production", "false", False),
    ("development", None, False),
    ("development", "true", True),
])
def test_prewarm_defaults_to_the_production_profile(
    weather_test_database_url, monkeypatch, environment, raw, active,
):
    monkeypatch.delenv("KOR_TRAVEL_WEATHER_API_PREWARM_ENABLED", raising=False)
    if raw is not None:
        monkeypatch.setenv("KOR_TRAVEL_WEATHER_API_PREWARM_ENABLED", raw)
    settings = WeatherSettings(
        _env_file=None, environment=environment, database_url=weather_test_database_url,
    )
    assert settings.api_prewarm_active is active


def test_repository_double_never_prewarms_even_when_enabled(weather_test_database_url):
    class Double:
        def create_schema(self):
            pass

        def __getattr__(self, name):
            raise AssertionError(f"warmup이 repository double의 {name}을 불렀습니다.")

    app = create_app(_settings(weather_test_database_url), Double())
    with TestClient(app) as client:
        body = client.get("/ready").json()
    assert body["warm"] == "disabled"


def test_prewarm_reads_admin_map_paths_once_within_statement_cap(
    repository, weather_test_database_url,
):
    _seed(repository, 130)
    app = create_app(_settings(weather_test_database_url), repository)
    statements: list[str] = []
    timeouts: list[str] = []
    markers: list[dict] = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
        # 마감 listener가 먼저 SET LOCAL을 걸었다. 같은 transaction에서 실효값을 읽는다.
        with cursor.connection.cursor() as probe:
            probe.execute("SHOW statement_timeout")
            timeouts.append(probe.fetchone()[0])

    original = repository.marker_values_many

    def marker_values_many(ids, **kwargs):
        markers.append({"ids": list(ids), **kwargs})
        return original(ids, **kwargs)

    repository.marker_values_many = marker_values_many
    event.listen(repository.engine, "before_cursor_execute", record)
    try:
        with TestClient(app) as client:
            body = _wait_ready(client)
            # /metrics의 catalog 읽기는 warmup 밖이라 기록에서 뺀다.
            event.remove(repository.engine, "before_cursor_execute", record)
            metrics = client.get("/metrics").text
    finally:
        if event.contains(repository.engine, "before_cursor_execute", record):
            event.remove(repository.engine, "before_cursor_execute", record)

    assert body["ready"] is True
    assert body["warm"] == "done"
    assert body["reason"] is None
    assert body["elapsed_ms"] >= 0
    assert [step["name"] for step in body["steps"]] == list(STEP_NAMES)
    assert set(_outcomes(body).values()) == {"ok"}
    # 읽기 전용이다. 대표 위치 갱신 같은 쓰기는 하지 않는다.
    assert statements and not [sql for sql in statements if WRITE.match(sql)]
    # 문장마다 5초 상한(남은 예산이 더 짧으면 그 값)이 걸렸다.
    assert timeouts and all(0 < _timeout_ms(value) <= 5000 for value in timeouts)
    # 앞쪽 60곳 × 2묶음, 관리 지도 기본 필터(종류 6개)와 같은 인자다.
    assert [len(call["ids"]) for call in markers] == [60, 60]
    assert all(call["kinds"] == list(WeatherKind) for call in markers)
    assert all(call["limit_per_location"] == 80 for call in markers)
    assert set(markers[0]["locations"]) == set(markers[0]["ids"])
    # 연결 여러 개를 pool에 남겼다.
    assert repository.engine.pool.checkedin() >= prewarm.PREWARM_CONNECTIONS
    assert re.search(
        r'^ktw_api_prewarm_step_duration_seconds_count\{(?=[^}]*step="markers")'
        r'(?=[^}]*outcome="ok")[^}]*\} [1-9]',
        metrics, re.M,
    )


def test_statement_cap_cancels_a_slow_read_and_later_steps_still_run(
    repository, weather_test_database_url, monkeypatch, caplog,
):
    _seed(repository, 2)
    monkeypatch.setattr(prewarm, "PREWARM_STATEMENT_SECONDS", 0.5)

    def slow_regions():
        with repository.engine.connect() as connection:
            connection.execute(text("SELECT pg_sleep(5)"))

    repository.region_locations = slow_regions
    app = create_app(_settings(weather_test_database_url), repository)
    caplog.set_level(logging.WARNING, logger="kortravelweather_api.prewarm")
    with TestClient(app) as client:
        body = _wait_ready(client)
    assert body["warm"] == "done"
    outcomes = _outcomes(body)
    assert outcomes.pop("regions") == "failed"
    assert set(outcomes.values()) == {"ok"}
    regions = next(step for step in body["steps"] if step["name"] == "regions")
    assert regions["elapsed_ms"] < 3000  # 5초 pg_sleep를 끝까지 기다리지 않았다.
    failure = next(record for record in caplog.records
                   if "weather_api_prewarm_step_failed" in record.getMessage())
    assert "step=regions" in failure.getMessage()
    assert "sqlstate=57014" in failure.getMessage()
    assert not failure.exc_info  # DB 오류는 원문·traceback 없이 분류만 남긴다.


def test_unexpected_error_is_logged_and_does_not_stop_the_app(
    repository, weather_test_database_url, caplog,
):
    def broken():
        raise RuntimeError("fixture failure")

    repository.region_locations = broken
    app = create_app(_settings(weather_test_database_url), repository)
    caplog.set_level(logging.WARNING, logger="kortravelweather_api.prewarm")
    with TestClient(app) as client:
        body = _wait_ready(client)
        assert client.get("/health").status_code == 200
    assert body["warm"] == "done"
    assert _outcomes(body)["regions"] == "failed"
    failure = next(record for record in caplog.records
                   if "weather_api_prewarm_step_failed step=regions" in record.getMessage())
    assert failure.exc_info and failure.exc_info[0] is RuntimeError  # 코드 결함은 traceback


def test_connection_failure_skips_every_remaining_step(
    repository, weather_test_database_url, monkeypatch,
):
    def unreachable(self):
        raise OperationalError("SELECT 1", {}, ConnectionRefusedError("fixture"))

    monkeypatch.setattr(ApiPrewarm, "_open_connections", unreachable)
    app = create_app(_settings(weather_test_database_url), repository)
    with TestClient(app) as client:
        body = _wait_ready(client)
    assert body["warm"] == "skipped"
    assert body["reason"] == "error"
    assert _outcomes(body) == {
        "connections": "failed", **{name: "skipped" for name in STEP_NAMES[1:]},
    }


def test_budget_exceeded_skips_remaining_steps_and_running_is_503(
    repository, weather_test_database_url, monkeypatch,
):
    _seed(repository, 2)
    monkeypatch.setattr(prewarm, "PREWARM_BUDGET_SECONDS", 2.0)
    entered, release = threading.Event(), threading.Event()

    def blocked_regions():
        entered.set()
        assert release.wait(10)
        return {"ready": False, "calculated_at": None, "items": []}

    repository.region_locations = blocked_regions
    app = create_app(_settings(weather_test_database_url), repository)
    with TestClient(app) as client:
        assert entered.wait(10)
        running = client.get("/ready")
        assert running.status_code == 503
        assert running.headers["retry-after"] == "1"
        assert running.json()["warm"] == "running"
        assert running.json()["ready"] is False
        state = app.state.prewarm
        sleep(max(0.0, state.started_at + 2.0 - monotonic()) + 0.1)
        release.set()
        body = _wait_ready(client)
    assert body["warm"] == "skipped"
    assert body["reason"] == "budget"
    assert body["elapsed_ms"] >= 2000
    assert _outcomes(body) == {
        "connections": "ok", "session_check": "ok", "regions": "ok",
        "locations": "skipped", "markers": "skipped", "location_detail": "skipped",
    }


def test_two_user_requests_in_flight_skip_remaining_steps(repository, weather_test_database_url):
    _seed(repository, 2)
    prewarm_entered, prewarm_gate = threading.Event(), threading.Event()
    user_gate = threading.Event()
    original_session_check = repository.is_admin_session_revoked

    def blocked_session_check(session):
        prewarm_entered.set()
        assert prewarm_gate.wait(10)
        return original_session_check(session)

    def blocked_regions():
        assert user_gate.wait(10)
        return {"ready": False, "calculated_at": None, "items": []}

    repository.is_admin_session_revoked = blocked_session_check
    repository.region_locations = blocked_regions
    app = create_app(_settings(weather_test_database_url), repository)
    activity = app.state.request_activity
    with TestClient(app) as client, ThreadPoolExecutor(2) as pool:
        assert prewarm_entered.wait(10)
        users = [pool.submit(client.get, "/v1/weather/regions") for _ in range(2)]
        _until(lambda: activity.active == 2)
        client.get("/ready")  # 점검 요청은 사용자 요청으로 세지 않는다.
        assert activity.active == 2
        prewarm_gate.set()
        body = _wait_ready(client)
        user_gate.set()
        assert [future.result(10).status_code for future in users] == [200, 200]
    assert activity.active == 0
    assert body["warm"] == "skipped"
    assert body["reason"] == "busy"
    assert _outcomes(body) == {
        "connections": "ok", "session_check": "ok", "regions": "skipped",
        "locations": "skipped", "markers": "skipped", "location_detail": "skipped",
    }


def test_shutdown_cancels_a_running_prewarm(repository, weather_test_database_url, monkeypatch):
    async def endless(self):
        await asyncio.sleep(60)
        return "ok"

    monkeypatch.setattr(ApiPrewarm, "_regions", endless)
    app = create_app(_settings(weather_test_database_url), repository)
    state = app.state.prewarm
    with TestClient(app):
        _until(lambda: len(state.steps) == 2)  # connections·session_check 뒤 regions에서 대기
        assert state.status == "running"
        closing = monotonic()
    assert monotonic() - closing < 5
    assert state.status == "skipped"
    assert state.reason == "shutdown"
    assert state.finished_at is not None
