"""persist shared admin login-failure buckets across web replicas."""

import sqlalchemy as sa

from alembic import op

revision = "0008_admin_login_rate_limits"
down_revision = "0007_current_value_projection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weather_admin_login_rate_limits",
        sa.Column("bucket_hash", sa.String(length=64), primary_key=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_weather_admin_login_rate_limits_updated",
        "weather_admin_login_rate_limits",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_weather_admin_login_rate_limits_updated",
        table_name="weather_admin_login_rate_limits",
    )
    op.drop_table("weather_admin_login_rate_limits")
