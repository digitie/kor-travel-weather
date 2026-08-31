"""add encrypted provider credential overrides."""

import sqlalchemy as sa

from alembic import op

revision = "0004_provider_credentials"
down_revision = "0003_sync_run_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weather_provider_credentials",
        sa.Column("provider", sa.String(length=120), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("last4", sa.String(length=4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("weather_provider_credentials")
