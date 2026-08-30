"""weather locations/facts/sync runs."""

import sqlalchemy as sa

from alembic import op

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
        sa.CheckConstraint(
            "latitude >= 33 AND latitude <= 43", name="ck_weather_locations_latitude"
        ),
        sa.CheckConstraint(
            "longitude >= 124 AND longitude <= 132", name="ck_weather_locations_longitude"
        ),
        sa.CheckConstraint("nx IS NULL OR (nx >= 1 AND nx <= 300)", name="ck_weather_locations_nx"),
        sa.CheckConstraint("ny IS NULL OR (ny >= 1 AND ny <= 300)", name="ck_weather_locations_ny"),
    )
    op.create_index(
        "ix_weather_locations_enabled_region", "weather_locations", ["enabled", "region_code"]
    )
    op.create_index(
        "ix_weather_locations_coordinates", "weather_locations", ["latitude", "longitude"]
    )
    op.create_table(
        "weather_source_records",
        sa.Column("source_record_key", sa.String(length=255), primary_key=True),
        sa.Column("provider", sa.String(length=120), nullable=False),
        sa.Column("dataset_key", sa.String(length=160), nullable=False),
        sa.Column("source_entity_type", sa.String(length=80), nullable=False),
        sa.Column("source_entity_id", sa.String(length=200), nullable=False),
        sa.Column("raw_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider",
            "dataset_key",
            "source_entity_type",
            "source_entity_id",
            "raw_payload_hash",
            name="uq_weather_source_records_identity",
        ),
    )
    op.create_index(
        "ix_weather_source_records_dataset_fetched",
        "weather_source_records",
        ["dataset_key", "fetched_at"],
    )
    op.create_table(
        "weather_values",
        sa.Column("value_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "location_id",
            sa.String(length=120),
            sa.ForeignKey("weather_locations.location_id", ondelete="RESTRICT"),
            nullable=False,
        ),
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
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source_record_key",
            sa.String(length=255),
            sa.ForeignKey("weather_source_records.source_record_key", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("normalization_version", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "location_id",
            "provider",
            "dataset_key",
            "weather_domain",
            "forecast_style",
            "metric_key",
            "target_at",
            "source_record_key",
            name="uq_weather_values_identity",
        ),
        sa.CheckConstraint(
            "value_number IS NOT NULL OR value_text IS NOT NULL",
            name="ck_weather_values_has_value",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from",
            name="ck_weather_values_valid_window",
        ),
    )
    op.create_index(
        "ix_weather_values_location_time",
        "weather_values",
        ["location_id", "valid_at", "observed_at"],
    )
    op.create_index(
        "ix_weather_values_location_target_known",
        "weather_values",
        ["location_id", "target_at", "known_at"],
    )
    op.create_index(
        "ix_weather_values_dataset_metric", "weather_values", ["dataset_key", "metric_key"]
    )
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
    op.create_index(
        "uq_weather_sync_runs_active",
        "weather_sync_runs",
        ["provider", "dataset_key"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_table(
        "weather_sync_run_sources",
        sa.Column(
            "run_id",
            sa.String(length=64),
            sa.ForeignKey("weather_sync_runs.run_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "source_record_key",
            sa.String(length=255),
            sa.ForeignKey("weather_source_records.source_record_key", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("kor-travel-weather schema는 PostgreSQL만 지원합니다.")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION weather_immutable_row() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION '% is immutable', TG_TABLE_NAME;
        END; $$;
        """
    )
    for table in ("weather_source_records", "weather_values"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION weather_immutable_row()"
        )


def downgrade() -> None:
    for table in ("weather_source_records", "weather_values"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS weather_immutable_row()")
    op.drop_table("weather_sync_run_sources")
    op.drop_index("uq_weather_sync_runs_active", table_name="weather_sync_runs")
    op.drop_index("ix_weather_sync_runs_started", table_name="weather_sync_runs")
    op.drop_table("weather_sync_runs")
    op.drop_index("ix_weather_values_dataset_metric", table_name="weather_values")
    op.drop_index("ix_weather_values_location_target_known", table_name="weather_values")
    op.drop_index("ix_weather_values_location_time", table_name="weather_values")
    op.drop_table("weather_values")
    op.drop_index("ix_weather_source_records_dataset_fetched", table_name="weather_source_records")
    op.drop_table("weather_source_records")
    op.drop_index("ix_weather_locations_coordinates", table_name="weather_locations")
    op.drop_index("ix_weather_locations_enabled_region", table_name="weather_locations")
    op.drop_table("weather_locations")
