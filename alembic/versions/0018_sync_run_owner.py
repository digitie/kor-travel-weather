"""중단된 수집의 Dagster worker 소유권을 기록한다."""

import sqlalchemy as sa

from alembic import op

revision = "0018_sync_run_owner"
down_revision = "0017_forward_partitions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("weather_sync_runs", sa.Column("orchestrator_run_id", sa.String(64)))
    op.create_index(
        "ix_weather_sync_runs_orchestrator_run_id", "weather_sync_runs", ["orchestrator_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_weather_sync_runs_orchestrator_run_id", table_name="weather_sync_runs")
    op.drop_column("weather_sync_runs", "orchestrator_run_id")
