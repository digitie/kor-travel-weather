"""Map 연결이 끊긴 service export 요청은 슬롯·DB 연결을 돌려주고 DB 작업을 멈춘다(호출 검토 A6).

실제 ASGI ``http.disconnect``를 middleware 너머로 보낸다(``Request.is_disconnected`` 대체 없음).
"""

import logging
import threading
from time import monotonic

import anyio
import pytest
from kortravelweather_api.routers import service_exports
from sqlalchemy import event, text
from test_frozen_service_exports import TOKEN, _client, _collect
from test_frozen_service_exports import repository as repository

from kortravelweather import export_cancellation
from kortravelweather.export_artifacts import ExportArtifactError, ExportGuardTimeout
from kortravelweather.export_cancellation import ExportCancellation
from kortravelweather.export_locations import FrozenLocationExports
from kortravelweather.export_snapshots import FrozenCurrentExports
from kortravelweather.models import WeatherLocation

#: Map이 read timeout(연결 끊김) 뒤 같은 cursor를 다시 보내기까지의 최대 간격(초).
#: Map ``PROVIDER_BOUNDARY_BASE_DELAY_SECONDS``=15초에 jitter ±20%(kor-travel-map #1542)를 더했다.
MAP_TIMEOUT_RETRY_SECONDS = 18.0


def _scope(path: str, query: str, headers: dict[str, str] | None = None) -> dict:
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
        "query_string": query.encode(),
        "headers": [(b"host", b"test"), (b"x-admin-token", TOKEN.encode())] + [
            (key.encode(), value.encode()) for key, value in (headers or {}).items()
        ],
        "client": ("127.0.0.1", 50000), "server": ("test", 80),
    }


