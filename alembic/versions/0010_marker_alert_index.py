"""index the alert slice the map marker lookup scans on every batch.

``marker_values_many`` ranks alert revisions for every marker batch, filtering
on ``weather_domain``/``metric_key``.  No index covered that predicate, so
PostgreSQL fell back to a parallel sequential scan of the whole append-only
table: measured on production (20,282,755 rows, 28 GB) it read ~20 GB and took
66 s cold to return 7,860 of the 88,298 alert rows the table holds.  That scan,
not the observed lookup, is what pushed a map marker batch past the gateway
timeout.

Build cost: see the note in 0009 -- this is a second concurrent build on the
same 28 GB table, so the two together dominate the deploy window unless they
are built with psql beforehand.
"""

import sqlalchemy as sa

from alembic import op
from kortravelweather.index_ddl import ensure_concurrent_index

revision = "0010_marker_alert_index"
down_revision = "0009_marker_observed_index"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_weather_values_alert_lookup"
_ALERT_PREDICATE = "weather_domain = 'weather_alert' OR metric_key = 'ALERT'"
_ORDERED_COLUMNS = "(location_id, target_at DESC)"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        ensure_concurrent_index(
            op.execute,
            bind,
            name=_INDEX_NAME,
            table="weather_values",
            columns=_ORDERED_COLUMNS,
            where=_ALERT_PREDICATE,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}"))
