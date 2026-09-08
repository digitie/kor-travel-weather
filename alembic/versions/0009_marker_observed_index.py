"""index the observed/nowcast slice the map marker lookup actually reads.

The marker query walks revisions descending, so the trailing columns are
declared ``DESC NULLS LAST``; an ascending index cannot serve that ORDER BY and
forces a per-location top-N sort instead (measured 3.4 ms vs 132 ms).  The ORM
declaration in ``kortravelweather.repository`` states the same definition, and
``test_create_all_and_alembic_build_identical_marker_indexes`` compares the two
schema paths so they cannot drift apart again.

Build cost: ``weather_values`` held 20,282,755 rows / 28 GB on the production
deployment when this was written.  ``CREATE INDEX CONCURRENTLY`` does not use
parallel workers and makes two passes, so budget several minutes per index and
do not interrupt the migrate step -- ``api`` and ``dagster`` wait on it.  An
index built ahead of the deploy with the same definition is left alone by
``IF NOT EXISTS``, which is what ``deploy/n150.md`` relies on.
"""

import sqlalchemy as sa

from alembic import op

revision = "0009_marker_observed_index"
down_revision = "0008_admin_login_rate_limits"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_weather_values_marker_observed"
_METRIC_PREDICATE = "metric_key IN ('TEMP', 'T1H', 'TMP', 'WEATHER_CODE', 'SKY', 'PTY')"
_STYLE_PREDICATE = "forecast_style IN ('observed', 'nowcast')"
_ORDERED_COLUMNS = (
    "(location_id, metric_key, known_at DESC NULLS LAST, "
    "source_record_key DESC NULLS LAST, value_id DESC)"
)


def _drop_if_invalid(bind: sa.engine.Connection, name: str) -> None:
    """Reclaim an index a failed concurrent build left behind.

    ``IF NOT EXISTS`` would otherwise skip the retry forever.  Only the
    ``indisvalid`` flag is consulted -- an index that merely shares the name is
    trusted, because comparing definitions reliably is harder than it looks and
    getting it wrong would drop a healthy multi-gigabyte index mid-deploy.
    """
    if bind.execute(
        sa.text(
            "SELECT 1 FROM pg_class c "
            "JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE c.relname = :name AND NOT i.indisvalid"
        ),
        {"name": name},
    ).scalar():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        _drop_if_invalid(bind, _INDEX_NAME)
        op.execute(
            sa.text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                f"{_INDEX_NAME} ON weather_values {_ORDERED_COLUMNS} "
                f"WHERE {_METRIC_PREDICATE} AND {_STYLE_PREDICATE}"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}"))
