from __future__ import annotations

import os
from pathlib import Path
from typing import Any

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
            assert version == "0016_purge_lookup_indexes"
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
            projection_indexes = {
                item["name"]
                for item in inspect(engine).get_indexes("weather_current_values")
            }
            assert "ix_weather_current_values_alert_lookup" in projection_indexes
            # Without this the bundle read ranks a location's whole slice.
            assert "ix_weather_current_values_location_current" in projection_indexes
            projection_columns = {
                item["name"]
                for item in inspect(engine).get_columns("weather_current_values")
            }
            # The bundle sort keys must live on the pointer table, or the
            # per-location limit cannot be applied before the fact join.
            assert {"known_at", "source_record_key"} <= projection_columns
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
            # 0012 backfills the denormalised sort keys.  Removing NOT NULL took
            # away the only runtime assertion that the backfill ran at all, so
            # check the values here: disabling the loop entirely used to leave
            # the whole suite green.
            keys = connection.execute(
                text(
                    "SELECT cv.known_at = wv.known_at "
                    "   AND cv.source_record_key = wv.source_record_key "
                    "FROM weather_current_values cv "
                    "JOIN weather_values wv ON wv.value_id = cv.value_id "
                    "WHERE cv.location_id = 'projection-backfill'"
                )
            ).scalar_one()
            assert keys is True, (
                "the projection's sort keys do not match the fact it points at; "
                "the backfill did not run or did not finish"
            )
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
                    "forecast_style, metric_key, target_at, known_at, "
                    "normalization_version, payload, "
                    "collected_at, source_record_key, value_number) VALUES "
                    "('immutable-value', 'immutability', 'p', 'd', 'd', 'short', 'TMP', "
                    "'2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00', 'test', '{}', "
                    "'2026-01-01 00:00:00+00', 'immutable-source', 1)"
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
_PROJECTION_INDEXES = (
    "ix_weather_current_values_alert_lookup",
    "ix_weather_current_values_location_current",
)
#: The purge's "still cited?" lookup. Unlike the marker/projection indexes
#: above, these are plain (no expression, no predicate) -- the assertion this
#: shares a loop with only checks that both schema paths agree, not ordering.
_PURGE_LOOKUP_INDEXES = (
    "ix_weather_values_source_record_key",
    "ix_weather_sync_run_sources_source_record_key",
)


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
            from_create_all.update(
                _index_definitions(connection, "weather_sync_run_sources")
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
            from_alembic.update(
                _index_definitions(connection, "weather_sync_run_sources")
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

        for name in _PURGE_LOOKUP_INDEXES:
            assert name in from_create_all, f"{name} missing from create_all schema"
            assert name in from_alembic, f"{name} missing from alembic schema"
            assert from_create_all[name] == from_alembic[name], (
                f"{name} differs between schema paths:\n"
                f"  create_all: {from_create_all[name]}\n"
                f"  alembic   : {from_alembic[name]}"
            )
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

        # 0013's index needs 0012's columns, so it can only be pre-built after
        # that revision -- which is what the runbook says. Proving the ordering
        # works here is the point: the previous version of this test listed the
        # index but never created it, so the survival check skipped it.
        command.upgrade(config, "0012_current_value_sort_keys")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE INDEX ix_weather_current_values_location_current "
                    "ON weather_current_values "
                    "(location_id, "
                    " (CASE WHEN forecast_style IN ('observed', 'nowcast') "
                    "  THEN 0 ELSE 1 END), "
                    " target_at DESC, known_at DESC NULLS LAST, "
                    " source_record_key DESC NULLS LAST, value_id DESC)"
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

        # 0015 rebuilds ``weather_values`` to partition it, so every index on
        # that table is necessarily recreated and pre-building one saves
        # nothing.  Only the projection's indexes survive a deploy, and those
        # are the ones the runbook still tells operators to build ahead.
        rebuilt_by_partitioning = set(_MARKER_INDEXES)
        expected_prebuilt = set(names) - rebuilt_by_partitioning
        for name in sorted(rebuilt_by_partitioning & set(before)):
            assert before[name] != after.get(name), (
                f"{name} kept its oid across 0015; if the fact table is no "
                "longer rebuilt, the runbook should go back to pre-building it"
            )
        assert expected_prebuilt <= set(before), (
            f"{sorted(expected_prebuilt - set(before))} were never pre-created, so "
            "this test would prove nothing about them"
        )
        for name in sorted(expected_prebuilt):
            assert before[name] == after[name], f"{name} was rebuilt by the migration"
        # And the migration must still have produced every index it owns.
        for name in names:
            assert name in definitions
    finally:
        get_settings.cache_clear()


def _revision_module(name: str):  # noqa: ANN202
    """Import a migration by path; alembic never exposes them as modules."""
    import importlib.util

    path = Path("alembic") / "versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"revision_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_backfill_pass_reports_exactly_what_it_repaired(monkeypatch) -> None:
    """``upgrade()`` stops when a pass reports zero, so the count has to be real.

    The pass is one statement over the whole table, and its return value is the
    only thing standing between "every pointer agrees with its fact" and
    "alembic recorded the revision anyway".  A count that is too low ends the
    loop early; one that never reaches zero fails the deploy.  So: N rows in
    need of repair must report exactly N, and the pass that follows must report
    zero rather than repairing them a second time.
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
        command.upgrade(config, "0012_current_value_sort_keys")

        rows = 7
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO weather_locations "
                    "(location_id, name, latitude, longitude, created_at, updated_at) "
                    "VALUES ('sweep', 'Sweep', 37, 127, "
                    "'2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00')"
                )
            )
            for index in range(rows):
                connection.execute(
                    text(
                        "INSERT INTO weather_source_records "
                        "(source_record_key, provider, dataset_key, source_entity_type, "
                        "source_entity_id, raw_payload_hash, payload, fetched_at, "
                        "imported_at) VALUES (:key, 'p', 'd', 'weather_response', "
                        "'sweep', :key, '{}', :at, :at)"
                    ),
                    {"key": f"sweep-source-{index}", "at": "2026-01-01 00:00:00+00"},
                )
                connection.execute(
                    text(
                        "INSERT INTO weather_values "
                        "(value_id, location_id, provider, dataset_key, weather_domain, "
                        "forecast_style, metric_key, target_at, known_at, "
                        "normalization_version, payload, collected_at, source_record_key, "
                        "value_number) VALUES (:value_id, 'sweep', 'p', 'd', 'weather', "
                        "'short', :metric, '2026-01-01 02:00:00+00', :at, 'test', '{}', "
                        ":at, :key, 1)"
                    ),
                    {
                        "value_id": f"sweep-value-{index}",
                        "metric": f"M{index}",
                        "at": "2026-01-01 00:00:00+00",
                        "key": f"sweep-source-{index}",
                    },
                )
                # A pointer row as the outgoing release writes it: no sort keys.
                connection.execute(
                    text(
                        "INSERT INTO weather_current_values "
                        "(value_id, location_id, provider, dataset_key, weather_domain, "
                        "forecast_style, metric_key, target_at) "
                        "VALUES (:value_id, 'sweep', 'p', 'd', 'weather', 'short', "
                        ":metric, '2026-01-01 02:00:00+00')"
                    ),
                    {"value_id": f"sweep-value-{index}", "metric": f"M{index}"},
                )

        revision = _revision_module("0012_current_value_sort_keys")

        with engine.begin() as connection:
            repaired = revision._repair_pass(connection)
        assert repaired == rows, (
            f"the pass reported {repaired} of {rows} rows repaired; upgrade() "
            "treats that number as the amount of work left to do"
        )

        with engine.connect() as connection:
            disagreeing = connection.execute(
                text(
                    "SELECT count(*) FROM weather_current_values cv "
                    "JOIN weather_values wv ON wv.value_id = cv.value_id "
                    "WHERE cv.source_record_key IS DISTINCT FROM wv.source_record_key "
                    "   OR cv.known_at IS DISTINCT FROM wv.known_at"
                )
            ).scalar_one()
        assert disagreeing == 0

        # A pass that changes nothing is what upgrade() takes as proof that
        # every row is consistent, so it has to actually mean that.
        with engine.begin() as connection:
            assert revision._repair_pass(connection) == 0
    finally:
        get_settings.cache_clear()


def test_the_bundle_ordering_is_served_by_its_index(monkeypatch) -> None:
    """Plan the real statement and refuse a plan that has to sort.

    The ordering expression exists four times: SQLAlchemy's ``case()`` in the
    query, ``_CURRENT_PREFERENCE_EXPRESSION`` in the ORM index, ``_PREFERENCE``
    in revision 0013, and the pre-build block in ``deploy/n150.md``.  Comparing
    the strings would still not answer the only question that matters, because
    the query's copy is not a string at all -- PostgreSQL has to agree that the
    compiled ``case()`` and the stored index expression are the same thing.

    So ask PostgreSQL.  With sequential scans off, a matching expression yields
    an index scan that stops at the LIMIT; a mismatch yields a Sort node, and
    the read goes back to ranking every row in the location's slice.  This test
    binds the query to the ORM index; the migration's copy is held to the ORM
    index by ``test_create_all_and_alembic_build_identical_marker_indexes``, and
    the runbook's copy by ``test_migration_leaves_a_pre_built_index_alone``.
    """
    import contextlib
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from sqlalchemy.dialects import postgresql

    from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue

    database_url = TEST_DATABASE_URL
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        repository = WeatherRepository(database_url)
        with repository.engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
        repository.create_schema()

        locations = [f"plan-{index}" for index in range(12)]
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for location_id in locations:
            repository.upsert_location(
                WeatherLocation(
                    location_id=location_id,
                    name=location_id,
                    latitude=37.5,
                    longitude=127.0,
                )
            )
            repository.record_source(
                source_record_key=f"plan-source-{location_id}",
                provider="p",
                dataset_key="d",
                source_entity_type="weather_response",
                source_entity_id=location_id,
                payload={"rows": [], "location": location_id},
            )
        repository.upsert_values(
            [
                WeatherValue(
                    location_id=location_id,
                    provider="p",
                    dataset_key="d",
                    weather_domain="weather",
                    forecast_style=(
                        ForecastStyle.OBSERVED if index % 3 == 0 else ForecastStyle.SHORT
                    ),
                    metric_key=f"M{index}",
                    target_at=base + timedelta(hours=index),
                    known_at=base,
                    value_number=Decimal(index),
                    source_record_key=f"plan-source-{location_id}",
                )
                for location_id in locations
                for index in range(40)
            ]
        )
        with repository.engine.begin() as connection:
            connection.execute(text("ANALYZE weather_current_values"))

        captured: list[str] = []

        class _Stop(Exception):
            pass

        class _Recorder:
            def execute(self, statement, *args, **kwargs):  # noqa: ANN001,ANN002,ANN003
                captured.append(
                    str(
                        statement.compile(
                            dialect=postgresql.dialect(),
                            compile_kwargs={"literal_binds": True},
                        )
                    )
                )
                raise _Stop

            def scalars(self, statement, *args, **kwargs):  # noqa: ANN001,ANN002,ANN003
                raise _Stop

        with contextlib.suppress(_Stop):
            WeatherRepository._current_value_models_many(
                WeatherRepository.__new__(WeatherRepository),
                _Recorder(),
                locations,
                limit_per_location=5,
                prefer_current=True,
            )
        assert captured, "no statement was compiled"
        statement = captured[0]
        assert "ORDER BY CASE WHEN" in statement, (
            "the read no longer orders by the preference expression; this test "
            "is planning something other than what it claims to"
        )

        with repository.engine.connect() as connection:
            # Not a performance assertion -- the fixture is far too small for the
            # planner to care.  Disabling the alternative is what makes the
            # question "can this index serve the ordering" answerable at all.
            connection.execute(text("SET enable_seqscan = off"))
            plan = "\n".join(
                line for (line,) in connection.execute(text("EXPLAIN " + statement))
            )

        assert "ix_weather_current_values_location_current" in plan, (
            f"the bundle read does not use its index:\n{plan}"
        )
        assert "Sort" not in plan, (
            "PostgreSQL had to sort, so the query's ORDER BY and the index "
            f"expression are not the same expression:\n{plan}"
        )
    finally:
        get_settings.cache_clear()


def test_backfill_corrects_sort_keys_left_stale_by_the_outgoing_release(
    monkeypatch,
) -> None:
    """Stale sort keys must be repaired, not just missing ones.

    Compose starts this migration while the previous release is still serving.
    That release moves a pointer by writing ``value_id`` alone -- the new
    columns are not in its model -- so the row ends up naming a new fact while
    carrying the *previous* fact's ``known_at`` and ``source_record_key``.  The
    keys are wrong but not null, so a "backfill what is missing" pass walks past
    the row forever and every later read orders that location on a fact it no
    longer points at.

    Re-running the revision is how such a row gets repaired, which is also the
    retry an operator performs after an interrupted deploy: alembic records the
    version only once ``upgrade()`` returns, so the second run has to be both
    safe and effective.
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
        command.upgrade(config, "0006_marker_lookup_index")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO weather_locations "
                    "(location_id, name, latitude, longitude, created_at, updated_at) "
                    "VALUES ('stale-keys', 'Stale', 37, 127, "
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
                        "source_entity_id, raw_payload_hash, payload, fetched_at, "
                        "imported_at) VALUES (:source_key, 'p', 'd', 'weather_response', "
                        "'stale-keys', :hash, '{}', :known_at, :known_at)"
                    ),
                    {
                        "source_key": f"stale-source-{suffix}",
                        "hash": f"stale-hash-{suffix}",
                        "known_at": known_at,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO weather_values "
                        "(value_id, location_id, provider, dataset_key, weather_domain, "
                        "forecast_style, metric_key, target_at, known_at, "
                        "normalization_version, payload, collected_at, source_record_key, "
                        "value_number) VALUES (:value_id, 'stale-keys', 'p', 'd', "
                        "'weather', 'short', 'TMP', '2026-01-01 02:00:00+00', :known_at, "
                        "'test', '{}', :known_at, :source_key, 1)"
                    ),
                    {
                        "value_id": f"stale-value-{suffix}",
                        "known_at": known_at,
                        "source_key": f"stale-source-{suffix}",
                    },
                )
        command.upgrade(config, "0012_current_value_sort_keys")

        def sort_keys() -> tuple[Any, ...]:
            with engine.connect() as connection:
                return tuple(
                    connection.execute(
                        text(
                            "SELECT value_id, known_at, source_record_key "
                            "FROM weather_current_values "
                            "WHERE location_id = 'stale-keys'"
                        )
                    ).one()
                )

        value_id, _, source_key = sort_keys()
        assert value_id == "stale-value-new"
        assert source_key == "stale-source-new"

        # Exactly what the outgoing release's UPDATE path writes: the pointer
        # moves, the denormalised keys do not.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE weather_current_values SET value_id = 'stale-value-old' "
                    "WHERE location_id = 'stale-keys'"
                )
            )
        assert sort_keys()[:3:2] == ("stale-value-old", "stale-source-new"), (
            "the pointer did not move, or moved its keys with it -- either way "
            "the stale row this test exists to repair was never created"
        )

        # An operator re-running the interrupted revision.
        command.stamp(config, "0011_current_value_alert_index")
        command.upgrade(config, "0012_current_value_sort_keys")

        value_id, known_at, source_key = sort_keys()
        assert value_id == "stale-value-old"
        assert source_key == "stale-source-old", (
            "the backfill skipped a row whose keys were stale rather than null, "
            "so its ordering names a fact the pointer no longer references"
        )
        assert known_at.isoformat().startswith("2026-01-01T00:00")
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
