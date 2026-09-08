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
        # Leave historical facts in place while upgrading the final
        # projection migration so its DISTINCT ON backfill is exercised.
        command.upgrade(config, "0006_marker_lookup_index")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO weather_locations "
                    "(location_id, name, latitude, longitude, created_at, updated_at) "
                    "VALUES ('projection-backfill', 'Projection', 37, 127, "
                    "'2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00')"
                )
            )
            for suffix, known_at in (
                ("old", "2026-01-01 00:00:00+00"),
                ("new", "2026-01-01 01:00:00+00"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO weather_source_records "
                        "(source_record_key, provider, dataset_key, source_entity_type, "
                        "source_entity_id, raw_payload_hash, payload, fetched_at, imported_at) "
                        "VALUES (:source_key, 'p', 'd', 'weather_response', "
                        "'projection-backfill', :hash, '{}', :known_at, :known_at)"
                    ),
                    {
                        "source_key": f"projection-source-{suffix}",
                        "hash": f"projection-hash-{suffix}",
                        "known_at": known_at,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO weather_values "
                        "(value_id, location_id, provider, dataset_key, weather_domain, "
                        "forecast_style, metric_key, target_at, known_at, "
                        "normalization_version, payload, collected_at, source_record_key, "
                        "value_number) VALUES (:value_id, 'projection-backfill', 'p', 'd', "
                        "'weather', 'short', 'TMP', '2026-01-01 02:00:00+00', :known_at, "
                        "'test', '{}', :known_at, :source_key, :value_number)"
                    ),
                    {
                        "value_id": f"projection-value-{suffix}",
                        "known_at": known_at,
                        "source_key": f"projection-source-{suffix}",
                        "value_number": 1 if suffix == "old" else 2,
                    },
                )
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
            assert version == "0011_current_value_alert_index"
            weather_value_indexes = {
                item["name"] for item in inspect(engine).get_indexes("weather_values")
            }
            assert "ix_weather_values_marker_lookup" in weather_value_indexes
            # The marker observed pass filters ``forecast_style``; without this
            # index it scans past every newer forecast revision per location.
            assert "ix_weather_values_marker_observed" in weather_value_indexes
            # The marker alert pass had no index at all and fell back to a
            # sequential scan of the whole append-only table.
            assert "ix_weather_values_alert_lookup" in weather_value_indexes
            # The nearby bundle reads warnings from the projection on their own
            # budget; without this index that read walks the whole slice.
            assert "ix_weather_current_values_alert_lookup" in {
                item["name"]
                for item in inspect(engine).get_indexes("weather_current_values")
            }
            assert "weather_current_values" in inspect(engine).get_table_names()
            assert {
                "ix_weather_current_values_location_target",
                "ix_weather_current_values_location_style_target",
            }.issubset(
                {item["name"] for item in inspect(engine).get_indexes("weather_current_values")}
            )
            assert "weather_provider_credentials" in inspect(engine).get_table_names()
            assert "weather_admin_session_revocations" in inspect(engine).get_table_names()
            assert "weather_admin_login_rate_limits" in inspect(engine).get_table_names()
            current_value = connection.execute(
                text(
                    "SELECT value_id FROM weather_current_values "
                    "WHERE location_id = 'projection-backfill'"
                )
            ).scalar_one()
            assert current_value == "projection-value-new"
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


_MARKER_INDEXES = (
    "ix_weather_values_marker_lookup",
    "ix_weather_values_marker_observed",
    "ix_weather_values_alert_lookup",
)
_PROJECTION_INDEXES = ("ix_weather_current_values_alert_lookup",)


def _index_definitions(connection, table: str = "weather_values") -> dict[str, str]:
    rows = connection.execute(
        text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :table"),
        {"table": table},
    ).all()
    return {name: definition for name, definition in rows}


def test_create_all_and_alembic_build_identical_marker_indexes(monkeypatch) -> None:
    """Both schema paths must produce byte-identical marker index definitions.

    ``create_all`` and ``alembic upgrade`` each claim these index names, and
    ``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` silently accepts whatever is
    already there. A definition that merely shares the name is therefore
    permanent: an ascending index cannot serve the marker ORDER BY, so the
    query degrades to a per-location top-N sort and nothing reports it.
    Comparing names alone -- as this suite used to -- cannot catch that.
    """
    database_url = TEST_DATABASE_URL
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        repository = WeatherRepository(database_url)
        engine = repository.engine

        # 1) schema built by the ORM metadata
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
        repository.create_schema()
        with engine.connect() as connection:
            from_create_all = _index_definitions(connection)
            from_create_all.update(
                _index_definitions(connection, "weather_current_values")
            )

        # 2) schema built by the migration chain
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
        command.upgrade(Config("alembic.ini"), "head")
        with engine.connect() as connection:
            from_alembic = _index_definitions(connection)
            from_alembic.update(
                _index_definitions(connection, "weather_current_values")
            )

        for name in _MARKER_INDEXES + _PROJECTION_INDEXES:
            assert name in from_create_all, f"{name} missing from create_all schema"
            assert name in from_alembic, f"{name} missing from alembic schema"
            assert from_create_all[name] == from_alembic[name], (
                f"{name} differs between schema paths:\n"
                f"  create_all: {from_create_all[name]}\n"
                f"  alembic   : {from_alembic[name]}"
            )
            # The marker queries read revisions descending; an ascending index
            # cannot serve that ordering.
            assert "DESC" in from_alembic[name]
    finally:
        get_settings.cache_clear()


