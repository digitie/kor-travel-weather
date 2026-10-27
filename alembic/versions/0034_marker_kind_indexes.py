"""제공처 미지정 종류 마커의 현재/예보 대표값 순서 인덱스."""

from contextlib import contextmanager

import sqlalchemy as sa

from alembic import op

revision = "0034_marker_kind_indexes"
down_revision = "0033_export_source_inventory"
branch_labels = None
depends_on = None

CURRENT = "ix_weather_current_values_marker_current"
FORECAST = "ix_weather_current_values_marker_forecast"
PROVIDER_CURRENT = "ix_weather_current_values_marker_provider_current"
PROVIDER_FORECAST = "ix_weather_current_values_marker_provider_forecast"
METRICS = "metric_key IN ('TEMP', 'T1H', 'TMP', 'WEATHER_CODE', 'SKY', 'PTY')"
INCLUDE = ["provider", "forecast_style", "weather_domain", "dataset_key"]


@contextmanager
def bounded_autocommit():
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        previous = dict(
            bind.execute(
                sa.text(
                    "SELECT name, setting::bigint FROM pg_settings "
                    "WHERE name IN ('statement_timeout', 'lock_timeout')"
                )
            ).all()
        )
        try:
            for name, maximum in (("statement_timeout", 180000), ("lock_timeout", 5000)):
                bounded = min(previous[name] or maximum, maximum)
                bind.execute(
                    sa.text("SELECT set_config(:name,:value,false)"),
                    {"name": name, "value": f"{bounded}ms"},
                )
            yield bind
        finally:
            for name, value in previous.items():
                bind.execute(
                    sa.text("SELECT set_config(:name,:value,false)"),
                    {"name": name, "value": f"{value}ms"},
                )


def specifications():
    return (
        (CURRENT, "DESC", ("observed", "nowcast"), False),
        (FORECAST, "ASC", ("ultra_short", "short", "mid"), False),
        (PROVIDER_CURRENT, "DESC", ("observed", "nowcast"), True),
        (PROVIDER_FORECAST, "ASC", ("ultra_short", "short", "mid"), True),
    )


