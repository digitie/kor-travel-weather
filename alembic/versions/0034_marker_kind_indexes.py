"""제공처 미지정 종류 마커의 현재/예보 대표값 순서 인덱스."""
from contextlib import contextmanager

import sqlalchemy as sa

from alembic import op

revision = "0034_marker_kind_indexes"
down_revision = "0033_export_source_inventory"
branch_labels = None
depends_on = None

CURRENT = "ix_weather_current_values_marker_current"
FORECAST = "ix_weather_current_values_marker_forecast"
PROVIDER_CURRENT = "ix_weather_current_values_marker_provider_current"
PROVIDER_FORECAST = "ix_weather_current_values_marker_provider_forecast"
METRICS = "metric_key IN ('TEMP', 'T1H', 'TMP', 'WEATHER_CODE', 'SKY', 'PTY')"
INCLUDE = ["provider", "forecast_style", "weather_domain", "dataset_key"]


@contextmanager
def bounded_autocommit():
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        previous = dict(bind.execute(sa.text(
            "SELECT name, setting::bigint FROM pg_settings "
            "WHERE name IN ('statement_timeout', 'lock_timeout')"
        )).all())
        try:
            for name, maximum in (("statement_timeout", 180000), ("lock_timeout", 5000)):
                bounded = min(previous[name] or maximum, maximum)
                bind.execute(sa.text("SELECT set_config(:name,:value,false)"),
                             {"name": name, "value": f"{bounded}ms"})
            yield bind
        finally:
            for name, value in previous.items():
                bind.execute(sa.text("SELECT set_config(:name,:value,false)"),
                             {"name": name, "value": f"{value}ms"})


def upgrade():
    with bounded_autocommit() as bind:
        for name, direction, styles, provider in (
            (CURRENT, "DESC", "'observed', 'nowcast'", False),
            (FORECAST, "ASC", "'ultra_short', 'short', 'mid'", False),
            (PROVIDER_CURRENT, "DESC", "'observed', 'nowcast'", True),
            (PROVIDER_FORECAST, "ASC", "'ultra_short', 'short', 'mid'", True),
        ):
            # 실패한 concurrent build만 회수하고 유효한 기존 인덱스는 다시 만들지 않는다.
            invalid = bind.execute(sa.text(
                "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_index i ON i.indexrelid=c.oid "
                "WHERE n.nspname=current_schema() AND c.relname=:name AND NOT i.indisvalid"
            ), {"name": name}).scalar()
            if invalid:
                op.drop_index(name, table_name="weather_current_values",
                              postgresql_concurrently=True)
            op.create_index(
                name, "weather_current_values",
                [sa.text("location_id"), *([sa.text("provider")] if provider else []),
                 sa.text("metric_key"), sa.text(f"target_at {direction}"),
                 sa.text("known_at DESC NULLS LAST"), sa.text("source_record_key DESC NULLS LAST"),
                 sa.text("value_id DESC")],
                postgresql_where=sa.text(METRICS + f" AND forecast_style IN ({styles})"),
                postgresql_include=[key for key in INCLUDE if not provider or key != "provider"],
                postgresql_concurrently=True, if_not_exists=True,
            )


def downgrade():
    with bounded_autocommit():
        for name in (PROVIDER_FORECAST, PROVIDER_CURRENT, FORECAST, CURRENT):
            op.drop_index(name, table_name="weather_current_values",
                          postgresql_concurrently=True, if_exists=True)
