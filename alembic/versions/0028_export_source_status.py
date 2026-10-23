"""W4 수집 상태와 예정 시각의 작은 projection."""

import sqlalchemy as sa

from alembic import op

revision = "0028_export_source_status"
down_revision = "0027_export_forecast_index"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "weather_export_source_status",
        sa.Column("provider", sa.String(120), primary_key=True),
        sa.Column("dataset_key", sa.String(160), primary_key=True),
        sa.Column("forecast_style", sa.String(40), primary_key=True),
        sa.Column("last_run_id", sa.String(64)),
        sa.Column("last_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_finished_at", sa.DateTime(timezone=True)),
        sa.Column("last_status", sa.String(32)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("source_collected_at", sa.DateTime(timezone=True)),
        sa.Column("last_failed_at", sa.DateTime(timezone=True)),
        sa.Column("schedule_evaluated_at", sa.DateTime(timezone=True)),
        sa.Column("next_scheduled_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "forecast_style IN ('observed','nowcast','ultra_short','short','mid','external')",
            name="ck_weather_export_source_status_style",
        ),
        sa.CheckConstraint(
            "last_status IS NULL OR last_status IN "
            "('success','partial','failed','abandoned','cancelled')",
            name="ck_weather_export_source_status_terminal",
        ),
    )
    op.create_table(
        "weather_export_run_fetch_clocks",
        sa.Column(
            "run_id", sa.String(64),
            sa.ForeignKey("weather_sync_runs.run_id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("provider", sa.String(120), primary_key=True),
        sa.Column("dataset_key", sa.String(160), primary_key=True),
        sa.Column("first_fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "first_fetched_at <= last_fetched_at", name="ck_weather_export_fetch_clock_order"
        ),
    )


def downgrade():
    op.drop_table("weather_export_run_fetch_clocks")
    op.drop_table("weather_export_source_status")
