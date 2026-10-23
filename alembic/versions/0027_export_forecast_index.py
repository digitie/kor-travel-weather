"""예보 reader의 위치별 chunk index와 단조 공개 watermark."""

import sqlalchemy as sa

from alembic import op

revision = "0027_export_forecast_index"
down_revision = "0026_export_frozen_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_weather_export_generations_publication",
        "weather_export_generations",
        ["created_at", "generation_id"],
    )
    op.add_column("weather_export_generation_chunks", sa.Column("location_ids", sa.JSON()))
    op.create_table(
        "weather_export_publication_clock",
        sa.Column("clock_id", sa.Integer(), primary_key=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        "INSERT INTO weather_export_publication_clock (clock_id, published_at) "
        "SELECT 1, MAX(created_at) FROM weather_export_generations HAVING COUNT(*) > 0"
    )


def downgrade():
    op.drop_table("weather_export_publication_clock")
    op.drop_column("weather_export_generation_chunks", "location_ids")
    op.drop_index(
        "ix_weather_export_generations_publication", table_name="weather_export_generations"
    )
