"""index the observed/nowcast slice the map marker lookup actually reads."""

import sqlalchemy as sa

from alembic import op

revision = "0009_marker_observed_index"
down_revision = "0008_admin_login_rate_limits"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_weather_values_marker_observed"
_METRIC_PREDICATE = "metric_key IN ('TEMP', 'T1H', 'TMP', 'WEATHER_CODE', 'SKY', 'PTY')"
_STYLE_PREDICATE = "forecast_style IN ('observed', 'nowcast')"


def _drop_invalid_index(bind: sa.engine.Connection) -> None:
    """Remove a leftover index from a failed CONCURRENTLY build.

    ``CREATE INDEX CONCURRENTLY`` leaves an unusable but existing index behind
    when it fails, and ``IF NOT EXISTS`` would then skip the retry forever.
    """
    invalid = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_class c "
            "JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE c.relname = :name AND NOT i.indisvalid"
        ),
        {"name": _INDEX_NAME},
    ).scalar()
    if invalid:
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}"))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # ``weather_values`` is append-only and can hold millions of rows, so
        # build without blocking API/Dagster reads during deploy.
        with op.get_context().autocommit_block():
            _drop_invalid_index(bind)
            op.execute(
                sa.text(
                    "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                    f"{_INDEX_NAME} ON weather_values "
                    "(location_id, metric_key, known_at DESC NULLS LAST, "
                    "source_record_key DESC NULLS LAST, value_id DESC) "
                    f"WHERE {_METRIC_PREDICATE} AND {_STYLE_PREDICATE}"
                )
            )
    else:
        op.create_index(
            _INDEX_NAME,
            "weather_values",
            [
                "location_id",
                "metric_key",
                "known_at",
                "source_record_key",
                "value_id",
            ],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}"))
    else:
        op.drop_index(_INDEX_NAME, table_name="weather_values")
