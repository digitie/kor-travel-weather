"""예상 manifest·scope epoch·terminal 검증만 추가한다. collector/API는 활성화하지 않는다."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0024_export_plan_barrier"
down_revision = "0023_export_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("owner_epochs", sa.JSON()),
        sa.Column("plan_reference_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("plan_closed_at", sa.DateTime(timezone=True)),
        sa.Column("seal_digest", sa.String(64)),
        sa.Column("sealed_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("weather_export_attempts", column)
    op.create_table(
        "weather_export_scope_owners",
        sa.Column("source_scope", sa.String(64), primary_key=True),
        sa.Column("owner_epoch", sa.BigInteger(), nullable=False),
        sa.Column("active_attempt_id", sa.String(36), nullable=False),
        sa.CheckConstraint("owner_epoch > 0", name="ck_weather_export_owner_epoch"),
    )
    op.create_table(
        "weather_export_expected_chunks",
        sa.Column(
            "attempt_id",
            sa.String(36),
            sa.ForeignKey("weather_export_attempts.attempt_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("chunk_key", sa.String(64), primary_key=True),
        sa.Column("source_scopes", sa.JSON(), nullable=False),
        sa.Column("input_point_count", sa.Integer(), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "input_point_count BETWEEN 0 AND 5000", name="ck_weather_export_input_points"
        ),
    )
    op.create_table(
        "weather_export_source_closures",
        sa.Column(
            "attempt_id",
            sa.String(36),
            sa.ForeignKey("weather_export_attempts.attempt_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source_scope", sa.String(64), primary_key=True),
        sa.Column("chunk_keys", sa.JSON(), nullable=False),
    )
    op.create_table(
        "weather_export_validated_chunks",
        sa.Column("attempt_id", sa.String(36), primary_key=True),
        sa.Column("chunk_key", sa.String(64), primary_key=True),
        sa.Column("artifact_digest", sa.String(64), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["attempt_id", "chunk_key"],
            ["weather_export_chunks.attempt_id", "weather_export_chunks.chunk_key"],
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "weather_export_terminal_evidence",
        sa.Column(
            "attempt_id",
            sa.String(36),
            sa.ForeignKey("weather_export_attempts.attempt_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("execution_mode", sa.String(16), nullable=False),
        sa.Column("orchestrator_run_id", sa.String(64)),
        sa.Column("seal_digest", sa.String(64), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for name in (
        "weather_export_terminal_evidence",
        "weather_export_validated_chunks",
        "weather_export_source_closures",
        "weather_export_expected_chunks",
        "weather_export_scope_owners",
    ):
        op.drop_table(name)
    for name in (
        "sealed_at",
        "seal_digest",
        "plan_closed_at",
        "plan_reference_count",
        "owner_epochs",
    ):
        op.drop_column("weather_export_attempts", name)
