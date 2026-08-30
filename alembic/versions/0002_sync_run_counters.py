"""record middle-region and provider request counters on sync runs."""

import sqlalchemy as sa

from alembic import op

revision = "0002_sync_run_counters"
down_revision = "0001_weather_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "weather_sync_runs",
        sa.Column("mid_groups_fetched", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "weather_sync_runs",
        sa.Column("requests_fetched", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("weather_sync_runs", "requests_fetched")
    op.drop_column("weather_sync_runs", "mid_groups_fetched")
