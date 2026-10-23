"""Shared PostgreSQL database isolation for the repository test suite."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)
os.environ.setdefault("KOR_TRAVEL_WEATHER_ENV", "development")
os.environ.setdefault("KOR_TRAVEL_WEATHER_DATABASE_URL", TEST_DATABASE_URL)


@pytest.fixture
def weather_test_database_url() -> str:
    """다른 conftest 모듈을 import하지 않고 동일 시험 DB 설정을 제공한다."""
    return TEST_DATABASE_URL


@pytest.fixture(autouse=True)
def clean_postgresql_database() -> None:
    """Keep tests isolated while exercising the production database dialect."""
    engine = create_engine(TEST_DATABASE_URL, future=True)
    try:
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "TRUNCATE TABLE reverse_address_entries, reverse_address_control, "
                        "weather_sync_run_sources, weather_values, "
                        "weather_source_records, weather_sync_runs, weather_locations "
                        "RESTART IDENTITY CASCADE"
                    )
                )
                # retention 시험이 바꾼 catalog 경계도 격리한다. 행만 비우면 다음
                # 시험의 과거 fixture가 이미 폐기된 구간으로 오인되어 입력이 누락된다.
                connection.execute(text(
                    "ALTER TABLE IF EXISTS weather_values_default "
                    "DROP CONSTRAINT IF EXISTS weather_values_default_known_at_floor"
                ))
                export_tables = connection.scalars(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname=current_schema() "
                    "AND tablename LIKE 'weather_export_%'"
                )).all()
                if export_tables:
                    quote = connection.dialect.identifier_preparer.quote
                    connection.exec_driver_sql(
                        "TRUNCATE TABLE " + ", ".join(quote(name) for name in export_tables)
                        + " RESTART IDENTITY CASCADE"
                    )
        except ProgrammingError as exc:
            if "does not exist" not in str(exc).lower():
                raise
    finally:
        engine.dispose()
