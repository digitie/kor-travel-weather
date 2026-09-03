"""index the bounded metric projection used by map markers."""

import sqlalchemy as sa

from alembic import op

revision = "0006_marker_lookup_index"
down_revision = "0005_admin_session_revocations"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_weather_values_marker_lookup"
_MARKER_METRIC_PREDICATE = "metric_key IN ('TEMP', 'T1H', 'TMP', 'WEATHER_CODE', 'SKY', 'PTY')"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # This table is append-only and can contain millions of rows.  A
        # concurrent build keeps API/Dagster reads available during deploy.
        with op.get_context().autocommit_block():
            op.execute(
                sa.text(
                    "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                    f"{_INDEX_NAME} ON weather_values "
                    "(location_id, metric_key, known_at DESC NULLS LAST, "
                    "source_record_key DESC NULLS LAST, value_id DESC) "
                    f"WHERE {_MARKER_METRIC_PREDICATE}"
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
