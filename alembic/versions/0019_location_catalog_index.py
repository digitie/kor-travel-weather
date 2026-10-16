"""공개 위치 목록의 정렬을 covering index로 처리한다."""

import sqlalchemy as sa

from alembic import op

revision = "0019_location_catalog_index"
down_revision = "0018_sync_run_owner"
branch_labels = None
depends_on = None

INDEX = "ix_weather_locations_enabled_name_id"


def upgrade() -> None:
    # 수집 중 위치 갱신을 긴 CREATE INDEX 잠금으로 막지 않는다.
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX,
            "weather_locations",
            ["name", "location_id"],
            postgresql_where=sa.text("enabled IS TRUE"),
            postgresql_include=["latitude", "longitude", "nx", "ny", "region_code", "enabled"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(INDEX, table_name="weather_locations", postgresql_concurrently=True)
