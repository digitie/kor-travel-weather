"""저배율 대표 위치의 재생성 가능한 읽기 모델을 추가한다."""

import sqlalchemy as sa

from alembic import op

revision = "0040_region_locations"
down_revision = "0039_page_liveness"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "weather_region_locations",
        sa.Column("level", sa.String(8), primary_key=True),
        sa.Column("region_key", sa.Text(), primary_key=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("sido_name", sa.Text(), nullable=False),
        sa.Column("sigungu_name", sa.Text()),
        sa.Column(
            "location_id",
            sa.String(120),
            sa.ForeignKey("weather_locations.location_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("location_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coordinate_key", sa.Text(), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("level IN ('sido', 'sigungu')", name="ck_weather_region_level"),
        sa.CheckConstraint("member_count > 0", name="ck_weather_region_members"),
    )
    op.create_table(
        "weather_region_projection_states",
        sa.Column("projection_key", sa.String(32), primary_key=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("row_count >= 0", name="ck_weather_region_projection_count"),
    )


def downgrade():
    # 원본·영구 기상/유가 통계가 아닌 대표 위치 참조 캐시만 제거한다.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_table("weather_region_projection_states")
    op.drop_table("weather_region_locations")
