"""index the observed/nowcast slice the map marker lookup actually reads.

Also repairs ``ix_weather_values_marker_lookup`` when an earlier
``Base.metadata.create_all`` built it with ascending trailing columns.  The
marker query walks revisions descending, so an ascending index forces a
per-location top-N sort instead of a bounded index read.

Build cost: ``weather_values`` held 20,282,755 rows / 28 GB on the production
deployment when this was written.  ``CREATE INDEX CONCURRENTLY`` does not use
parallel workers and makes two passes, so budget several minutes per index and
do not interrupt the migrate step -- ``api`` and ``dagster`` wait on it.  To
avoid that window entirely, build both indexes with psql ahead of the deploy;
this migration then finds them already correct and returns immediately.
"""

import sqlalchemy as sa

from alembic import op
from kortravelweather.index_ddl import ensure_concurrent_index

revision = "0009_marker_observed_index"
down_revision = "0008_admin_login_rate_limits"
branch_labels = None
depends_on = None

_OBSERVED_INDEX = "ix_weather_values_marker_observed"
_LOOKUP_INDEX = "ix_weather_values_marker_lookup"
_METRIC_PREDICATE = "metric_key IN ('TEMP', 'T1H', 'TMP', 'WEATHER_CODE', 'SKY', 'PTY')"
_STYLE_PREDICATE = "forecast_style IN ('observed', 'nowcast')"
_ORDERED_COLUMNS = (
    "(location_id, metric_key, known_at DESC NULLS LAST, "
    "source_record_key DESC NULLS LAST, value_id DESC)"
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        ensure_concurrent_index(
            op.execute,
            bind,
            name=_LOOKUP_INDEX,
            table="weather_values",
            columns=_ORDERED_COLUMNS,
            where=_METRIC_PREDICATE,
        )
        ensure_concurrent_index(
            op.execute,
            bind,
            name=_OBSERVED_INDEX,
            table="weather_values",
            columns=_ORDERED_COLUMNS,
            where=f"{_METRIC_PREDICATE} AND {_STYLE_PREDICATE}",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_OBSERVED_INDEX}"))
