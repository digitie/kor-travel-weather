"""실제 revision DDL의 lock 상한과 caller GUC 복원/원자 rollback."""

import importlib.util
import time
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError
from test_export_external import external_repository as external_repository


def _revision():
    path = Path(__file__).parents[1] / "alembic/versions/0033_export_source_inventory.py"
    spec = importlib.util.spec_from_file_location("inventory_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("caller_lock,caller_statement", [(0, 0), (150, 700)])
def test_inventory_ddl_is_bounded_and_success_restores_shorter_caller_settings(
    external_repository, caller_lock, caller_statement,
):
    repo = external_repository
    migration = _revision()
    with repo.engine.begin() as c, Operations.context(MigrationContext.configure(c)):
        migration.downgrade()
    lock_connection = repo.engine.connect()
    transaction = lock_connection.begin()
    try:
        lock_connection.exec_driver_sql(
            "LOCK TABLE weather_export_external_cohorts IN ACCESS SHARE MODE"
        )
        started = time.monotonic()
        with pytest.raises(OperationalError), repo.engine.begin() as c:
            c.execute(text("SELECT set_config('lock_timeout', :value, true)"),
                      {"value": f"{caller_lock}ms"})
            c.execute(text("SELECT set_config('statement_timeout', :value, true)"),
                      {"value": f"{caller_statement}ms"})
            with Operations.context(MigrationContext.configure(c)):
                migration.upgrade()
        elapsed = time.monotonic() - started
        assert elapsed < (7 if caller_lock == 0 else 2)
        assert "weather_export_approved_locations" not in inspect(repo.engine).get_table_names()
        assert "descriptors" not in {
            column["name"] for column in inspect(repo.engine).get_columns(
                "weather_export_external_cohorts"
            )
        }
    finally:
        transaction.rollback()
        lock_connection.close()
    with repo.engine.begin() as c, Operations.context(MigrationContext.configure(c)):
        c.execute(text("SELECT set_config('lock_timeout', :value, true)"),
                  {"value": f"{caller_lock}ms"})
        c.execute(text("SELECT set_config('statement_timeout', :value, true)"),
                  {"value": f"{caller_statement}ms"})
        migration.upgrade()
        actual = dict(c.execute(text(
            "SELECT name, setting::bigint FROM pg_settings "
            "WHERE name IN ('lock_timeout', 'statement_timeout')"
        )).all())
        assert actual == {"lock_timeout": caller_lock, "statement_timeout": caller_statement}
