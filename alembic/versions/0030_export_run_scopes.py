"""복합 수집의 명시적인 상태 scope를 native 실행에 보존한다."""

import sqlalchemy as sa

from alembic import op

revision = "0030_export_run_scopes"
down_revision = "0029_daily_summaries"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("weather_sync_runs", sa.Column("export_status_scopes", sa.JSON()))
    op.create_table(
        "weather_export_status_pending",
        sa.Column("run_id", sa.String(64), sa.ForeignKey(
            "weather_sync_runs.run_id", ondelete="CASCADE",
        ), primary_key=True),
        sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_weather_export_status_pending_provider", "weather_export_status_pending",
                    ["provider"])


def downgrade():
    op.drop_table("weather_export_status_pending")
    op.drop_column("weather_sync_runs", "export_status_scopes")
