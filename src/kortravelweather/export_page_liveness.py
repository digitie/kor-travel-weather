"""완료 페이지가 참조한 plan/generation/head 삭제를 같은 MVCC에서 확인한다.

일반 publication INSERT/UPDATE에는 쓰지 않는다. DELETE 0행도 statement epoch를
증가시키는 보수적 무효화다. reader는 자신이 소유한 RR transaction에서 호출한다.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Connection,
    MetaData,
    Table,
    Text,
    event,
    text,
)
from sqlalchemy.exc import SQLAlchemyError

from .export_artifacts import ExportArtifactError
from .export_status import status_transaction

TARGETS = ("weather_export_scope_plans", "weather_export_generations", "weather_export_heads")
TABLE_NAME = "weather_page_liveness_epochs"
FUNCTION_NAME = "weather_page_liveness_bump"
TRIGGER_NAME = "weather_page_liveness_changed"
_TARGET_CHECK = "target_name IN (" + ", ".join(repr(name) for name in TARGETS) + ")"


def _qualified(connection: Connection, schema: str, name: str) -> str:
    quote = connection.dialect.identifier_preparer.quote
    return f"{quote(schema)}.{quote(name)}"


def _relation(connection: Connection, name: str):
    return connection.execute(text(
        "SELECT c.oid,n.nspname AS schema,c.relname,c.relowner,r.rolname AS owner,"
        "c.relkind,c.relpersistence,c.relispartition,c.relrowsecurity,c.relforcerowsecurity "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_roles r ON r.oid=c.relowner WHERE c.oid=to_regclass(:name)"
    ), {"name": name}).mappings().one_or_none()


def _targets(connection: Connection, tables: Sequence[Table] | None = None):
    names = TARGETS if tables is None else tuple(
        connection.dialect.identifier_preparer.format_table(table) for table in tables
    )
    rows = [_relation(connection, name) for name in names]
    if any(row is None or row["relkind"] != "r" or row["relpersistence"] != "p"
           or row["relispartition"] for row in rows):
        raise RuntimeError("page liveness 대상은 기존 영구 일반 테이블이어야 합니다.")
    if tuple(row["relname"] for row in rows) != TARGETS or len({
        (row["schema"], row["relowner"]) for row in rows
    }) != 1:
        raise RuntimeError("page liveness 대상의 스키마·소유자가 다릅니다.")
    return rows


def _body(connection: Connection, schema: str) -> str:
    counter = _qualified(connection, schema, TABLE_NAME)
    literal = "'" + schema.replace("'", "''") + "'"
    return f"""BEGIN
    IF TG_TABLE_SCHEMA <> {literal} OR TG_TABLE_NAME NOT IN ({', '.join(repr(n) for n in TARGETS)})
       OR TG_WHEN <> 'AFTER' OR TG_LEVEL <> 'STATEMENT'
       OR TG_OP NOT IN ('DELETE', 'TRUNCATE') OR TG_NARGS <> 0 THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'page liveness trigger context mismatch';
    END IF;
    UPDATE {counter} SET epoch = epoch + 1 WHERE target_name = TG_TABLE_NAME;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'page liveness epoch missing';
    END IF;
    RETURN NULL;
