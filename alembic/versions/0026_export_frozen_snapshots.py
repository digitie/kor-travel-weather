"""서비스 export frozen manifest와 generation reader 참조."""

import sqlalchemy as sa

from alembic import op

revision = "0026_export_frozen_snapshots"
down_revision = "0025_export_success_heads"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "weather_export_snapshots",
        sa.Column("snapshot_id", sa.String(36), primary_key=True),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("source_collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_revision_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_weather_export_snapshots_manifest_digest",
        "weather_export_snapshots",
        ["manifest_digest"],
    )
    op.create_index(
        "ix_weather_export_snapshots_expires_at", "weather_export_snapshots", ["expires_at"]
    )
    op.create_table(
        "weather_export_snapshot_refs",
        sa.Column(
            "snapshot_id",
            sa.String(36),
            sa.ForeignKey("weather_export_snapshots.snapshot_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column(
            "generation_id",
            sa.String(36),
            sa.ForeignKey("weather_export_generations.generation_id"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_weather_export_snapshot_refs_generation_id",
        "weather_export_snapshot_refs",
        ["generation_id"],
    )


def downgrade():
    op.drop_table("weather_export_snapshot_refs")
    op.drop_table("weather_export_snapshots")
