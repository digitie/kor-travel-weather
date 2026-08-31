"""persist admin UI session revocations across web restarts."""

import sqlalchemy as sa

from alembic import op

revision = "0005_admin_session_revocations"
down_revision = "0004_provider_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weather_admin_session_revocations",
        sa.Column("session_digest", sa.String(length=64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_weather_admin_session_revocations_expires",
        "weather_admin_session_revocations",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_weather_admin_session_revocations_expires",
        table_name="weather_admin_session_revocations",
    )
    op.drop_table("weather_admin_session_revocations")
