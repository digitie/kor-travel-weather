"""제공처별 지도 metric의 대표값 조회용 부분 인덱스.

동시 빌드가 실패해 INVALID로 남은 같은 이름의 인덱스만 CONCURRENTLY 지우고 다시 만든다.
그대로 두면 재실행이 "already exists"로 막히고, IF NOT EXISTS만 쓰면 INVALID 인덱스를
영원히 건너뛴다. 0013과 같이 indisvalid만 보며 이미 유효한 인덱스는 그대로 둔다.
"""

from contextlib import contextmanager

import sqlalchemy as sa

from alembic import op

revision = "0022_provider_marker_index"
down_revision = "0021_reverse_addresses"
branch_labels = None
depends_on = None

INDEX = "ix_weather_current_values_provider_marker"
# 2026-10-22 운영 복구가 실제로 필요로 한 대기·실행 상한. 더 엄격한 호출자 설정은 유지한다.
LIMITS = (("statement_timeout", 900000), ("lock_timeout", 30000))


def _create(**options) -> None:
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
        **options,
    )


@contextmanager
def bounded_autocommit():
    # 수집 upsert를 막는 테이블 잠금을 피하는 CONCURRENTLY는 transaction 밖에서 실행한다.
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        previous = dict(bind.execute(sa.text(
            "SELECT name, setting::bigint FROM pg_settings "
            "WHERE name IN ('statement_timeout', 'lock_timeout')"
        )).all())
        try:
            for name, maximum in LIMITS:
                limit = min(previous[name] or maximum, maximum)
                bind.execute(sa.text("SELECT set_config(:name,:value,false)"),
                             {"name": name, "value": f"{limit}ms"})
            yield bind
        finally:
            for name, value in previous.items():
                bind.execute(sa.text("SELECT set_config(:name,:value,false)"),
                             {"name": name, "value": f"{value}ms"})


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        with op.get_context().autocommit_block():
            _create()
        return
    with bounded_autocommit() as bind:
        if bind.execute(sa.text(
            "SELECT 1 FROM pg_index i WHERE i.indexrelid = to_regclass(:name) "
            "AND NOT i.indisvalid"
        ), {"name": INDEX}).scalar():
            op.drop_index(INDEX, table_name="weather_current_values", postgresql_concurrently=True)
        _create(if_not_exists=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(INDEX, table_name="weather_current_values", postgresql_concurrently=True)
