"""index the alert slice the map marker lookup scans on every batch."""

import sqlalchemy as sa

from alembic import op

revision = "0010_marker_alert_index"
down_revision = "0009_marker_observed_index"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_weather_values_alert_lookup"
_ALERT_PREDICATE = "weather_domain = 'weather_alert' OR metric_key = 'ALERT'"
_ORDERED_COLUMNS = "(location_id, target_at DESC)"
_REQUIRED_FRAGMENT = "target_at DESC"


def _existing_definition(bind: sa.engine.Connection, name: str) -> str | None:
    return bind.execute(
        sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
        {"name": name},
    ).scalar()


def _is_invalid(bind: sa.engine.Connection, name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_class c "
                "JOIN pg_index i ON i.indexrelid = c.oid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": name},
        ).scalar()
    )


def _drop_unusable(bind: sa.engine.Connection, name: str) -> None:
    """Drop a leftover invalid build or a same-named index with wrong ordering."""
    definition = _existing_definition(bind, name)
    if definition is None:
        return
    if _is_invalid(bind, name) or _REQUIRED_FRAGMENT not in definition:
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # ``marker_values_many`` ranks alert revisions for every marker batch,
    # filtering on ``weather_domain``/``metric_key``.  No index covered that
    # predicate, so PostgreSQL fell back to a parallel sequential scan of the
    # whole append-only table: measured on production (20,282,755 rows, 28 GB)
    # it read ~20 GB and took 66 s cold to return 7,860 of the 88,298 alert
    # rows the table holds.  That scan, not the observed lookup, is what pushed
    # a map marker batch past the gateway timeout.
    with op.get_context().autocommit_block():
        _drop_unusable(bind, _INDEX_NAME)
        op.execute(
            sa.text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                f"{_INDEX_NAME} ON weather_values {_ORDERED_COLUMNS} "
                f"WHERE {_ALERT_PREDICATE}"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}"))