async def _call(app, path, query="", *, gone=None, headers=None):
    """``gone``이 set되면 연결이 끊긴 것처럼 ``http.disconnect``를 준다."""
    messages = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await (gone or anyio.Event()).wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app(_scope(path, query, headers), receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    return start["status"], {key.decode(): value.decode() for key, value in start["headers"]}


async def _until(predicate, seconds=10):
    # limiter·캐시 상태는 알림이 없어 짧게 다시 본다. 상한을 넘기면 실패한다.
    for _ in range(int(seconds / 0.01)):
        if predicate():
            return
        await anyio.sleep(0.01)
    raise AssertionError("조건이 시간 안에 참이 되지 않았습니다.")


def _count_statements(engine):
    statements = []

    # 취소 hook보다 먼저 세어 막힌 문장 시도도 놓치지 않는다.
    def before(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before, insert=True)
    return statements, lambda: event.remove(engine, "before_cursor_execute", before)


def _released(app, repository):
    assert app.state.service_export_limiter.borrowed_tokens == 0
    assert repository.engine.pool.checkedout() == 0
    assert not app.state.export_page_cache.inflight
    assert not app.state.export_page_cache.waiting
    assert not app.state.export_page_cache.arrivals


def test_disconnect_while_queued_for_a_slot_skips_all_db_work(repository, tmp_path):
    _collect(repository, tmp_path)
    app = _client(repository, tmp_path).app
    limiter = app.state.service_export_limiter
    result = {}
    statements, stop_counting = None, None

    async def main():
        nonlocal statements, stop_counting
        holders = [object(), object()]
        for holder in holders:
            await limiter.acquire_on_behalf_of(holder)
        gone = anyio.Event()

        async def call():
            result["status"], _ = await _call(
                app, "/v1/service/exports/current", "limit=1", gone=gone,
            )

        async with anyio.create_task_group() as group:
            group.start_soon(call)
            await _until(lambda: limiter.statistics().tasks_waiting == 1)
            statements, stop_counting = _count_statements(repository.engine)
            gone.set()
            await anyio.sleep(0.3)  # 감시 task가 끊김을 본다.
            for holder in holders:
                limiter.release_on_behalf_of(holder)

    try:
        anyio.run(main)
    finally:
        if stop_counting is not None:
            stop_counting()
    assert result["status"] == 499
    assert statements == []  # 슬롯을 받은 뒤 SQL을 하나도 보내지 않았다.
    assert not app.state.export_page_cache.entries
    _released(app, repository)


def test_disconnect_cancels_running_sql_and_returns_slot_and_connection(
    repository, tmp_path, monkeypatch,
):
    for key in ("a", "b"):
        repository.create_location(WeatherLocation(
            location_id=key, name=key, latitude=37.5, longitude=127,
        ))
    app = _client(repository, tmp_path).app
    running = threading.Event()
    original = FrozenLocationExports._freeze_locations

    def slow_freeze(self):
        with self.repository.engine.connect() as connection, connection.begin():
            running.set()
            connection.execute(text("select pg_sleep(30)"))
        return original(self)

    monkeypatch.setattr(FrozenLocationExports, "_freeze_locations", slow_freeze)
    result = {}

    async def main():
        gone = anyio.Event()

        async def call():
            result["status"], _ = await _call(
                app, "/v1/service/exports/locations", "limit=1", gone=gone,
            )

        async with anyio.create_task_group() as group:
            group.start_soon(call)
            await _until(running.is_set)
            await anyio.sleep(0.3)  # pg_sleep가 서버에서 실행 중이다.
            result["disconnected"] = monotonic()
            gone.set()
        result["finished"] = monotonic()

    anyio.run(main)
    assert result["status"] == 499
    # 30초 문장을 끝까지 기다리지 않고 서버 취소로 끝냈다.
    assert result["finished"] - result["disconnected"] < 10
    _released(app, repository)
    with repository.engine.connect() as connection:
        assert connection.scalar(text(
            "select count(*) from pg_stat_activity "
            "where query like 'select pg_sleep(30)%' and state = 'active'"
        )) == 0


@pytest.mark.parametrize("waiter", [True, False])
def test_disconnected_leader_finishes_for_a_waiter_and_stops_without_one(
    repository, tmp_path, monkeypatch, waiter,
):
    _collect(repository, tmp_path)
    app = _client(repository, tmp_path).app
    monkeypatch.setattr(export_cancellation, "LEADER_GRACE_SECONDS", 0.2)
    entered, release = threading.Event(), threading.Event()
    freezes = []
    original = FrozenCurrentExports._freeze

    def gated(self):
        freezes.append(1)
        entered.set()
        assert release.wait(10)
        return original(self)

    monkeypatch.setattr(FrozenCurrentExports, "_freeze", gated)
    cache = app.state.export_page_cache
    result = {}

    async def main():
        gone = anyio.Event()

        async def leader():
            result["leader"], _ = await _call(
                app, "/v1/service/exports/current", "limit=1", gone=gone,
            )

        async def follower():
            result["follower"], result["follower_headers"] = await _call(
                app, "/v1/service/exports/current", "limit=1",
            )

        async with anyio.create_task_group() as group:
            group.start_soon(leader)
            await _until(entered.is_set)
            if waiter:
                group.start_soon(follower)
                await _until(lambda: bool(cache.waiting))
            gone.set()
            await anyio.sleep(0.5)  # 리더 유예(0.2초)가 지났다.
            release.set()

    anyio.run(main)
    assert freezes == [1]
    # 리더의 요청자는 떠났으니 499다. 만든 페이지를 쓸지는 같은 키 대기자 유무가 정한다.
    assert result["leader"] == 499
    if waiter:
        assert result["follower"] == 200
        assert result["follower_headers"]["x-export-read"] == "reused"
        assert len(cache.entries) == 1
    else:
        assert not cache.entries
    _released(app, repository)


def _gated_current(monkeypatch, after=None):
    """current freeze 첫머리에서 멈춘다. ``after``가 있으면 풀린 뒤 그 예외로 실패한다."""
    entered, release = threading.Event(), threading.Event()
    freezes = []
    original = FrozenCurrentExports._freeze

    def gated(self):
        freezes.append(1)
        entered.set()
        assert release.wait(10)
        if after is not None:
            raise after
        return original(self)

    monkeypatch.setattr(FrozenCurrentExports, "_freeze", gated)
    return entered, release, freezes


@pytest.mark.parametrize("slot_busy", [False, True])
def test_disconnected_leader_survives_until_the_map_timeout_retry_arrives(
    repository, tmp_path, monkeypatch, slot_busy,
):
    """Map read timeout으로 끊긴 리더의 페이지를 15~18초 뒤 같은 cursor 재시도가 받는다.

    ``slot_busy``이면 다른 export가 슬롯을 쥐어 재시도는 limiter에서 기다리고, 그 사이 리더
    유예가 지난다. 그래도 리더는 재시도를 위해 끝까지 만들고 build는 한 번뿐이다.
    """
    _collect(repository, tmp_path)
    app = _client(repository, tmp_path).app
    offset = [0.0]
    created = []

    def watched():
        # 감시 시계만 앞당긴다. 페이지 캐시·승인 수명은 실제 시계 그대로다.
        created.append(ExportCancellation(clock=lambda: monotonic() + offset[0]))
        return created[-1]

    monkeypatch.setattr(service_exports, "ExportCancellation", watched)
    entered, release, freezes = _gated_current(monkeypatch)
    limiter = app.state.service_export_limiter
    result = {}

    async def main():
        gone = anyio.Event()
        holder = object()

        async def leader():
            result["leader"], _ = await _call(
                app, "/v1/service/exports/current", "limit=1", gone=gone,
            )

        async def retry():
            result["retry"], result["retry_headers"] = await _call(
                app, "/v1/service/exports/current", "limit=1",
            )

        async with anyio.create_task_group() as group:
            group.start_soon(leader)
            await _until(entered.is_set)
            gone.set()  # Map read timeout
            await _until(lambda: created[0].disconnected)
            if slot_busy:
                await limiter.acquire_on_behalf_of(holder)
                group.start_soon(retry)
                await _until(lambda: limiter.statistics().tasks_waiting == 1)
                offset[0] = export_cancellation.LEADER_GRACE_SECONDS + 1
                release.set()
                await _until(lambda: "retry" in result)
                limiter.release_on_behalf_of(holder)
            else:
                # 재시도가 오기 전에 리더가 다음 SQL에 이른다. 유예가 재시도 간격보다 짧으면 멈춘다.
                offset[0] = MAP_TIMEOUT_RETRY_SECONDS
                release.set()
                await _until(lambda: "leader" in result)
                group.start_soon(retry)

    anyio.run(main)
    assert freezes == [1]
    assert result["leader"] == 499
    assert result["retry"] == 200
    assert result["retry_headers"]["x-export-read"] == "reused"
    _released(app, repository)


@pytest.mark.parametrize("slot_busy", [False, True])
def test_conditional_leader_leaves_its_page_for_the_same_etag_retry(
    repository, tmp_path, monkeypatch, slot_busy,
):
    """조건부 첫 페이지 리더도 같은 키 재시도가 있으면 본문을 만들어 남긴다(호출 검토 A2 리뷰).

    Map W2는 current 첫 페이지에 ``If-None-Match``를 보낸다. read timeout으로 끊긴 리더를 같은
    ETag 재시도가 기다리는데 리더가 본문 읽기 전 304로 끝나면 캐시에 남는 것이 없어, 재시도가
    freeze를 처음부터 다시 했다. 이제 리더는 본문을 남기고 재시도는 그 페이지로 304를 받는다.
    """
    _collect(repository, tmp_path)
    app = _client(repository, tmp_path).app
    path = "/v1/service/exports/current"
    seeded = {}

    async def seed():
        seeded["status"], seeded["headers"] = await _call(app, path, "limit=1")

    anyio.run(seed)
    assert seeded["status"] == 200
    conditional = {"if-none-match": seeded["headers"]["etag"]}
    cache = app.state.export_page_cache
    # 완성 페이지를 비워 다음 조건부 요청이 freeze하는 리더가 되게 한다.
    cache.entries.clear()
    cache.bytes = 0
    offset = [0.0]
    created = []

    def watched():
        # 감시 시계만 앞당긴다. 페이지 캐시·승인 수명은 실제 시계 그대로다.
        created.append(ExportCancellation(clock=lambda: monotonic() + offset[0]))
        return created[-1]

    monkeypatch.setattr(service_exports, "ExportCancellation", watched)
    entered, release, freezes = _gated_current(monkeypatch)
    limiter = app.state.service_export_limiter
    result = {}

    async def main():
        gone = anyio.Event()
        holder = object()

        async def leader():
            result["leader"], _ = await _call(
                app, path, "limit=1", gone=gone, headers=conditional,
            )

        async def retry():
            result["retry"], result["retry_headers"] = await _call(
                app, path, "limit=1", headers=conditional,
            )

        async with anyio.create_task_group() as group:
            group.start_soon(leader)
            await _until(entered.is_set)
            gone.set()  # Map read timeout
            await _until(lambda: created[0].disconnected)
            if slot_busy:
                # 재시도는 limiter에서 기다린다(같은 키 도착 등록). 리더 유예는 이미 지났다.
                await limiter.acquire_on_behalf_of(holder)
                group.start_soon(retry)
                await _until(lambda: limiter.statistics().tasks_waiting == 1)
                offset[0] = export_cancellation.LEADER_GRACE_SECONDS + 1
                release.set()
                await _until(lambda: "retry" in result)
                limiter.release_on_behalf_of(holder)
            else:
                group.start_soon(retry)
                await _until(lambda: bool(cache.waiting))
                release.set()

    anyio.run(main)
    # 재시도는 freeze를 다시 하지 않고 리더가 남긴 페이지로 304를 받았다.
    assert freezes == [1]
    assert result["leader"] == 499
    assert result["retry"] == 304
    assert result["retry_headers"]["x-export-read"] == "reused"
    assert result["retry_headers"]["etag"] == conditional["if-none-match"]
    assert result["retry_headers"]["cache-control"] == "no-store, private"
    _released(app, repository)


@pytest.mark.parametrize(("error", "status", "logged"), [
    (ExportArtifactError("무결성"), 503, "weather_service_export_unavailable"),
    (ExportGuardTimeout("대기 상한"), 499, "weather_service_export_cancelled"),
])
def test_error_after_disconnect_keeps_its_failure_class(
    repository, tmp_path, monkeypatch, caplog, error, status, logged,
):
    """끊긴 뒤 리더에서 난 무결성 실패는 취소 INFO로 묻히지 않는다. 일시 경합만 499다."""
    _collect(repository, tmp_path)
    app = _client(repository, tmp_path).app
    entered, release, _ = _gated_current(monkeypatch, after=error)
    # 같은 세션의 Alembic fileConfig(앞선 migration 시험)가 이미 있던 logger를 끄므로
    # 이 시험 안에서만 다시 켠다. 끈 채로 두면 caplog가 기록을 하나도 받지 못한다.
    monkeypatch.setattr(service_exports.logger, "disabled", False)
    result = {}

    async def main():
        gone = anyio.Event()

        async def leader():
            result["status"], _ = await _call(
                app, "/v1/service/exports/current", "limit=1", gone=gone,
            )

        async with anyio.create_task_group() as group:
            group.start_soon(leader)
            await _until(entered.is_set)
            gone.set()
            await anyio.sleep(0.3)  # 감시 task가 끊김을 본다. 리더는 유예 안이다.
            release.set()

    with caplog.at_level(logging.INFO, logger=service_exports.logger.name):
        anyio.run(main)
    assert result["status"] == status
    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith(logged) for message in messages)
    if status == 503:
        assert any(message.startswith("weather_service_export_failure_site")
                   for message in messages)
        assert not any(message.startswith("weather_service_export_cancelled")
                       for message in messages)
    _released(app, repository)
