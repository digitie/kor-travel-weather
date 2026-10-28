"""일간 요약의 부분 RN1 강수 합계·무강수 판정을 미확정(null)으로 바로잡는다.

``summarize()``는 이제 구간 6시간 모두의 RN1 winner가 있을 때만 ``precip_sum_mm``과
``precip_occurred=false``를 확정한다. 저장된 요약은 그 위치·구간에 새 관측이 와야 다시
계산되므로 남아 있는 행(최대 31일)을 한 번 고친다. 시간별 winner(hourly_winners)와
``precip_occurred=true``(실제 강수·'1mm 미만' 증거)는 그대로 둔다.

날짜 인덱스로 하루씩 나눠 autocommit으로 **하루마다 commit**한다. 그래야 고친 행의 잠금을
migration 끝까지 쥐지 않고, 동시에 도는 publisher의 일별 upsert를 오래 막지 않는다. 각 UPDATE의
statement/lock 대기는 묶는다. 중간에 실패해도 이미 고친 날은 남고, 같은 조건만 고치므로 다시
실행하면 남은 날만 고친다.

배포 순서: 이 보정은 schema를 바꾸지 않으므로 옛 publisher가 migration 뒤에 닫힌 구간을 부분
합으로 다시 쓰지 않게 하려면 publisher sensor를 멈춘 채 migrate→이미지 교체를 하거나, 새 이미지로
바꾼 뒤 migrate한다.

되돌리기는 하지 않는다. 부분 합을 되살리면 다시 틀린 값이 된다.
"""

from contextlib import contextmanager

import sqlalchemy as sa

from alembic import op

revision = "0035_daily_precip_coverage"
down_revision = "0034_marker_kind_indexes"
branch_labels = None
depends_on = None

SLOT_HOURS = 6

PARTIAL_PRECIP_FIX = """
UPDATE weather_daily_summaries AS s
SET summary = (
    s.summary::jsonb
    || jsonb_build_object('precip_sum_mm', NULL::numeric)
    || CASE WHEN s.summary::jsonb -> 'precip_occurred' = 'true'::jsonb
            THEN '{}'::jsonb
            ELSE jsonb_build_object('precip_occurred', NULL::boolean) END
)::json
WHERE s.date = :day
  AND (
      CASE WHEN json_typeof(s.hourly_winners) = 'object' THEN (
          SELECT count(*) FROM json_each(s.hourly_winners) AS bucket(hour_key, cell)
          WHERE json_typeof(bucket.cell) = 'object' AND bucket.cell::jsonb ? 'precip'
      ) ELSE 0 END
  ) < :slot_hours
  AND (
      s.summary::jsonb -> 'precip_sum_mm' <> 'null'::jsonb
      OR s.summary::jsonb -> 'precip_occurred' = 'false'::jsonb
  )
"""


@contextmanager
def bounded(connection):
    """세션 단위로 statement/lock 대기를 묶고 끝나면 원래 값으로 되돌린다(autocommit 안)."""
    previous = dict(connection.execute(sa.text(
        "SELECT name, setting::bigint FROM pg_settings "
        "WHERE name IN ('statement_timeout', 'lock_timeout')"
    )).all())
    try:
        for name, maximum in (("statement_timeout", 60000), ("lock_timeout", 5000)):
            limit = min(previous[name] or maximum, maximum)
            connection.execute(sa.text("SELECT set_config(:name, :value, false)"),
                               {"name": name, "value": f"{limit}ms"})
        yield connection
    finally:
        for name, value in previous.items():
            connection.execute(sa.text("SELECT set_config(:name, :value, false)"),
                               {"name": name, "value": f"{value}ms"})


def upgrade():
    with op.get_context().autocommit_block(), bounded(op.get_bind()) as connection:
        days = connection.execute(sa.text(
            "SELECT DISTINCT date FROM weather_daily_summaries ORDER BY date"
        )).scalars().all()
        for day in days:
            # autocommit이라 각 UPDATE가 자기 transaction이다(하루 단위 commit).
            connection.execute(
                sa.text(PARTIAL_PRECIP_FIX), {"day": day, "slot_hours": SLOT_HOURS},
            )


def downgrade():
    # 데이터 보정만 한다. schema 변경이 없고 부분 합을 복원하지 않는다.
    pass
