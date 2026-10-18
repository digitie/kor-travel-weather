"""좌표 기반 주소 갱신의 파생 결과와 중단 복구 커서."""

import sqlalchemy as sa

from alembic import op

revision = "0021_reverse_addresses"
down_revision = "0020_short_forecast_retention"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reverse_address_entries",
        sa.Column("provider_id", sa.String(64), primary_key=True),
        sa.Column("target_key", sa.String(240), primary_key=True),
        sa.Column("coordinate_key", sa.String(64), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("refresh_id", sa.String(64)),
        sa.Column("payload", sa.JSON()),
        sa.Column("result_hash", sa.String(64)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("next_refresh_at", sa.DateTime(timezone=True)),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(80)),
    )
    for column in ("coordinate_key", "next_retry_at", "next_refresh_at"):
        op.create_index("ix_reverse_address_entries_" + column, "reverse_address_entries", [column])
    op.create_table(
        "reverse_address_control",
        sa.Column("provider_id", sa.String(64), primary_key=True),
        sa.Column("refresh_id", sa.String(64)),
        sa.Column("cursor", sa.String(240), nullable=False),
        sa.Column("force_refresh", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("owner", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("reverse_address_control")
    op.drop_table("reverse_address_entries")
