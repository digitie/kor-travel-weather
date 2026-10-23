"""복합 수집의 명시적인 상태 scope를 native 실행에 보존한다."""

import sqlalchemy as sa

from alembic import op

revision = "0030_export_run_scopes"
down_revision = "0029_daily_summaries"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("weather_sync_runs", sa.Column("export_status_scopes", sa.JSON()))


def downgrade():
    op.drop_column("weather_sync_runs", "export_status_scopes")
