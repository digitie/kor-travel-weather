"""인덱스 이름 충돌에서 다른 table/정의를 손대지 않는 migration 경계."""

from dataclasses import dataclass

import pytest
from alembic.config import Config
from sqlalchemy import text

from alembic import command
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import get_settings

NAMES = [
    "ix_weather_current_values_marker_current",
    "ix_weather_current_values_marker_forecast",
    "ix_weather_current_values_marker_provider_current",
    "ix_weather_current_values_marker_provider_forecast",
]


@dataclass
class MigrationDatabase:
    repo: WeatherRepository
    definitions: dict[str, str]

    def indexes(self):
        with self.repo.engine.connect() as connection:
            return dict(
                connection.execute(
                    text(
                        "SELECT c.relname,c.oid FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname='public' AND c.relname=ANY(:names)"
                    ),
                    {"names": NAMES},
                ).all()
            )

    def version(self):
        with self.repo.engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


@pytest.fixture
def migration_database(weather_test_database_url, monkeypatch):
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_DATABASE_URL", weather_test_database_url)
    get_settings.cache_clear()
    repo = WeatherRepository(weather_test_database_url)
    with repo.engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA public CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA public")
    repo.create_schema()
    with repo.engine.begin() as connection:
        definitions = dict(
            connection.execute(
                text("SELECT indexname,indexdef FROM pg_indexes WHERE indexname=ANY(:names)"),
                {"names": NAMES},
            ).all()
        )
        for name in NAMES:
            connection.exec_driver_sql("DROP INDEX " + name)
    command.stamp(Config("alembic.ini"), "0033_export_source_inventory")
    try:
        yield MigrationDatabase(repo, definitions)
    finally:
        repo.engine.dispose()
        get_settings.cache_clear()


@pytest.mark.parametrize("valid", [False, True], ids=["invalid", "valid"])
def test_other_table_collision_is_never_dropped_or_skipped(migration_database, valid):
    db = migration_database
    name = NAMES[-1]
    with db.repo.engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE marker_collision (LIKE weather_current_values)")
        connection.exec_driver_sql(
            db.definitions[name].replace(
                "ON public.weather_current_values ",
                "ON public.marker_collision ",
            )
        )
        connection.execute(
            text("UPDATE pg_index SET indisvalid=:valid WHERE indexrelid=to_regclass(:name)"),
            {"valid": valid, "name": name},
        )
    before = db.indexes()
    with pytest.raises(RuntimeError, match="충돌"):
        command.upgrade(Config("alembic.ini"), "head")
    assert db.indexes() == before and set(before) == {name}
    assert db.version() == "0033_export_source_inventory"
    with db.repo.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT indisvalid FROM pg_index WHERE indexrelid=:oid"), {"oid": before[name]}
            ).scalar_one()
            is valid
        )


@pytest.mark.parametrize(
    "variant",
    [
        "columns",
        "direction",
        "predicate",
        "include",
        "opclass",
        "collation",
        "expression",
        "unique",
    ],
)
@pytest.mark.parametrize("valid", [False, True], ids=["invalid", "valid"])
def test_wrong_definition_is_never_dropped_or_marked_complete(migration_database, variant, valid):
    db = migration_database
    name = NAMES[0]
    definition = db.definitions[name]
    if variant == "columns":
        definition = f"CREATE INDEX {name} ON weather_current_values (location_id)"
    elif variant == "direction":
        definition = definition.replace("target_at DESC", "target_at", 1)
    elif variant == "predicate":
        definition = definition.replace("'PTY'::character varying", "'MISSING'::character varying")
    elif variant == "include":
        definition = definition.replace(
            "INCLUDE (provider, forecast_style", "INCLUDE (forecast_style"
        )
    elif variant == "opclass":
        definition = definition.replace("metric_key,", "metric_key text_pattern_ops,", 1)
    elif variant == "collation":
        definition = definition.replace("metric_key,", 'metric_key COLLATE "C",', 1)
    elif variant == "expression":
        definition = definition.replace("(location_id,", "(lower(location_id),", 1)
    elif variant == "unique":
        definition = definition.replace("CREATE INDEX", "CREATE UNIQUE INDEX", 1)
    assert definition != db.definitions[name]
    with db.repo.engine.begin() as connection:
        connection.exec_driver_sql(definition)
        connection.execute(
            text("UPDATE pg_index SET indisvalid=:valid WHERE indexrelid=to_regclass(:name)"),
            {"valid": valid, "name": name},
        )
    before = db.indexes()
    with pytest.raises(RuntimeError, match="충돌"):
        command.upgrade(Config("alembic.ini"), "head")
    assert db.indexes() == before and set(before) == {name}
    assert db.version() == "0033_export_source_inventory"


def test_resolved_table_schema_is_used_even_with_another_first_schema(
    migration_database, monkeypatch
):
    db = migration_database
    name = NAMES[-1]
    with db.repo.engine.begin() as connection:
        connection.exec_driver_sql("CREATE SCHEMA marker_search")
        connection.exec_driver_sql("CREATE TABLE marker_search.marker_collision (location_id text)")
        connection.exec_driver_sql(
            f"CREATE INDEX {name} ON marker_search.marker_collision (location_id)"
        )
        foreign_oid = connection.execute(
            text("SELECT to_regclass('marker_search.'||:name)::oid"), {"name": name}
        ).scalar_one()
    monkeypatch.setenv("PGOPTIONS", "-csearch_path=marker_search,public")
    try:
        command.upgrade(Config("alembic.ini"), "head")
        assert set(db.indexes()) == set(NAMES)
        command.downgrade(Config("alembic.ini"), "0033_export_source_inventory")
        assert not db.indexes()
        with db.repo.engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT to_regclass('marker_search.'||:name)::oid"), {"name": name}
                ).scalar_one()
                == foreign_oid
            )
    finally:
        with db.repo.engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA marker_search CASCADE")


def test_downgrade_rejects_a_replaced_foreign_index_before_dropping_anything(migration_database):
    db = migration_database
    command.upgrade(Config("alembic.ini"), "head")
    name = NAMES[0]
    with db.repo.engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX " + name)
        connection.exec_driver_sql("CREATE TABLE marker_collision (location_id text)")
        connection.exec_driver_sql(f"CREATE INDEX {name} ON marker_collision (location_id)")
    before = db.indexes()
    with pytest.raises(RuntimeError, match="충돌"):
        command.downgrade(Config("alembic.ini"), "0033_export_source_inventory")
    assert db.indexes() == before
    assert db.version() == "0034_marker_kind_indexes"
