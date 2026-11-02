"""reference 최신 handle 조회에서 큰 manifest의 반복 해석을 제거한다."""

from contextlib import contextmanager

import sqlalchemy as sa

from alembic import op

revision = "0036_snapshot_kind_projection"
down_revision = "0035_daily_precip_coverage"
branch_labels = None
depends_on = None

INDEX = "ix_weather_export_snapshots_kind_issue"
EXPRESSION = "((manifest -> 'selection'::text) ->> 'kind'::text)"


@contextmanager
def bounded_connection():
    # generated column을 추가할 때 기존 행을 한 번 읽는다. 잠금 대기는 5초,
    # DDL은 180초로 제한하고 index는 별도 concurrent 문장으로 만든다.
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        previous = dict(bind.execute(sa.text(
            "SELECT name,setting::bigint FROM pg_settings "
            "WHERE name IN ('statement_timeout','lock_timeout')"
        )).all())
        try:
            for name, maximum in (("statement_timeout", 180000), ("lock_timeout", 5000)):
                bind.execute(sa.text("SELECT set_config(:name,:value,false)"), {
                    "name": name, "value": f"{min(previous[name] or maximum, maximum)}ms",
                })
            yield bind
        finally:
            for name, value in previous.items():
                bind.execute(sa.text("SELECT set_config(:name,:value,false)"), {
                    "name": name, "value": f"{value}ms",
                })


def target(bind):
    return bind.execute(sa.text(
        "SELECT c.oid,n.nspname AS schema FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.oid=to_regclass('weather_export_snapshots') AND c.relkind='r'"
    )).mappings().one()


def check_column(bind, table):
    row = bind.execute(sa.text(
        "SELECT a.attgenerated,a.atttypid::regtype::text AS type,"
        "pg_get_expr(d.adbin,d.adrelid) AS expression "
        "FROM pg_attribute a LEFT JOIN pg_attrdef d "
        "ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
        "WHERE a.attrelid=:table AND a.attname='selection_kind' AND NOT a.attisdropped"
    ), {"table": table["oid"]}).mappings().one_or_none()
    if row is not None and dict(row) != {
        "attgenerated": "s", "type": "character varying", "expression": EXPRESSION,
    }:
        raise RuntimeError("selection_kind의 기존 정의가 migration과 다릅니다.")
    return row


def check_index(bind, table):
    row = bind.execute(sa.text(
        "SELECT i.indrelid,i.indisvalid,i.indisready,i.indislive,i.indisunique,"
        "i.indisexclusion,i.indnkeyatts,i.indnatts,i.indpred IS NULL AS no_predicate,"
        "i.indexprs IS NULL AS no_expression,c.reloptions,c.reltablespace,a.amname,"
        "ARRAY(SELECT o.opcname||':'||ns.nspname "
        "FROM unnest(i.indclass) WITH ORDINALITY k(opclass,position) "
        "JOIN pg_opclass o ON o.oid=k.opclass JOIN pg_namespace ns ON ns.oid=o.opcnamespace "
        "ORDER BY k.position) AS opclasses,"
        "(SELECT bool_and(k.collation_oid=at.attcollation) "
        "FROM unnest(i.indkey::smallint[],i.indcollation::oid[]) k(attnum,collation_oid) "
        "JOIN pg_attribute at ON at.attrelid=i.indrelid AND at.attnum=k.attnum) "
        "AS default_collations,"
        "ARRAY(SELECT pg_get_indexdef(c.oid,k,false) FROM generate_series(1,i.indnatts) k) "
        "AS columns FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "LEFT JOIN pg_index i ON i.indexrelid=c.oid LEFT JOIN pg_am a ON a.oid=c.relam "
        "WHERE n.nspname=:schema AND c.relname=:name"
    ), {"schema": table["schema"], "name": INDEX}).mappings().one_or_none()
    if row is not None and not (
        row["indrelid"] == table["oid"] and row["amname"] == "btree"
        and row["columns"] == ["selection_kind", "issue_order"]
        and row["indnkeyatts"] == row["indnatts"] == 2
        and row["no_predicate"] and row["no_expression"]
        and row["indisvalid"] and row["indisready"] and row["indislive"]
        and not row["indisunique"] and not row["indisexclusion"]
        and row["reloptions"] is None and row["reltablespace"] == 0
        and row["opclasses"] == ["text_ops:pg_catalog", "int8_ops:pg_catalog"]
        and row["default_collations"] is True
        and bind.execute(sa.text(
            "SELECT indoption::smallint[] FROM pg_index "
            "WHERE indexrelid=to_regclass(:index)"
        ), {"index": bind.dialect.identifier_preparer.quote(table["schema"]) + "." + INDEX}
        ).scalar_one() == [0, 3]
    ):
        raise RuntimeError("snapshot 종류 인덱스의 기존 정의 또는 상태가 다릅니다.")
    return row


def upgrade():
    with bounded_connection() as bind:
        table = target(bind)
        # concurrent DDL 후 실패한 경우에도 이미 완성된 같은 column/index만 재사용한다.
        if check_column(bind, table) is None:
            op.add_column("weather_export_snapshots", sa.Column(
                "selection_kind", sa.String(), sa.Computed(
                    "(manifest -> 'selection') ->> 'kind'", persisted=True,
                ),
            ), schema=table["schema"])
        check_column(bind, table)
        if check_index(bind, table) is None:
            op.create_index(INDEX, "weather_export_snapshots", [
                "selection_kind", sa.text("issue_order DESC"),
            ], schema=table["schema"], postgresql_concurrently=True)
        check_index(bind, table)


def downgrade():
    with bounded_connection() as bind:
        table = target(bind)
        check_column(bind, table)
        if check_index(bind, table) is not None:
            op.drop_index(INDEX, table_name="weather_export_snapshots",
                          schema=table["schema"], postgresql_concurrently=True)
        op.drop_column("weather_export_snapshots", "selection_kind", schema=table["schema"])
