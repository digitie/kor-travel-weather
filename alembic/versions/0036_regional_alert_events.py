"""기존 활성 카드와 분리한 공식 지역별 발표 관측."""

import sqlalchemy as sa

from alembic import op

revision = "0036_regional_alert_events"
down_revision = "0035_daily_precip_coverage"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "weather_alert_events",
        sa.Column("event_id", sa.String(68), primary_key=True),
        sa.Column("announcement_id", sa.String(68), nullable=False),
        sa.Column("area_code", sa.String(32), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event", sa.JSON(), nullable=False),
        sa.Column(
            "source_record_key",
            sa.String(255),
            sa.ForeignKey("weather_source_records.source_record_key", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_weather_alert_events_area_seen",
        "weather_alert_events",
        ["area_code", "first_seen_at", "event_id"],
    )
    op.create_index("ix_weather_alert_events_source", "weather_alert_events", ["source_record_key"])
    op.create_index(
        "ix_weather_alert_events_announcement",
        "weather_alert_events",
        ["announcement_id", "first_seen_at"],
    )
    op.execute(
        "CREATE TRIGGER weather_alert_events_immutable BEFORE UPDATE OR DELETE "
        "ON weather_alert_events FOR EACH ROW EXECUTE FUNCTION weather_immutable_row()"
    )


def downgrade():
    op.drop_table("weather_alert_events")
