"""index the alert slice of the current-value projection.

The nearby bundle reads warnings on their own budget so a shared row cap cannot
hide them.  A warning's ``target_at`` is its announcement time, which sits
below the forecast rows at the top of a location's projection slice, so a
newest-first read cannot stop early: for a location that has never carried a
warning it walks the entire slice and returns nothing.  Measured on a
production-density reproduction, 60 alert-free locations cost 913 ms and
229,918 buffers to return zero rows, and that cost grows with retention.

The predicate is kept identical to ``_ALERT_PROJECTION_PREDICATE`` in
``kortravelweather.repository`` so PostgreSQL can prove the index applies.

``weather_current_values`` held 2,132 MB on production, far smaller than
``weather_values``; this build is correspondingly quicker than 0009/0010.
"""

import sqlalchemy as sa

from alembic import op

revision = "0011_current_value_alert_index"
down_revision = "0010_marker_alert_index"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_weather_current_values_alert_lookup"
_ALERT_PREDICATE = (
    "metric_key = 'ALERT'"
    " OR weather_domain ILIKE '%alert%'"
    " OR weather_domain ILIKE '%warning%'"
    " OR dataset_key ILIKE '%alert%'"
    " OR dataset_key ILIKE '%warning%'"
)
_ORDERED_COLUMNS = "(location_id, target_at DESC)"


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
                f"{_INDEX_NAME} ON weather_current_values {_ORDERED_COLUMNS} "
                f"WHERE {_ALERT_PREDICATE}"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}"))
