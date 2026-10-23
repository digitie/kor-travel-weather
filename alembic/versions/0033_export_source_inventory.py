"""quota 선택 밖 위치도 유지하는 작은 위치별 승인 source inventory."""

import sqlalchemy as sa

from alembic import op

revision = "0033_export_source_inventory"
down_revision = "0032_export_publication_pending"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("weather_export_external_cohorts", sa.Column("descriptors", sa.JSON()))
    op.create_table(
        "weather_export_approved_locations",
        sa.Column("provider", sa.String(120), primary_key=True),
        sa.Column("dataset_key", sa.String(160), primary_key=True),
        sa.Column("location_id", sa.String(120), primary_key=True),
        sa.Column("namespace", sa.String(64), nullable=False),
        sa.Column("generation_id", sa.String(36), sa.ForeignKey(
            "weather_export_generations.generation_id", ondelete="RESTRICT",
        ), nullable=False),
        sa.Column("source_scope", sa.String(64), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("nx", sa.Integer()), sa.Column("ny", sa.Integer()),
        sa.Column("source_collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_weather_export_approved_locations_namespace", "weather_export_approved_locations",
        ["namespace"],
    )


def downgrade():
    op.drop_table("weather_export_approved_locations")
    op.drop_column("weather_export_external_cohorts", "descriptors")
