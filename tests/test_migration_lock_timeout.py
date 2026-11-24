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


def test_the_default_rides_on_the_connection_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PGOPTIONS", raising=False)
    assert MIGRATION_LOCK_TIMEOUT == "5s"
    assert migration_connect_args("postgresql+psycopg://u:p@h/db") == {
        "options": "-c lock_timeout=5s"
    }
    # URL이 준 options는 뒤에 둔다: 같은 설정이면 운영자 값이 이긴다.
    assert migration_connect_args(
        "postgresql+psycopg://u:p@h/db?options=-c%20lock_timeout%3D30s"
    ) == {"options": "-c lock_timeout=5s -c lock_timeout=30s"}


def test_pgoptions_is_kept_between_the_default_and_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """libpq는 ``options`` 인자가 있으면 ``PGOPTIONS``를 읽지 않으므로 직접 이어 붙인다."""
    monkeypatch.setenv("PGOPTIONS", "-csearch_path=marker_search,public")
    assert migration_connect_args("postgresql+psycopg://u:p@h/db") == {
        "options": "-c lock_timeout=5s -csearch_path=marker_search,public"
    }
    assert migration_connect_args(
        "postgresql+psycopg://u:p@h/db?options=-c%20lock_timeout%3D30s"
    ) == {"options": "-c lock_timeout=5s -csearch_path=marker_search,public -c lock_timeout=30s"}
    monkeypatch.setenv("PGOPTIONS", "-c lock_timeout=2s")
    engine = create_engine(
        TEST_DATABASE_URL, connect_args=migration_connect_args(TEST_DATABASE_URL)
    )
    try:
        with engine.connect() as connection:
            # 환경변수가 기본값보다 뒤라 그 값이 이긴다.
            assert connection.scalar(text("SHOW lock_timeout")) == "2s"
    finally:
        engine.dispose()


def test_a_migration_sees_the_default_and_its_own_setting_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PGOPTIONS", raising=False)
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
    """``alembic upgrade``가 실제로 여는 연결이 기본값과 ``PGOPTIONS``를 함께 갖는다(env.py 배선).

    다른 migration 시험처럼 빈 schema에서 시작한다. 앞 시험이 ``create_schema()``로 만든 DB
    (``alembic_version`` 없음)에서 바로 올리면 0001이 이미 있는 표에 부딪힌다.
    """
    monkeypatch.delenv("PGOPTIONS", raising=False)
    reset = create_engine(TEST_DATABASE_URL)
    try:
        with reset.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
    finally:
        reset.dispose()
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_DATABASE_URL", TEST_DATABASE_URL)
    # 운영자가 alembic에 넘기는 libpq 설정이 기본값 때문에 사라지지 않아야 한다.
    monkeypatch.setenv("PGOPTIONS", "-csearch_path=public")
    get_settings.cache_clear()
    seen: list[tuple[str, str]] = []

    def on_connect(dbapi_connection, _record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SELECT current_setting('lock_timeout'), current_setting('search_path')")
            seen.append(tuple(cursor.fetchone()))
        finally:
            cursor.close()
            dbapi_connection.rollback()

    event.listen(Pool, "connect", on_connect)
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        event.remove(Pool, "connect", on_connect)
        get_settings.cache_clear()
    assert seen and set(seen) == {("5s", "public")}, seen