def target_table(bind):
    # search_path의 첫 schema와 실제 table 소유 schema는 다를 수 있다.
    row = (
        bind.execute(
            sa.text(
                "SELECT c.oid,n.nspname AS schema_name FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.oid=to_regclass('weather_current_values') AND c.relkind='r'"
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise RuntimeError("weather_current_values의 실제 table을 확인할 수 없습니다.")
    return row


def expected_predicates(styles):
    # PostgreSQL 16의 원래 IN과 pg_get_indexdef 재생성에서 생기는 두 정확한
    # binary-coercion 형식만 허용한다. 임의 SQL 정규화/의미 추측은 하지 않는다.
    metrics = ("TEMP", "T1H", "TMP", "WEATHER_CODE", "SKY", "PTY")

    def array(items, individual):
        if individual:
            return (
                "ARRAY[" + ", ".join(f"('{item}'::character varying)::text" for item in items) + "]"
            )
        return (
            "(ARRAY[" + ", ".join(f"'{item}'::character varying" for item in items) + "])::text[]"
        )

    return {
        f"(((metric_key)::text = ANY ({array(metrics, individual)})) AND "
        f"((forecast_style)::text = ANY ({array(styles, individual)})))"
        for individual in (False, True)
    }


def checked_index(bind, table, specification, *, required=False):
    name, direction, styles, provider = specification
    row = (
        bind.execute(
            sa.text("""
        SELECT c.oid,c.relkind,c.reloptions,c.reltablespace,a.amname,
               i.indrelid,i.indisvalid,i.indisready,i.indislive,i.indisunique,i.indisexclusion,
               i.indnkeyatts,i.indexprs::text,pg_get_expr(i.indpred,i.indrelid) AS predicate,
               i.indoption::smallint[] AS options,
               ARRAY(SELECT pg_get_indexdef(c.oid,k,false)
                     FROM generate_series(1,i.indnatts) k) AS columns,
               ARRAY(SELECT o.opcname||':'||n.nspname
                     FROM unnest(i.indclass) WITH ORDINALITY k(opclass,position)
                     JOIN pg_opclass o ON o.oid=k.opclass
                     JOIN pg_namespace n ON n.oid=o.opcnamespace
                     ORDER BY k.position) AS opclasses,
               (SELECT bool_and(k.collation_oid=at.attcollation)
                FROM unnest(i.indkey::smallint[],i.indcollation::oid[])
                     WITH ORDINALITY k(attnum,collation_oid,position)
                JOIN pg_attribute at ON at.attrelid=i.indrelid AND at.attnum=k.attnum
                WHERE k.position<=i.indnkeyatts) AS default_collations
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_index i ON i.indexrelid=c.oid
        LEFT JOIN pg_am a ON a.oid=c.relam
        WHERE n.nspname=:schema AND c.relname=:name
    """),
            {"schema": table["schema_name"], "name": name},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        if required:
            raise RuntimeError(f"생성한 인덱스를 확인할 수 없습니다: {name}")
        return None
    keys = [
        "location_id",
        *(["provider"] if provider else []),
        "metric_key",
        "target_at",
        "known_at",
        "source_record_key",
        "value_id",
    ]
    include = [key for key in INCLUDE if not provider or key != "provider"]
    options = [0] * (3 if provider else 2) + [3 if direction == "DESC" else 0, 1, 1, 3]
    opclasses = [
        ("timestamptz_ops" if key in {"target_at", "known_at"} else "text_ops") + ":pg_catalog"
        for key in keys
    ]
    matches = (
        row["indrelid"] == table["oid"]
        and row["relkind"] == "i"
        and row["amname"] == "btree"
        and row["indexprs"] is None
        and row["columns"] == keys + include
        and row["options"] == options
        and row["indnkeyatts"] == len(keys)
        and row["predicate"] in expected_predicates(styles)
        and row["opclasses"] == opclasses
        and row["default_collations"] is True
        and not row["indisunique"]
        and not row["indisexclusion"]
        and row["indislive"]
        and row["reloptions"] is None
        and row["reltablespace"] == 0
    )
    if not matches:
        raise RuntimeError(f"인덱스 소유 table 또는 정확한 정의가 충돌합니다: {name}")
    if bind.execute(
        sa.text("SELECT 1 FROM pg_stat_progress_create_index WHERE index_relid=:oid"),
        {"oid": row["oid"]},
    ).scalar():
        raise RuntimeError(f"인덱스의 다른 빌드가 아직 진행 중입니다: {name}")
    if (required or row["indisvalid"]) and not (row["indisvalid"] and row["indisready"]):
        raise RuntimeError(f"인덱스가 valid/ready 상태가 아닙니다: {name}")
    return row


def upgrade():
    with bounded_autocommit() as bind:
        table = target_table(bind)
        # 4이름을 모두 검산한 다음에만 이번 migration 소유 DDL을 시작한다.
        for specification in specifications():
            checked_index(bind, table, specification)
        for specification in specifications():
            name, direction, styles, provider = specification
            existing = checked_index(bind, table, specification)
            if existing is not None and not existing["indisvalid"]:
                op.drop_index(
                    name,
                    table_name="weather_current_values",
                    schema=table["schema_name"],
                    postgresql_concurrently=True,
                )
            op.create_index(
                name,
                "weather_current_values",
                [
                    sa.text("location_id"),
                    *([sa.text("provider")] if provider else []),
                    sa.text("metric_key"),
                    sa.text(f"target_at {direction}"),
                    sa.text("known_at DESC NULLS LAST"),
                    sa.text("source_record_key DESC NULLS LAST"),
                    sa.text("value_id DESC"),
                ],
                schema=table["schema_name"],
                postgresql_where=sa.text(
                    METRICS + " AND forecast_style IN (" + ", ".join(repr(s) for s in styles) + ")"
                ),
                postgresql_include=[key for key in INCLUDE if not provider or key != "provider"],
                postgresql_concurrently=True,
                if_not_exists=True,
            )
            checked_index(bind, table, specification, required=True)


def downgrade():
    with bounded_autocommit() as bind:
        table = target_table(bind)
        for specification in specifications():
            checked_index(bind, table, specification)
        for specification in reversed(specifications()):
            name = specification[0]
            checked_index(bind, table, specification)
            op.drop_index(
                name,
                table_name="weather_current_values",
                schema=table["schema_name"],
                postgresql_concurrently=True,
                if_exists=True,
            )
