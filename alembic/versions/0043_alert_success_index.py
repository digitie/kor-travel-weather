"""특보 최신 성공 run의 정렬 전량 탐색을 partial composite index로 제거한다."""

from contextlib import contextmanager

import sqlalchemy as sa

from alembic import op

revision = "0043_alert_success_index"
down_revision = "0042_export_database_artifacts"
branch_labels = None
depends_on = None

INDEX = "ix_weather_sync_runs_success_finished"
TABLE = "weather_sync_runs"


@contextmanager
def bounded_autocommit():
    # 기존 concurrent-index migration과 같이 더 엄격한 caller 한도는 유지한다.
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        previous = dict(
            bind.execute(
                sa.text(
                    "SELECT name, setting::bigint FROM pg_settings "
                    "WHERE name IN ('statement_timeout','lock_timeout')"
                )
            ).all()
        )
        try:
            for name, maximum in (("statement_timeout", 180000), ("lock_timeout", 5000)):
                value = min(previous[name] or maximum, maximum)
                bind.execute(
                    sa.text("SELECT set_config(:name,:value,false)"),
                    {"name": name, "value": f"{value}ms"},
                )
            yield bind
        finally:
            for name, value in previous.items():
                bind.execute(
                    sa.text("SELECT set_config(:name,:value,false)"),
                    {"name": name, "value": f"{value}ms"},
                )


def existing_index(bind):
    row = (
        bind.execute(
            sa.text("""
        SELECT i.indrelid=to_regclass(:table) AS own_table,
               a.amname,i.indisvalid,i.indisready,i.indisunique,i.indnkeyatts,i.indnatts,
               i.indoption::smallint[] AS options,
               pg_get_expr(i.indpred,i.indrelid) AS predicate,i.indexprs IS NULL AS no_expression,
               ARRAY(SELECT pg_get_indexdef(c.oid,k,false)
                     FROM generate_series(1,i.indnkeyatts) k) AS keys,
               EXISTS(SELECT 1 FROM pg_stat_progress_create_index p
                      WHERE p.index_relid=c.oid) AS building
        FROM pg_class c JOIN pg_index i ON i.indexrelid=c.oid
        JOIN pg_am a ON a.oid=c.relam
        WHERE c.relname=:name AND c.relnamespace=(
            SELECT relnamespace FROM pg_class WHERE oid=to_regclass(:table)
        )
    """),
            {"table": TABLE, "name": INDEX},
        )
        .mappings()
        .one_or_none()
    )
    if row is not None and not (
        row["own_table"]
        and row["amname"] == "btree"
        and not row["indisunique"]
        and row["indnkeyatts"] == 3
        and row["indnatts"] == 3
        and row["predicate"] == "((status)::text = 'success'::text)"
        and row["no_expression"]
        and row["keys"] == ["provider", "dataset_key", "finished_at"]
        and row["options"] == [0, 0, 3]
    ):
        raise RuntimeError("alert success index 이름 또는 정의가 충돌합니다.")
    if row is not None and row["building"]:
        raise RuntimeError("alert success index 빌드가 진행 중입니다.")
    return row


def upgrade():
    with bounded_autocommit() as bind:
        row = existing_index(bind)
        if row is not None and row["indisvalid"] and row["indisready"]:
            return
        if row is not None:
            # 취소된 이 revision 소유 INVALID 빌드만 지우고 재시도한다.
            op.drop_index(INDEX, table_name=TABLE, postgresql_concurrently=True)
        op.create_index(
            INDEX, TABLE, ["provider", "dataset_key", sa.text("finished_at DESC")],
            postgresql_where=sa.text("status = 'success'"), postgresql_concurrently=True,
        )
        row = existing_index(bind)
        if row is None or not (row["indisvalid"] and row["indisready"]):
            raise RuntimeError("alert success index가 valid/ready 상태가 아닙니다.")


def downgrade():
    with bounded_autocommit() as bind:
        if existing_index(bind) is not None:
            op.drop_index(INDEX, table_name=TABLE, postgresql_concurrently=True)
