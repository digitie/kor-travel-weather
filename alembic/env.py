"""Alembic migration environment for kor-travel-weather."""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from kortravelweather.repository import Base
from kortravelweather.settings import get_settings

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        if connection.dialect.name == "sqlite":
            # SQLite leaves foreign-key enforcement disabled per connection;
            # migrations must enable it just like the application engine.
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        # Alembic intentionally treats SQLite DDL as non-transactional.  In
        # that mode ``begin_transaction`` is a null context and the version
        # table write is otherwise rolled back when the connection closes.
        # Commit explicitly so ``upgrade head`` is repeatable on the default
        # development database.
        if connection.dialect.name == "sqlite":
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
