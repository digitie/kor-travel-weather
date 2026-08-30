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


@pytest.fixture(autouse=True)
def clean_postgresql_database() -> None:
    """Keep tests isolated while exercising the production database dialect."""
    engine = create_engine(TEST_DATABASE_URL, future=True)
    try:
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "TRUNCATE TABLE weather_sync_run_sources, weather_values, "
                        "weather_source_records, weather_sync_runs, weather_locations "
                        "RESTART IDENTITY CASCADE"
                    )
                )
        except ProgrammingError as exc:
            if "does not exist" not in str(exc).lower():
                raise
    finally:
        engine.dispose()