END;"""


def _function(connection: Connection, schema: str):
    return connection.execute(text(
        "SELECT p.oid,p.proowner,p.prosecdef,p.proconfig,p.prosrc,p.prokind,p.provolatile,"
        "p.proparallel,p.proisstrict,p.proretset,p.proleakproof,"
        "p.prorettype='trigger'::regtype AS is_trigger,l.lanname,"
        "NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) "
        "a WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS no_public_execute "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname=:schema AND p.proname=:name AND p.pronargs=0"
    ), {"schema": schema, "name": FUNCTION_NAME}).mappings().one_or_none()


def _counter(connection: Connection, schema: str, owner: int):
    row = _relation(connection, _qualified(connection, schema, TABLE_NAME))
    if row is None or row["relkind"] != "r" or row["relpersistence"] != "p" or (
        row["relowner"] != owner or row["relispartition"] or row["relrowsecurity"]
        or row["relforcerowsecurity"]
    ):
        raise RuntimeError("page liveness counter의 종류·소유자가 다릅니다.")
    columns = connection.execute(text(
        "SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,"
        "a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid) "
        "FROM pg_attribute a LEFT JOIN pg_attrdef d "
        "ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
        "WHERE a.attrelid=:oid AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum"
    ), {"oid": row["oid"]}).all()
    constraints = connection.execute(text(
        # PG18은 NOT NULL도 별도 'n'으로 등록한다. 양 버전 모두 위 attnotnull로 검산한다.
        "SELECT contype,pg_get_constraintdef(oid,false) FROM pg_constraint "
        "WHERE conrelid=:oid AND contype<>'n'"
    ), {"oid": row["oid"]}).all()
    target_expression = "CHECK ((target_name = ANY (ARRAY[" + ", ".join(
        repr(name) + "::text" for name in TARGETS
    ) + "])))"
    if columns != [("target_name", "text", True, "", "", None),
                   ("epoch", "bigint", True, "", "", "0")] or set(constraints) != {
        ("p", "PRIMARY KEY (target_name)"), ("c", "CHECK ((epoch >= 0))"),
        ("c", target_expression),
    }:
        raise RuntimeError("page liveness counter의 열·제약이 다릅니다.")
    return row


def _verify(connection: Connection, targets) -> None:
    schema, owner = targets[0]["schema"], targets[0]["relowner"]
    _counter(connection, schema, owner)
    fn = _function(connection, schema)
    if fn is None or not (
        fn["proowner"] == owner and fn["prosecdef"] and fn["is_trigger"]
        and fn["proconfig"] == ["search_path=pg_catalog"]
        and fn["prosrc"].strip() == _body(connection, schema).strip()
        and fn["prokind"] == "f" and fn["provolatile"] == "v" and fn["proparallel"] == "u"
        and not fn["proisstrict"] and not fn["proretset"] and not fn["proleakproof"]
        and fn["lanname"] == "plpgsql" and fn["no_public_execute"]
    ):
        raise RuntimeError("page liveness 함수의 본문·소유자·search_path가 다릅니다.")
    for target in targets:
        triggers = connection.execute(text(
            "SELECT tgfoid,tgtype::int,tgenabled,tgisinternal,tgnargs,tgqual,"
            "tgoldtable,tgnewtable,tgconstraint FROM pg_trigger "
            "WHERE tgrelid=:oid AND tgname=:name"
        ), {"oid": target["oid"], "name": TRIGGER_NAME}).all()
        if triggers != [(fn["oid"], 40, "O", False, 0, None, None, None, 0)]:
            raise RuntimeError("page liveness statement trigger의 정의가 다릅니다.")
    counter = _qualified(connection, schema, TABLE_NAME)
    rows = connection.exec_driver_sql(f"SELECT target_name,epoch FROM {counter}").all()
    if len(rows) != 3 or set(dict(rows)) != set(TARGETS) or any(
        type(value) is not int or value < 0 for _, value in rows
    ):
        raise RuntimeError("page liveness counter 3행이 완결되지 않았습니다.")


def register_page_liveness(metadata: MetaData, targets: Sequence[Table]) -> Table:
    """metadata.create_all에서도 3대상 뒤 counter와 PG trigger를 설치한다(FK 없음)."""
    if tuple(table.name for table in targets) != TARGETS:
        raise ValueError("page liveness 대상 순서가 다릅니다.")
    schema = targets[0].schema
    key = f"{schema}.{TABLE_NAME}" if schema else TABLE_NAME
    if key in metadata.tables:
        return metadata.tables[key]
    table = Table(TABLE_NAME, metadata,
                  Column("target_name", Text, primary_key=True),
                  Column("epoch", BigInteger, nullable=False, server_default=text("0")),
                  CheckConstraint(_TARGET_CHECK, name="ck_weather_page_liveness_target"),
                  CheckConstraint("epoch >= 0", name="ck_weather_page_liveness_epoch"),
                  schema=schema)
    for target in targets:
        table.add_is_dependent_on(target)

    def created(table, connection, **kwargs):
        if connection.dialect.name != "postgresql":
            return
        with status_transaction(connection):
            actual = _targets(connection, targets)
            schema, owner = actual[0]["schema"], actual[0]["owner"]
            table.info["page_liveness_schema"] = schema
            counter, function = (_qualified(connection, schema, name)
                                 for name in (TABLE_NAME, FUNCTION_NAME))
            if _function(connection, schema) is not None or any(connection.execute(text(
                "SELECT 1 FROM pg_trigger WHERE tgrelid=:oid AND tgname=:name"
            ), {"oid": target["oid"], "name": TRIGGER_NAME}).first() for target in actual):
                raise RuntimeError("기존 page liveness 객체를 새 counter로 대체하지 않습니다.")
            quote = connection.dialect.identifier_preparer.quote
            connection.exec_driver_sql(f"ALTER TABLE {counter} OWNER TO {quote(owner)}")
            _counter(connection, schema, actual[0]["relowner"])
            connection.execute(table.insert(), [{"target_name": name, "epoch": 0}
                                                for name in TARGETS])
            connection.exec_driver_sql(
                f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql "
                f"SECURITY DEFINER SET search_path=pg_catalog AS $liveness$"
                + _body(connection, schema) + "$liveness$"
            )
            connection.exec_driver_sql(f"ALTER FUNCTION {function}() OWNER TO {quote(owner)}")
            connection.exec_driver_sql(f"REVOKE ALL ON FUNCTION {function}() FROM PUBLIC")
            for target in actual:
                target_name = _qualified(connection, schema, target["relname"])
                connection.exec_driver_sql(
                    f"CREATE TRIGGER {quote(TRIGGER_NAME)} AFTER DELETE OR TRUNCATE "
                    f"ON {target_name} FOR EACH STATEMENT EXECUTE FUNCTION {function}()"
                )
            _verify(connection, actual)

    def dropping(table, connection, **kwargs):
        if connection.dialect.name == "postgresql":
            with status_transaction(connection):
                _remove_objects(connection, _targets(connection, targets))

    event.listen(table, "after_create", created)
    event.listen(table, "before_drop", dropping)
    return table


def install_page_liveness(connection: Connection) -> None:
    """0039용: 기존 대상의 실제 schema/owner를 확인하고 새 파생 객체만 만든다."""
    if connection.dialect.name != "postgresql":
        return
    with status_transaction(connection):
        actual = _targets(connection)
        schema = actual[0]["schema"]
        if _relation(connection, _qualified(connection, schema, TABLE_NAME)) is not None:
            _verify(connection, actual)
            return
        metadata = MetaData()
        targets = [Table(name, metadata, schema=schema) for name in TARGETS]
        register_page_liveness(metadata, targets).create(connection)


def _remove_objects(connection: Connection, actual) -> None:
    _verify(connection, actual)
    schema = actual[0]["schema"]
    quote = connection.dialect.identifier_preparer.quote
    for target in actual:
        connection.exec_driver_sql(
            f"DROP TRIGGER {quote(TRIGGER_NAME)} ON "
            + _qualified(connection, schema, target["relname"])
        )
    connection.exec_driver_sql(f"DROP FUNCTION {_qualified(connection, schema, FUNCTION_NAME)}()")


def remove_page_liveness(connection: Connection) -> None:
    """0039 되돌림: 기존 plan/generation/head와 그 행은 전혀 변경하지 않는다."""
    if connection.dialect.name != "postgresql":
        return
    with status_transaction(connection):
        actual = _targets(connection)
        _remove_objects(connection, actual)
        connection.exec_driver_sql(
            f"DROP TABLE {_qualified(connection, actual[0]['schema'], TABLE_NAME)}"
        )


def capture_liveness(connection: Connection, metadata: MetaData) -> tuple[int, int, int] | None:
    """caller RR의 단일 조회에서 3 epoch와 대상·활성 trigger 존재를 함께 확인한다."""
    if connection.dialect.name != "postgresql":
        return None
    key = f"{metadata.schema}.{TABLE_NAME}" if metadata.schema else TABLE_NAME
    table = metadata.tables.get(key)
    if table is None:
        raise ExportArtifactError("완료 페이지 생존 검산 schema가 등록되지 않았습니다.")
    schema = (table.schema or table.info.get("page_liveness_schema")
              or connection.dialect.default_schema_name)
    counter = _qualified(connection, schema, TABLE_NAME)
    try:
        rows = connection.execute(text(
            f"SELECT e.target_name,e.epoch,EXISTS(SELECT 1 FROM pg_catalog.pg_trigger t "
            "WHERE t.tgrelid=pg_catalog.to_regclass("
            "pg_catalog.format('%I.%I',CAST(:schema AS text),e.target_name)) "
            "AND t.tgname=:trigger AND t.tgtype=40 AND t.tgenabled='O' "
            "AND NOT t.tgisinternal AND t.tgnargs=0 "
            "AND t.tgfoid=pg_catalog.to_regprocedure("
            "pg_catalog.format('%I.%I()',CAST(:schema AS text),CAST(:function AS text)))) "
            "AS intact "
            f"FROM {counter} e"
        ), {"schema": schema, "trigger": TRIGGER_NAME, "function": FUNCTION_NAME}).all()
    except SQLAlchemyError:
        raise ExportArtifactError("완료 페이지 생존 검산을 확인할 수 없습니다.") from None
    epochs = {name: epoch for name, epoch, _ in rows}
    if len(rows) != 3 or set(epochs) != set(TARGETS) or any(
        type(value) is not int or value < 0 for value in epochs.values()
    ) or any(intact is not True for _, _, intact in rows):
        raise ExportArtifactError("완료 페이지 생존 검산 3행이 완결되지 않았습니다.")
    return tuple(epochs[name] for name in TARGETS)
