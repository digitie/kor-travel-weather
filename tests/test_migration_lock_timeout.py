"""alembic migration 연결의 기본 ``lock_timeout``(2026-11-24 Map 호출 검토 L7).

migration이 열린 transaction 뒤에서 lock을 끝없이 기다리면 그 뒤에 온 지도 읽기도 줄을 선다.
연결 시작 옵션으로 5초 기본값을 주고, migration 자신의 설정이 우선한다.
"""

from __future__ import annotations

import os

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import Pool

from alembic import command
from kortravelweather.migration_limits import MIGRATION_LOCK_TIMEOUT, migration_connect_args
from kortravelweather.settings import get_settings

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)


def test_the_default_rides_on_the_connection_options() -> None:
    assert MIGRATION_LOCK_TIMEOUT == "5s"
    assert migration_connect_args("postgresql+psycopg://u:p@h/db") == {
        "options": "-c lock_timeout=5s"
    }
    # URL이 준 options는 뒤에 둔다: 같은 설정이면 운영자 값이 이긴다.
    assert migration_connect_args(
        "postgresql+psycopg://u:p@h/db?options=-c%20lock_timeout%3D30s"
    ) == {"options": "-c lock_timeout=5s -c lock_timeout=30s"}


def test_a_migration_sees_the_default_and_its_own_setting_wins() -> None:
    engine = create_engine(
        TEST_DATABASE_URL, connect_args=migration_connect_args(TEST_DATABASE_URL)
    )
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SHOW lock_timeout")) == "5s"
            # migration 자신의 SET LOCAL이 그 transaction에서 이긴다.
            connection.execute(text("SET LOCAL lock_timeout = '30s'"))
            assert connection.scalar(text("SHOW lock_timeout")) == "30s"
            # 0017처럼 TO DEFAULT로 돌리면 서버 기본(0)이 아니라 이 연결의 기본값이다.
            connection.execute(text("SET LOCAL lock_timeout TO DEFAULT"))
            assert connection.scalar(text("SHOW lock_timeout")) == "5s"
            connection.rollback()
            # 0012처럼 session 수준으로 바꾸면 그 연결의 남은 migration에 그 값이 간다.
            connection.execute(text("SET lock_timeout = 0"))
            connection.commit()
            assert connection.scalar(text("SHOW lock_timeout")) == "0"
            connection.execute(text("RESET lock_timeout"))
            assert connection.scalar(text("SHOW lock_timeout")) == "5s"
            connection.rollback()
    finally:
        engine.dispose()


def test_alembic_env_opens_its_connection_with_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``alembic upgrade``가 실제로 여는 연결이 기본값을 갖는다(env.py 배선)."""
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()
    seen: list[str] = []

    def on_connect(dbapi_connection, _record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SHOW lock_timeout")
            seen.append(cursor.fetchone()[0])
        finally:
            cursor.close()
            dbapi_connection.rollback()

    event.listen(Pool, "connect", on_connect)
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        event.remove(Pool, "connect", on_connect)
        get_settings.cache_clear()
    assert seen and set(seen) == {"5s"}, seen
