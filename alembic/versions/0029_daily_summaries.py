"""승인 관측의 일간 세 구간 요약과 증분 checkpoint."""

import sqlalchemy as sa

from alembic import op

revision = "0029_daily_summaries"
down_revision = "0028_export_source_status"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "weather_daily_summaries",
        sa.Column("location_id", sa.String(120), primary_key=True),
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("slot", sa.String(10), primary_key=True),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("hourly_winners", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_weather_daily_summaries_date", "weather_daily_summaries", ["date"])
    op.create_table(
        "weather_daily_summary_coverage",
        sa.Column("location_id", sa.String(120), primary_key=True),
        sa.Column("namespace", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("source_scope", sa.String(64), nullable=False),
        sa.Column("nx", sa.Integer()),
        sa.Column("ny", sa.Integer()),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("source_collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation_id", sa.String(36), nullable=False),
    )
    op.create_table(
        "weather_daily_summary_progress",
        sa.Column("progress_id", sa.Integer(), primary_key=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation_id", sa.String(36), nullable=False),
        sa.Column("source_collected_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("weather_daily_summary_progress")
    op.drop_table("weather_daily_summary_coverage")
    op.drop_table("weather_daily_summaries")