def test_migration_leaves_a_pre_built_index_alone(monkeypatch) -> None:
    """An index built ahead of the deploy must survive the migration untouched.

    ``deploy/n150.md`` tells the operator to create these concurrently outside
    the deploy window, because building them while ``migrate`` holds the API
    down costs tens of minutes on a 28 GB table. That only saves anything if
    ``IF NOT EXISTS`` really does leave the pre-built index in place -- the oid
    must not change.
    """
    database_url = TEST_DATABASE_URL
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        engine = WeatherRepository(database_url).engine
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
        config = Config("alembic.ini")
        command.upgrade(config, "0008_admin_login_rate_limits")

        # Exactly the statements deploy/n150.md tells the operator to run.
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE INDEX ix_weather_values_marker_observed ON weather_values "
                "(location_id, metric_key, known_at DESC NULLS LAST, "
                "source_record_key DESC NULLS LAST, value_id DESC) "
                "WHERE metric_key IN ('TEMP', 'T1H', 'TMP', 'WEATHER_CODE', 'SKY', 'PTY') "
                "AND forecast_style IN ('observed', 'nowcast')"
            )
            connection.exec_driver_sql(
                "CREATE INDEX ix_weather_values_alert_lookup ON weather_values "
                "(location_id, target_at DESC) "
                "WHERE weather_domain = 'weather_alert' OR metric_key = 'ALERT'"
            )
            # ``exec_driver_sql`` hands the string to psycopg, which reads
            # ``%`` as a placeholder; go through ``text()`` for the ILIKE
            # patterns.
            connection.execute(
                text(
                    "CREATE INDEX ix_weather_current_values_alert_lookup "
                    "ON weather_current_values (location_id, target_at DESC) "
                    "WHERE metric_key = 'ALERT' OR weather_domain ILIKE '%alert%' "
                    "OR weather_domain ILIKE '%warning%' "
                    "OR dataset_key ILIKE '%alert%' "
                    "OR dataset_key ILIKE '%warning%'"
                )
            )

        names = _MARKER_INDEXES + _PROJECTION_INDEXES

        def oids(connection) -> dict[str, int]:
            rows = connection.execute(
                text(
                    "SELECT relname, oid FROM pg_class "
                    "WHERE relname = ANY(:names)"
                ),
                {"names": list(names)},
            ).all()
            return {name: oid for name, oid in rows}

        with engine.connect() as connection:
            before = oids(connection)

        command.upgrade(config, "head")

        with engine.connect() as connection:
            after = oids(connection)
            definitions = _index_definitions(connection)
            definitions.update(_index_definitions(connection, "weather_current_values"))

        for name in names:
            if name not in before:
                # 0006 builds the marker lookup; it is not pre-created here.
                continue
            assert before[name] == after[name], f"{name} was rebuilt by the migration"
        # And the migration must still have produced every index it owns.
        for name in names:
            assert name in definitions
    finally:
        get_settings.cache_clear()


def test_migration_rebuilds_an_index_left_invalid_by_a_failed_build(
    monkeypatch,
) -> None:
    """A failed concurrent build must not be skipped forever by IF NOT EXISTS."""
    database_url = TEST_DATABASE_URL
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        engine = WeatherRepository(database_url).engine
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
        config = Config("alembic.ini")
        command.upgrade(config, "0008_admin_login_rate_limits")
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE INDEX ix_weather_values_alert_lookup ON weather_values "
                "(location_id, target_at DESC) "
                "WHERE weather_domain = 'weather_alert' OR metric_key = 'ALERT'"
            )
            # Mark it the way an interrupted CONCURRENTLY build leaves it.
            connection.exec_driver_sql(
                "UPDATE pg_index SET indisvalid = false WHERE indexrelid = "
                "'ix_weather_values_alert_lookup'::regclass"
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            valid = connection.execute(
                text(
                    "SELECT i.indisvalid FROM pg_class c "
                    "JOIN pg_index i ON i.indexrelid = c.oid "
                    "WHERE c.relname = 'ix_weather_values_alert_lookup'"
                )
            ).scalar_one()
        assert valid is True
    finally:
        get_settings.cache_clear()
