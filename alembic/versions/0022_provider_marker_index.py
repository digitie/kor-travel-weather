"""제공처별 지도 metric의 대표값 조회용 부분 인덱스."""

import sqlalchemy as sa

from alembic import op

revision = "0022_provider_marker_index"
down_revision = "0021_reverse_addresses"
branch_labels = None
depends_on = None

INDEX = "ix_weather_current_values_provider_marker"


def upgrade() -> None:
    # 수집 upsert를 막는 테이블 잠금을 피한다. 실패 시 남은 INVALID index는
    # 운영 절차에서 이름/정의를 확인한 뒤 CONCURRENTLY 제거하고 재시도한다.
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX, "weather_current_values",
            [sa.text("location_id"), sa.text("provider"), sa.text("metric_key"),
             sa.text("(CASE WHEN forecast_style IN ('observed', 'nowcast') THEN 0 ELSE 1 END)"),
             sa.text("known_at DESC NULLS LAST"), sa.text("source_record_key DESC NULLS LAST"),
             sa.text("value_id DESC")],
            postgresql_where=sa.text(
                "metric_key IN ('TEMP', 'T1H', 'TMP', 'WEATHER_CODE', 'SKY', 'PTY')",
            ),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(INDEX, table_name="weather_current_values", postgresql_concurrently=True)
