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
from kortravelweather_api.read_limits import PublicReadTimeout
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

    # worker thread 안에서 사용자와 같은 limiter의 자리를 쥐고 있는지 본다.
    auth_slots: list[int] = []
    marker_slots: list[int] = []
    original = repository.marker_values_many
    original_session_check = repository.is_admin_session_revoked

    def marker_values_many(ids, **kwargs):
        markers.append({"ids": list(ids), **kwargs})
        marker_slots.append(app.state.marker_limiter.borrowed_tokens)
        return original(ids, **kwargs)

    def is_admin_session_revoked(session):
        auth_slots.append(app.state.auth_limiter.borrowed_tokens)
        return original_session_check(session)

    repository.marker_values_many = marker_values_many
    repository.is_admin_session_revoked = is_admin_session_revoked
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
    # 앞쪽 120곳(사용자 60곳 묶음 2개)을 15곳씩 8묶음으로, 관리 지도 기본 필터(종류 6개)와
    # 같은 인자로 읽었다. 앞쪽부터 빠짐없이, 겹치지 않게 나눴다.
    assert [len(call["ids"]) for call in markers] == [15] * 8
    catalog = repository.list_locations(
        enabled_only=True, search=None, limit=1000, offset=0, include_metadata=False,
    )
    assert [i for call in markers for i in call["ids"]] == [
        row.location_id for row in catalog[:120]
    ]
    assert all(call["kinds"] == list(WeatherKind) for call in markers)
    assert all(call["limit_per_location"] == 80 for call in markers)
    assert set(markers[0]["locations"]) == set(markers[0]["ids"])
    marker_step = next(step for step in body["steps"] if step["name"] == "markers")
    assert (marker_step["warmed"], marker_step["planned"]) == (120, 120)
    # 상세 단계는 하위 읽기 셋(위치·최신값·예보)을 모두 마쳤다.
    detail_step = next(step for step in body["steps"] if step["name"] == "location_detail")
    assert (detail_step["warmed"], detail_step["planned"]) == (3, 3)
    # 다른 단계는 나눠 읽는 진행이 없다.
    assert all(step["warmed"] is None and step["planned"] is None
               for step in body["steps"] if step["name"] not in {"markers", "location_detail"})
    # 세션 확인은 auth_limiter, 마커 묶음은 marker_limiter의 한 자리를 묶음마다 사용자와 나눠 썼다.
    assert auth_slots == [1]
    assert marker_slots == [1] * 8
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


def _cold_marker_reads(repository: WeatherRepository, seconds_per_location: float) -> list[int]:
    """배포 직후처럼 마커 읽기 시간이 위치 수에 비례하게 한다(문장 하나, 상한이 걸린다)."""
    sizes: list[int] = []
    original = repository.marker_values_many

    def marker_values_many(ids, **kwargs):
        sizes.append(len(ids))
        with repository.engine.connect() as connection:
            connection.execute(text("SELECT pg_sleep(:s)"), {"s": seconds_per_location * len(ids)})
        return original(ids, **kwargs)

    repository.marker_values_many = marker_values_many
    return sizes


@pytest.mark.parametrize("chunk_size", [60, prewarm.MARKER_CHUNK_SIZE])
def test_cold_marker_cost_warms_small_chunks_within_the_budget(
    repository, weather_test_database_url, monkeypatch, chunk_size,
):
    """odroid 2026-11-25 재현: 60곳 한 묶음은 문장 상한에 걸려 하나도 데우지 못한다.

    예산·상한을 1/5로 줄이고(6초·1초) 위치당 0.04초를 들게 하면 60곳 묶음은 2.4초로 상한을 넘고,
    15곳 묶음(0.6초)은 상한 안에 들어 마커 몫이 허락하는 만큼 데운 뒤 상세 단계도 실행한다.
    """
    _seed(repository, 130)
    monkeypatch.setattr(prewarm, "PREWARM_BUDGET_SECONDS", 6.0)
    monkeypatch.setattr(prewarm, "PREWARM_STATEMENT_SECONDS", 1.0)
    monkeypatch.setattr(prewarm, "MARKER_CHUNK_SIZE", chunk_size)
    sizes = _cold_marker_reads(repository, 0.04)
    app = create_app(_settings(weather_test_database_url), repository)
    with TestClient(app) as client:
        body = _wait_ready(client)
    markers = next(step for step in body["steps"] if step["name"] == "markers")
    assert body["elapsed_ms"] < 6000
    assert markers["planned"] == 120
    if chunk_size == 60:
        # 예전 방식: 묶음마다 57014로 끝나고 데운 위치가 없다.
        assert markers["outcome"] == "failed"
        assert markers["warmed"] == 0
        assert sizes == [60, 60]
    else:
        assert markers["outcome"] == "ok"
        # 마커 몫(예산 6초 - 상세 몫 1초, 새 묶음은 1초 이상 남았을 때만)에 든 만큼 앞쪽부터 데웠다.
        assert 30 <= markers["warmed"] < 120
        assert markers["warmed"] == sum(sizes) == len(sizes) * 15
    assert body["warm"] == "done"
    assert _outcomes(body)["location_detail"] == "ok"


