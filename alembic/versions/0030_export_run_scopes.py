"""복합 수집의 명시적인 상태 scope를 native 실행에 보존한다."""

import sqlalchemy as sa

from alembic import op

revision = "0030_export_run_scopes"
down_revision = "0029_daily_summaries"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("weather_sync_runs", sa.Column("export_status_scopes", sa.JSON()))
    op.add_column("weather_sync_runs", sa.Column(
        "export_status_pending", sa.Boolean(), nullable=False, server_default=sa.false(),
    ))
    op.create_index(
        "ix_weather_sync_runs_export_pending", "weather_sync_runs",
        ["provider", "started_at", "run_id"],
        postgresql_where=sa.text("export_status_pending = true"),
    )


def downgrade():
    op.drop_index("ix_weather_sync_runs_export_pending", table_name="weather_sync_runs")
    op.drop_column("weather_sync_runs", "export_status_pending")
    op.drop_column("weather_sync_runs", "export_status_scopes")
