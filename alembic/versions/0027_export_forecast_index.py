"""예보 reader의 위치별 chunk index와 단조 공개 watermark."""

import sqlalchemy as sa

from alembic import op

revision = "0027_export_forecast_index"
down_revision = "0026_export_frozen_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("weather_export_generation_chunks", sa.Column("location_ids", sa.JSON()))
    op.create_table(
        "weather_export_publication_clock",
        sa.Column("clock_id", sa.Integer(), primary_key=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("weather_export_publication_clock")
    op.drop_column("weather_export_generation_chunks", "location_ids")
