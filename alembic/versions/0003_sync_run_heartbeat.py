"""track liveness of long-running provider syncs."""

import sqlalchemy as sa

from alembic import op

revision = "0003_sync_run_heartbeat"
down_revision = "0002_sync_run_counters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "weather_sync_runs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_weather_sync_runs_heartbeat", "weather_sync_runs", ["heartbeat_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_weather_sync_runs_heartbeat", table_name="weather_sync_runs")
    op.drop_column("weather_sync_runs", "heartbeat_at")
