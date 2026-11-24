"""Map 연결이 끊긴 service export 요청은 슬롯·DB 연결을 돌려주고 DB 작업을 멈춘다(호출 검토 A6).

실제 ASGI ``http.disconnect``를 middleware 너머로 보낸다(``Request.is_disconnected`` 대체 없음).
"""

import threading
from time import monotonic

import anyio
import pytest
from sqlalchemy import event, text
from test_frozen_service_exports import TOKEN, _client, _collect
from test_frozen_service_exports import repository as repository

from kortravelweather import export_cancellation
from kortravelweather.export_locations import FrozenLocationExports
from kortravelweather.export_snapshots import FrozenCurrentExports
from kortravelweather.models import WeatherLocation


def _scope(path: str, query: str) -> dict:
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
        "query_string": query.encode(),
        "headers": [(b"host", b"test"), (b"x-admin-token", TOKEN.encode())],
        "client": ("127.0.0.1", 50000), "server": ("test", 80),
    }


async def _call(app, path, query="", *, gone=None):
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

    await app(_scope(path, query), receive, send)
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
