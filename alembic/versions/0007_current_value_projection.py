"""materialize the newest revision pointer for each logical weather point."""

import sqlalchemy as sa

from alembic import op

revision = "0007_current_value_projection"
down_revision = "0006_marker_lookup_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weather_current_values",
        sa.Column(
            "value_id",
            sa.String(length=64),
            sa.ForeignKey("weather_values.value_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
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
        sa.Column("metric_key", sa.String(length=80), nullable=False),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "location_id",
            "provider",
            "dataset_key",
            "weather_domain",
            "forecast_style",
            "metric_key",
            "target_at",
            name="uq_weather_current_values_logical_point",
        ),
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("kor-travel-weather schema는 PostgreSQL만 지원합니다.")

    # Backfill is intentionally done before the read indexes are built.  The
    # DISTINCT ON order is the same ordering used by the repository's revision
    # selector, so upgrading an existing append-only store does not change the
    # public latest/forecast projection.
    op.execute(
        sa.text(
            """
            INSERT INTO weather_current_values (
                value_id, location_id, provider, dataset_key, weather_domain,
                forecast_style, metric_key, target_at
            )
            SELECT DISTINCT ON (
                location_id, provider, dataset_key, weather_domain,
                forecast_style, metric_key, target_at
            )
                value_id, location_id, provider, dataset_key, weather_domain,
                forecast_style, metric_key, target_at
            FROM weather_values
            ORDER BY
                location_id, provider, dataset_key, weather_domain,
                forecast_style, metric_key, target_at,
                known_at DESC NULLS LAST,
                source_record_key DESC,
                value_id DESC
            """
        )
    )

    # The table is bounded by logical points, but can still be large for a
    # nationwide forecast catalog.  Build indexes concurrently so the API and
    # Dagster remain readable during a rolling migration.  This block must be
    # outside Alembic's transaction by PostgreSQL design.
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "ix_weather_current_values_location_target "
                "ON weather_current_values (location_id, target_at, value_id)"
            )
        )
        op.execute(
            sa.text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "ix_weather_current_values_location_style_target "
                "ON weather_current_values "
                "(location_id, forecast_style, target_at, value_id)"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                sa.text(
                    "DROP INDEX CONCURRENTLY IF EXISTS "
                    "ix_weather_current_values_location_style_target"
                )
            )
            op.execute(
                sa.text(
                    "DROP INDEX CONCURRENTLY IF EXISTS "
                    "ix_weather_current_values_location_target"
                )
            )
    else:
        op.drop_index(
            "ix_weather_current_values_location_style_target",
            table_name="weather_current_values",
        )
        op.drop_index(
            "ix_weather_current_values_location_target",
            table_name="weather_current_values",
        )
    op.drop_table("weather_current_values")