def test_failed_marker_chunk_is_logged_and_later_chunks_still_run(
    repository, weather_test_database_url, monkeypatch, caplog,
):
    _seed(repository, 130)
    monkeypatch.setattr(prewarm, "PREWARM_STATEMENT_SECONDS", 0.5)
    original = repository.marker_values_many
    calls: list[int] = []

    def marker_values_many(ids, **kwargs):
        calls.append(len(ids))
        if len(calls) == 2:
            with repository.engine.connect() as connection:
                connection.execute(text("SELECT pg_sleep(5)"))
        return original(ids, **kwargs)

    repository.marker_values_many = marker_values_many
    app = create_app(_settings(weather_test_database_url), repository)
    caplog.set_level(logging.WARNING, logger="kortravelweather_api.prewarm")
    with TestClient(app) as client:
        body = _wait_ready(client)
    assert body["warm"] == "done"
    markers = next(step for step in body["steps"] if step["name"] == "markers")
    # 둘째 묶음만 상한(0.5초)에 걸렸다. 실패를 숨기지 않고, 나머지 7묶음은 데웠다.
    assert markers["outcome"] == "failed"
    assert (markers["warmed"], markers["planned"]) == (105, 120)
    assert calls == [15] * 8
    assert _outcomes(body)["location_detail"] == "ok"
    failures = [record.getMessage() for record in caplog.records
                if "weather_api_prewarm_step_failed" in record.getMessage()]
    assert len(failures) == 1
    assert "step=markers chunk=2/8 " in failures[0]
    assert "sqlstate=57014" in failures[0]


def _slow_detail_reads(
    repository: WeatherRepository, seconds: dict[str, float],
) -> list[str]:
    """배포 직후처럼 상세 하위 읽기마다 차가운 비용을 더한다(자기 연결의 문장 하나)."""
    calls: list[str] = []
    for read, method in (
        ("location", "get_location"), ("latest", "latest_values"), ("forecast", "timeline"),
    ):
        original = getattr(repository, method)

        def slow(*args, _read=read, _original=original, **kwargs):
            calls.append(_read)
            with repository.engine.connect() as connection:
                connection.execute(text("SELECT pg_sleep(:s)"), {"s": seconds[_read]})
            return _original(*args, **kwargs)

        setattr(repository, method, slow)
    return calls


async def _markers_use_their_whole_share(self):
    """마커 단계가 마커 몫을 끝까지 쓴 상황. 상세 단계는 마커 몫이 끝난 뒤 시작한다."""
    await asyncio.sleep(max(0.0, self._marker_deadline() - monotonic()))
    return "skipped"


def test_cold_location_detail_stays_within_the_budget(
    repository, weather_test_database_url, monkeypatch, caplog,
):
    """odroid 2026-11-26 재현: 차가운 상세 읽기가 남은 몫을 넘겨 예산 마감에 잘렸다.

    운영은 마커가 23.0초에 끝나 상세 단계가 7.0초를 받았고, 세 읽기를 한 번에 하다 마지막 사실
    수화 문장이 마감에 잘려(57014) 전체가 30113ms였다. 예산·상한을 1/5(6초·1초)로 줄이고 마커가
    몫을 다 쓰게 한 뒤 하위 읽기에 0.3/0.9/0.9초를 들게 하면, 예전 방식은 1초 남은 상세 단계를
    통째로 시작해 예산 마감(6초)에 잘리고 전체가 6초를 넘는다. 지금은 하위 읽기마다 남은 예산을
    보고, 문장 상한 하나만큼 남지 않은 예보 읽기를 시작하지 않아 예산 안에 끝난다.
    """
    _seed(repository, 2)
    monkeypatch.setattr(prewarm, "PREWARM_BUDGET_SECONDS", 6.0)
    monkeypatch.setattr(prewarm, "PREWARM_STATEMENT_SECONDS", 1.0)
    monkeypatch.setattr(ApiPrewarm, "_markers", _markers_use_their_whole_share)
    calls = _slow_detail_reads(repository, {"location": 0.3, "latest": 0.9, "forecast": 0.9})
    app = create_app(_settings(weather_test_database_url), repository)
    caplog.set_level(logging.WARNING, logger="kortravelweather_api.prewarm")
    with TestClient(app) as client:
        body = _wait_ready(client)
    assert body["elapsed_ms"] < 6000
    assert body["warm"] == "done"
    detail = next(step for step in body["steps"] if step["name"] == "location_detail")
    # 위치·최신값을 마치고, 남은 몫이 문장 상한보다 적어 예보는 시작하지 않았다. 부분 진행은
    # warmed/planned로 보이고 잘린 문장이 없으므로 실패 로그도 없다.
    assert detail["outcome"] == "ok"
    assert (detail["warmed"], detail["planned"]) == (2, 3)
    assert calls == ["location", "latest"]
    assert not [record for record in caplog.records
                if "weather_api_prewarm_step_failed" in record.getMessage()]


