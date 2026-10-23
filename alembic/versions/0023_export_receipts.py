"""Map export의 성공 승격 전 attempt·chunk 검증 기록만 추가한다."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023_export_receipts"
down_revision = "0022_provider_marker_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weather_export_attempts",
        sa.Column("attempt_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("scope_key", sa.String(240), nullable=False),
        sa.Column("execution_mode", sa.String(16), nullable=False),
        sa.Column("orchestrator_run_id", sa.String(64)),
        sa.Column("planned_sources", sa.JSON(), nullable=False),
        sa.Column("plan_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("execution_mode IN ('native','dagster')", name="ck_weather_export_mode"),
        sa.CheckConstraint(
            "status IN ('preparing','sealed','failed')", name="ck_weather_export_status"
        ),
        sa.CheckConstraint(
            "execution_mode != 'dagster' OR orchestrator_run_id IS NOT NULL",
            name="ck_weather_export_dagster_id",
        ),
    )
    op.create_index("ix_weather_export_attempts_run_id", "weather_export_attempts", ["run_id"])
    op.create_table(
        "weather_export_chunks",
        sa.Column(
            "attempt_id",
            sa.String(36),
            sa.ForeignKey("weather_export_attempts.attempt_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("chunk_key", sa.String(64), primary_key=True),
        sa.Column("artifact_digest", sa.String(64), nullable=False),
        sa.Column("byte_count", sa.Integer(), nullable=False),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.Column("covered_sources", sa.JSON(), nullable=False),
        sa.Column("source_collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "byte_count BETWEEN 0 AND 8388608", name="ck_weather_export_chunk_bytes"
        ),
        sa.CheckConstraint("point_count BETWEEN 0 AND 5000", name="ck_weather_export_chunk_points"),
    )


def downgrade() -> None:
    op.drop_table("weather_export_chunks")
    op.drop_index("ix_weather_export_attempts_run_id", "weather_export_attempts")
    op.drop_table("weather_export_attempts")
