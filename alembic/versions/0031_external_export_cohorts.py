"""외부 제공처가 실제 선택한 위치 집합의 bounded 최신 projection."""

import sqlalchemy as sa

from alembic import op

revision = "0031_external_export_cohorts"
down_revision = "0030_export_run_scopes"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("weather_export_snapshots", "source_collected_at", nullable=True)
    op.create_table(
        "weather_export_external_cohorts",
        sa.Column("provider", sa.String(40), primary_key=True),
        sa.Column("dataset_key", sa.String(120), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("run_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("targets", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("weather_export_external_cohorts")
    op.execute("DELETE FROM weather_export_snapshots WHERE source_collected_at IS NULL")
    op.alter_column("weather_export_snapshots", "source_collected_at", nullable=False)
