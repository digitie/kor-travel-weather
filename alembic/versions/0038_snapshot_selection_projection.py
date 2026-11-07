"""큰 snapshot JSON을 WHERE에서 반복 해석하지 않도록 작은 선택 열을 저장한다."""

from contextlib import contextmanager

import sqlalchemy as sa

from alembic import op

revision = "0038_snapshot_selection"
down_revision = "0037_release_merge"
branch_labels = None
depends_on = None

COLUMNS = {
    "selection_signature": "((manifest -> 'selection'::text) ->> 'signature'::text)",
    "forecast_style": "((manifest -> 'selection'::text) ->> 'style'::text)",
    "forecast_since": "((manifest -> 'selection'::text) ->> 'since'::text)",
    "forecast_provider_signature": (
        "((manifest -> 'selection'::text) ->> 'provider_signature'::text)"
    ),
    "daily_resource": "(manifest ->> 'daily_resource'::text)",
}
INDEXES = {
    "ix_weather_export_snapshots_signature_issue": ["selection_signature", "issue_order"],
    "ix_weather_export_snapshots_forecast_issue": [
        "selection_kind", "forecast_style", "forecast_since", "forecast_provider_signature",
        "issue_order",
    ],
    "ix_weather_export_snapshots_daily_issue": ["daily_resource", "issue_order"],
}


@contextmanager
def bounded_connection():
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


def check_column(bind, table, name):
    row = bind.execute(sa.text(
        "SELECT a.attgenerated,a.atttypid::regtype::text AS type,"
        "pg_get_expr(d.adbin,d.adrelid) AS expression "
        "FROM pg_attribute a LEFT JOIN pg_attrdef d "
        "ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
        "WHERE a.attrelid=:table AND a.attname=:name AND NOT a.attisdropped"
    ), {"table": table["oid"], "name": name}).mappings().one_or_none()
    if row is not None and dict(row) != {
        "attgenerated": "s", "type": "character varying", "expression": COLUMNS[name],
    }:
        raise RuntimeError("snapshot 선택 열의 기존 정의가 migration과 다릅니다.")
    return row


def check_index(bind, table, name):
    row = bind.execute(sa.text(
        "SELECT i.indrelid,i.indisvalid,i.indisready,i.indislive,i.indisunique,"
        "i.indisexclusion,i.indnkeyatts,i.indnatts,i.indpred IS NULL AS no_predicate,"
        "i.indexprs IS NULL AS no_expression,c.reloptions,c.reltablespace,a.amname,"
        "i.indoption::smallint[] AS options,"
        "ARRAY(SELECT pg_get_indexdef(c.oid,k,false) FROM generate_series(1,i.indnatts) k) "
        "AS columns FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "LEFT JOIN pg_index i ON i.indexrelid=c.oid LEFT JOIN pg_am a ON a.oid=c.relam "
        "WHERE n.nspname=:schema AND c.relname=:name"
    ), {"schema": table["schema"], "name": name}).mappings().one_or_none()
    if row is not None and not (
        row["indrelid"] == table["oid"] and row["amname"] == "btree"
        and row["columns"] == INDEXES[name]
        and row["indnkeyatts"] == row["indnatts"] == len(INDEXES[name])
        and row["no_predicate"] and row["no_expression"]
        and row["indisvalid"] and row["indisready"] and row["indislive"]
        and not row["indisunique"] and not row["indisexclusion"]
        and row["reloptions"] is None and row["reltablespace"] == 0
        and row["options"] == [0] * (len(INDEXES[name]) - 1) + [3]
    ):
        raise RuntimeError("snapshot 선택 인덱스의 기존 정의 또는 상태가 다릅니다.")
    return row


def upgrade():
    with bounded_connection() as bind:
        table = target(bind)
        missing = [name for name in COLUMNS if check_column(bind, table, name) is None]
        if missing:
            quote = bind.dialect.identifier_preparer.quote
            # 한 ALTER로 기존 큰 manifest를 한 번만 backfill한다. 원문은 수정하지 않는다.
            clauses = [f"ADD COLUMN {quote(name)} VARCHAR GENERATED ALWAYS AS "
                       f"({COLUMNS[name]}) STORED" for name in missing]
            bind.exec_driver_sql(
                f"ALTER TABLE {quote(table['schema'])}.weather_export_snapshots "
                + ", ".join(clauses)
            )
        for name in COLUMNS:
            check_column(bind, table, name)
        for name, columns in INDEXES.items():
            if check_index(bind, table, name) is None:
                op.create_index(name, "weather_export_snapshots", [
                    *columns[:-1], sa.text("issue_order DESC"),
                ], schema=table["schema"], postgresql_concurrently=True)
            check_index(bind, table, name)


def downgrade():
    with bounded_connection() as bind:
        table = target(bind)
        for name in INDEXES:
            if check_index(bind, table, name) is not None:
                op.drop_index(name, table_name="weather_export_snapshots",
                              schema=table["schema"], postgresql_concurrently=True)
        for name in reversed(COLUMNS):
            if check_column(bind, table, name) is not None:
                op.drop_column("weather_export_snapshots", name, schema=table["schema"])
