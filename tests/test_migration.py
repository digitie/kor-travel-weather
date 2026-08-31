from __future__ import annotations

import os

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import get_settings

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)


def test_alembic_postgresql_schema_has_shared_safety_contract(monkeypatch) -> None:
    database_url = TEST_DATABASE_URL
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        engine = WeatherRepository(database_url).engine
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
        config = Config("alembic.ini")
        command.upgrade(config, "head")
        # A second PostgreSQL upgrade is a no-op because the revision is
        # committed transactionally with the schema.
        command.upgrade(config, "head")

        inspector = inspect(engine)
        assert "ck_weather_locations_latitude" in {
            item["name"] for item in inspector.get_check_constraints("weather_locations")
        }
        assert "uq_weather_sync_runs_active" in {
            item["name"] for item in inspector.get_indexes("weather_sync_runs")
        }
        assert "ix_weather_sync_runs_heartbeat" in {
            item["name"] for item in inspector.get_indexes("weather_sync_runs")
        }
        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert version == "0005_admin_session_revocations"
            assert "weather_provider_credentials" in inspect(engine).get_table_names()
            assert "weather_admin_session_revocations" in inspect(engine).get_table_names()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO weather_sync_runs "
                    "(run_id, provider, dataset_key, status, started_at) "
                    "VALUES ('run-1', 'p', 'd', 'running', '2026-01-01 00:00:00+00')"
                )
            )
            with pytest.raises(Exception, match="unique"), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO weather_sync_runs "
                        "(run_id, provider, dataset_key, status, started_at) "
                        "VALUES ('run-2', 'p', 'd', 'running', '2026-01-01 00:00:00+00')"
                    )
                )
            connection.execute(
                text(
                    "INSERT INTO weather_locations "
                    "(location_id, name, latitude, longitude, nx, ny, created_at, updated_at) "
                    "VALUES ('immutability', 'Immutable', 37, 127, 60, 127, "
                    "'2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO weather_source_records "
                    "(source_record_key, provider, dataset_key, source_entity_type, "
                    "source_entity_id, raw_payload_hash, payload, fetched_at, imported_at) "
                    "VALUES ('immutable-source', 'p', 'd', 'weather_response', 'immutability', "
                    "'hash', '{}', '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO weather_values "
                    "(value_id, location_id, provider, dataset_key, weather_domain, "
                    "forecast_style, metric_key, target_at, normalization_version, payload, "
                    "collected_at, source_record_key, value_number) VALUES "
                    "('immutable-value', 'immutability', 'p', 'd', 'd', 'short', 'TMP', "
                    "'2026-01-01 00:00:00+00', 'test', '{}', '2026-01-01 00:00:00+00', "
                    "'immutable-source', 1)"
                )
            )
        with pytest.raises(Exception, match="immutable"), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE weather_source_records SET payload='{}' "
                    "WHERE source_record_key='immutable-source'"
                )
            )
        with pytest.raises(Exception, match="immutable"), engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM weather_values WHERE value_id='immutable-value'"
                )
            )
    finally:
        get_settings.cache_clear()