def test_failed_detail_read_is_reported_and_later_reads_still_run(
    repository, weather_test_database_url, monkeypatch, caplog,
):
    """상한에 잘린 하위 읽기는 숨기지 않고 failed로 남기며, 예산이 남으면 다음 하위 읽기로 간다."""
    _seed(repository, 2)
    monkeypatch.setattr(prewarm, "PREWARM_STATEMENT_SECONDS", 0.5)
    calls = _slow_detail_reads(repository, {"location": 0.0, "latest": 5.0, "forecast": 0.0})
    app = create_app(_settings(weather_test_database_url), repository)
    caplog.set_level(logging.WARNING, logger="kortravelweather_api.prewarm")
    with TestClient(app) as client:
        body = _wait_ready(client)
    detail = next(step for step in body["steps"] if step["name"] == "location_detail")
    assert detail["outcome"] == "failed"
    assert (detail["warmed"], detail["planned"]) == (2, 3)
    assert calls == ["location", "latest", "forecast"]
    failures = [record.getMessage() for record in caplog.records
                if "weather_api_prewarm_step_failed" in record.getMessage()]
    assert len(failures) == 1
    assert "step=location_detail read=latest " in failures[0]
    assert "sqlstate=57014" in failures[0]


def test_statement_cut_by_the_budget_still_finishes_within_it(
    repository, weather_test_database_url, monkeypatch,
):
    """예산 마감에 잘린 문장의 취소·rollback도 예산 안에 끝난다(마감 여유).

    예전에는 문장 마감이 예산 끝과 같아 잘린 문장을 정리하는 시간만큼 전체가 예산을 넘겼다.
    """
    _seed(repository, 2)
    monkeypatch.setattr(prewarm, "PREWARM_BUDGET_SECONDS", 2.0)

    def endless_regions():
        with repository.engine.connect() as connection:
            connection.execute(text("SELECT pg_sleep(10)"))

    repository.region_locations = endless_regions
    app = create_app(_settings(weather_test_database_url), repository)
    with TestClient(app) as client:
        body = _wait_ready(client)
    assert body["elapsed_ms"] < 2000
    assert body["warm"] == "skipped"
    assert body["reason"] == "budget"
    assert _outcomes(body) == {
        "connections": "ok", "session_check": "ok", "regions": "failed",
        "locations": "skipped", "markers": "skipped", "location_detail": "skipped",
    }


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
        raw = client.get("/ready").text
    # deploy.sh가 본문 문자열에서 찾는 표기와 같다(공백 없는 JSON).
    assert '"reason":"error"' in raw and '"outcome":"failed"' in raw
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


def test_shutdown_stops_the_worker_thread_before_its_next_statement(
    repository, weather_test_database_url,
):
    """asyncio 취소는 worker thread를 멈추지 못한다. 종료는 thread에 신호를 보내고 기다린다."""
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    later: list[str] = []

    def two_statement_regions():
        try:
            with repository.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                entered.set()
                assert release.wait(10)
                try:
                    connection.execute(text("SELECT 2"))
                except PublicReadTimeout:
                    later.append("refused")
                else:
                    later.append("executed")
        finally:
            returned.set()

    repository.region_locations = two_statement_regions
    app = create_app(_settings(weather_test_database_url), repository)
    state = app.state.prewarm
    release_later = threading.Timer(0.5, release.set)
    with TestClient(app):
        assert entered.wait(10)
        # 종료가 task를 취소한 뒤에 thread를 풀어 준다. thread는 다음 문장을 시도한다.
        release_later.start()
        closing = monotonic()
    release_later.join()
    assert returned.is_set()  # lifespan 종료가 thread가 돌아올 때까지 기다렸다.
    assert 0.4 < monotonic() - closing < 5
    assert later == ["refused"]
    assert state.status == "skipped"
    assert state.reason == "shutdown"


