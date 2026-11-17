"""GC receipt 후보를 digest로 찾는 digest index를 동시 빌드한다."""

from contextlib import contextmanager

import sqlalchemy as sa

from alembic import op

revision = "0041_export_receipt_gc_index"
down_revision = "0040_region_locations"
branch_labels = None
depends_on = None

INDEX = "ix_weather_export_chunks_artifact_digest"
TABLE = "weather_export_chunks"


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
               i.indpred IS NULL AS no_predicate,i.indexprs IS NULL AS no_expression,
               pg_get_indexdef(c.oid,1,false) AS key,
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
        and row["indnkeyatts"] == 1
        and row["indnatts"] == 1
        and row["no_predicate"]
        and row["no_expression"]
        and row["key"] == "artifact_digest"
    ):
        raise RuntimeError("receipt GC index 이름 또는 정의가 충돌합니다.")
    if row is not None and row["building"]:
        raise RuntimeError("receipt GC index 빌드가 진행 중입니다.")
    return row


def upgrade():
    with bounded_autocommit() as bind:
        row = existing_index(bind)
        if row is not None and row["indisvalid"] and row["indisready"]:
            return
        if row is not None:
            # 취소된 이 revision 소유 INVALID 빌드만 지우고 재시도한다.
            op.drop_index(INDEX, table_name=TABLE, postgresql_concurrently=True)
        op.create_index(INDEX, TABLE, ["artifact_digest"], postgresql_concurrently=True)
        row = existing_index(bind)
        if row is None or not (row["indisvalid"] and row["indisready"]):
            raise RuntimeError("receipt GC index가 valid/ready 상태가 아닙니다.")


def downgrade():
    with bounded_autocommit() as bind:
        if existing_index(bind) is not None:
            op.drop_index(INDEX, table_name=TABLE, postgresql_concurrently=True)
