"""단기예보의 valid_at 기준 정리에 한정한 파티션 부분 인덱스."""

import sqlalchemy as sa

from alembic import op

revision = "0020_short_forecast_retention"
down_revision = "0019_location_catalog_index"
branch_labels = None
depends_on = None

INDEX = "ix_weather_values_short_forecast_retention"
# migration의 역사적 정의는 향후 런타임 정책 변경과 독립적으로 고정한다.
PREDICATE = (
    "provider = 'python-kma-api' AND dataset_key = 'kma_short_forecast' "
    "AND weather_domain = 'kma_short_forecast' AND forecast_style = 'short' "
    "AND valid_at IS NOT NULL"
)


def upgrade() -> None:
    # partitioned parent에는 CONCURRENTLY를 지원하지 않는다.
    # root의 잠금·디스크/WAL 예산을 확인한 유지보수 창에서만 실행한다.
    # 단일 transaction이므로 실패하면 index와 revision 모두 rollback된다.
    op.create_index(
        INDEX, "weather_values", ["valid_at", "known_at", "value_id"],
        postgresql_where=sa.text(PREDICATE),
    )


def downgrade() -> None:
    # 이 revision이 추가한 parent/상속 index만 제거한다.
    op.drop_index(INDEX, table_name="weather_values")