def _gate_regions(repository: WeatherRepository) -> tuple[threading.Event, threading.Event]:
    """regions 단계를 막아 두고 그사이 마커 자리를 잡을 수 있게 한다."""
    entered, release = threading.Event(), threading.Event()

    def gated_regions():
        entered.set()
        assert release.wait(10)
        return {"ready": False, "calculated_at": None, "items": []}

    repository.region_locations = gated_regions
    return entered, release


def test_marker_slot_wait_ends_with_the_marker_share_and_detail_still_runs(
    repository, weather_test_database_url, monkeypatch,
):
    """사용자 마커 묶음이 두 자리를 모두 쥐면 warmup은 같은 limiter에서 마커 몫까지만 기다린다.

    문장 마감은 전체 예산(3초)에서 마감 여유(0.25초)를 뺀 2.75초다. 마커 몫은 거기서 상세 몫(문장
    상한 0.5초와 마감 여유)을 빼고, 새 묶음을 시작할 여유(문장 상한 하나)를 남긴 1.5초까지다. 그 뒤
    상세 단계는 남긴 몫으로 실행된다.
    """
    _seed(repository, 2)
    monkeypatch.setattr(prewarm, "PREWARM_BUDGET_SECONDS", 3.0)
    monkeypatch.setattr(prewarm, "PREWARM_STATEMENT_SECONDS", 0.5)
    monkeypatch.setattr(prewarm, "PREWARM_FINISH_MARGIN_SECONDS", 0.25)
    entered, release = _gate_regions(repository)
    app = create_app(_settings(weather_test_database_url), repository)
    limiter = app.state.marker_limiter
    users = [object(), object()]
    with TestClient(app) as client:
        assert entered.wait(10)
        for user in users:
            client.portal.call(limiter.acquire_on_behalf_of_nowait, user)
        release.set()
        _until(lambda: client.portal.call(limiter.statistics).tasks_waiting == 1)
        body = _wait_ready(client)
        for user in users:
            client.portal.call(limiter.release_on_behalf_of, user)
    assert body["warm"] == "done"
    assert body["reason"] is None
    # 사용자가 자리를 돌려줄 때까지가 아니라 마커 몫(1.5초)까지만 기다리고 예산 안에 끝났다.
    markers = next(step for step in body["steps"] if step["name"] == "markers")
    assert markers["elapsed_ms"] >= 1000
    assert 1500 <= body["elapsed_ms"] < 3000
    assert (markers["warmed"], markers["planned"]) == (0, 2)
    assert _outcomes(body) == {
        "connections": "ok", "session_check": "ok", "regions": "ok", "locations": "ok",
        "markers": "skipped", "location_detail": "ok",
    }


def test_marker_step_yields_when_users_pile_up_while_it_waits(
    repository, weather_test_database_url,
):
    """자리를 얻은 뒤에도 사용자 요청 수를 다시 본다. 몰렸으면 묶음 없이 자리를 돌려준다."""
    _seed(repository, 2)
    entered, release = _gate_regions(repository)
    app = create_app(_settings(weather_test_database_url), repository)
    limiter = app.state.marker_limiter
    activity = app.state.request_activity
    users = [object(), object()]
    batches: list[int] = []
    original = repository.marker_values_many

    def marker_values_many(ids, **kwargs):
        batches.append(len(ids))
        return original(ids, **kwargs)

    repository.marker_values_many = marker_values_many
    with TestClient(app) as client:
        assert entered.wait(10)
        for user in users:
            client.portal.call(limiter.acquire_on_behalf_of_nowait, user)
        release.set()
        _until(lambda: client.portal.call(limiter.statistics).tasks_waiting == 1)
        client.portal.call(setattr, activity, "active", 2)
        client.portal.call(limiter.release_on_behalf_of, users[0])
        body = _wait_ready(client)
        # warmup은 얻은 자리를 바로 돌려줬다. 남은 자리는 사용자 하나의 것이다.
        assert client.portal.call(lambda: limiter.borrowed_tokens) == 1
        client.portal.call(setattr, activity, "active", 0)
        client.portal.call(limiter.release_on_behalf_of, users[1])
    assert batches == []
    assert body["warm"] == "skipped"
    assert body["reason"] == "busy"
    assert _outcomes(body) == {
        "connections": "ok", "session_check": "ok", "regions": "ok", "locations": "ok",
        "markers": "skipped", "location_detail": "skipped",
    }
