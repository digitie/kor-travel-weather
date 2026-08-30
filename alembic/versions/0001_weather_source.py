"""weather locations/facts/sync runs."""

from alembic import op
import sqlalchemy as sa

revision = "0001_weather_source"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weather_locations",
        sa.Column("location_id", sa.String(length=120), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("nx", sa.Integer(), nullable=True),
        sa.Column("ny", sa.Integer(), nullable=True),
        sa.Column("region_code", sa.String(length=32), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_weather_locations_enabled_region", "weather_locations", ["enabled", "region_code"])
    op.create_index("ix_weather_locations_coordinates", "weather_locations", ["latitude", "longitude"])
    op.create_table(
        "weather_values",
        sa.Column("value_id", sa.String(length=64), primary_key=True),
        sa.Column("location_id", sa.String(length=120), sa.ForeignKey("weather_locations.location_id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=120), nullable=False),
        sa.Column("dataset_key", sa.String(length=160), nullable=False),
        sa.Column("weather_domain", sa.String(length=120), nullable=False),
        sa.Column("forecast_style", sa.String(length=40), nullable=False),
        sa.Column("timeline_bucket", sa.String(length=40), nullable=True),
        sa.Column("metric_key", sa.String(length=80), nullable=False),
        sa.Column("metric_name", sa.String(length=200), nullable=True),
        sa.Column("source_metric_key", sa.String(length=80), nullable=True),
        sa.Column("source_metric_name", sa.String(length=200), nullable=True),
        sa.Column("value_number", sa.Numeric(14, 4), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("severity", sa.String(length=64), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("normalization_version", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_record_key", sa.String(length=255), nullable=True),
        sa.UniqueConstraint(
            "location_id", "provider", "dataset_key", "metric_key", "issued_at", "valid_at", "observed_at",
            name="uq_weather_values_identity",
        ),
    )
    op.create_index("ix_weather_values_location_time", "weather_values", ["location_id", "valid_at", "observed_at"])
    op.create_index("ix_weather_values_dataset_metric", "weather_values", ["dataset_key", "metric_key"])
    op.create_table(
        "weather_sync_runs",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column("provider", sa.String(length=120), nullable=False),
        sa.Column("dataset_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locations_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("grids_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("values_loaded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_weather_sync_runs_started", "weather_sync_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_weather_sync_runs_started", table_name="weather_sync_runs")
    op.drop_table("weather_sync_runs")
    op.drop_index("ix_weather_values_dataset_metric", table_name="weather_values")
    op.drop_index("ix_weather_values_location_time", table_name="weather_values")
    op.drop_table("weather_values")
    op.drop_index("ix_weather_locations_coordinates", table_name="weather_locations")
    op.drop_index("ix_weather_locations_enabled_region", table_name="weather_locations")
    op.drop_table("weather_locations")
