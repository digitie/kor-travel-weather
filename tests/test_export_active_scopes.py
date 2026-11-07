"""소유권 묶음 조회는 원본 JSON/유실/실패 판정과 동일 MVCC를 보존한다."""

import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    select,
)

from kortravelweather.export_inventory_selection import _active_scope_states


@pytest.fixture
def scope_tables():
    url = os.environ.get("KOR_TRAVEL_WEATHER_TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("격리 PostgreSQL이 필요합니다.")
    engine = create_engine(url)
    metadata = MetaData()
    prefix = "active_scope_" + uuid.uuid4().hex
    definitions = {
        "weather_export_scope_owners": [
            Column("source_scope", String, primary_key=True),
            Column("active_attempt_id", String),
            Column("owner_epoch", BigInteger),
        ],
        "weather_export_attempts": [
            Column("attempt_id", String, primary_key=True),
            Column("run_id", String),
            Column("status", String),
            Column("owner_epochs", JSON),
        ],
        "weather_sync_runs": [Column("run_id", String, primary_key=True), Column("status", String)],
        "weather_export_generations": [
            Column("generation_id", String, primary_key=True),
            Column("attempt_id", String, unique=True),
        ],
    }
    tables = {
        key: Table(prefix + str(i), metadata, *columns)
        for i, (key, columns) in enumerate(definitions.items())
    }
    with engine.connect() as connection, connection.begin():
        metadata.create_all(connection)
        yield connection, SimpleNamespace(tables=tables)
        # 테스트 DDL·자료도 rollback한다. 운영 DB를 사용하지 않는다.
        connection.rollback()
    engine.dispose()


def _reference(connection, metadata, scopes):
    owners, attempts, runs, generations = (
        metadata.tables[name]
        for name in (
            "weather_export_scope_owners",
            "weather_export_attempts",
            "weather_sync_runs",
            "weather_export_generations",
        )
    )
    ownership = connection.execute(select(owners).where(owners.c.source_scope.in_(scopes))).all()
    attempt_ids = {row.active_attempt_id for row in ownership}
    attempt_rows = {
        r.attempt_id: r
        for r in connection.execute(
            select(attempts).where(
                attempts.c.attempt_id.in_(attempt_ids),
            )
        )
    }
    published = dict(
        connection.execute(
            select(
                generations.c.attempt_id,
                generations.c.generation_id,
            ).where(generations.c.attempt_id.in_(attempt_ids))
        )
        .tuples()
        .all()
    )
    statuses = dict(
        connection.execute(
            select(runs.c.run_id, runs.c.status).where(
                runs.c.run_id.in_({r.run_id for r in attempt_rows.values()}),
            )
        )
        .tuples()
        .all()
    )
    result = []
    for scope, attempt_id, epoch in ownership:
        attempt = attempt_rows.get(attempt_id)
        if attempt is None:
            continue
        claimed = (
            attempt.owner_epochs.get(scope) if isinstance(attempt.owner_epochs, dict) else None
        )
        result.append(
            (
                scope,
                attempt_id,
                attempt.run_id,
                attempt.status,
                published.get(attempt_id),
                statuses.get(attempt.run_id),
                (str(claimed)[:32] if claimed is not None else None) == str(epoch),
            )
        )
    return sorted(result)


def test_active_scopes_raw_json_claims_and_missing_states_match_reference(scope_tables):
    connection, metadata = scope_tables
    owners, attempts, runs, generations = (
        metadata.tables[name]
        for name in (
            "weather_export_scope_owners",
            "weather_export_attempts",
            "weather_sync_runs",
            "weather_export_generations",
        )
    )
    claims = [
        1,
        "1",
        1.0,
        True,
        None,
        {},
        [],
        "01",
        "1" + " " * 40,
        9223372036854775807,
        "9223372036854775807",
    ]
    for i, claim in enumerate(claims):
        scope, attempt_id = f"scope-{i}", f"attempt-{i}"
        epoch = 9223372036854775807 if i >= 9 else 1
        connection.execute(
            owners.insert().values(
                source_scope=scope, active_attempt_id=attempt_id, owner_epoch=epoch
            )
        )
        connection.execute(
            attempts.insert().values(
                attempt_id=attempt_id,
                run_id=f"run-{i}",
                status="failed" if i == 6 else "success",
                owner_epochs={scope: claim} if i != 7 else [claim],
            )
        )
        if i != 4:
            connection.execute(
                runs.insert().values(
                    run_id=f"run-{i}",
                    status=("running" if i == 5 else "failed" if i == 6 else "success"),
                )
            )
        if i != 5:
            connection.execute(
                generations.insert().values(generation_id=f"g-{i}", attempt_id=attempt_id)
            )
    # 여러 scope가 공유하는 원본 JSON은 한 번만 전송하고 epoch는 각각 확인한다.
    connection.execute(
        owners.insert().values(source_scope="shared", active_attempt_id="attempt-0", owner_epoch=2)
    )
    connection.execute(
        attempts.update()
        .where(attempts.c.attempt_id == "attempt-0")
        .values(
            owner_epochs={"scope-0": 1, "shared": 2},
        )
    )
    connection.execute(
        owners.insert().values(
            source_scope="missing-attempt", active_attempt_id="missing", owner_epoch=1
        )
    )
    scopes = {f"scope-{i}" for i in range(len(claims))} | {"shared", "missing-attempt", "no-owner"}
    expected = _reference(connection, metadata, scopes)
    statements = []

    def count_sql(*args):
        statements.append(args[2])

    event.listen(connection, "before_cursor_execute", count_sql)
    try:
        actual = sorted(_active_scope_states(connection, metadata, scopes))
        assert actual == expected
        assert len(statements) == 1
        assert _active_scope_states(connection, metadata, set()) == []
        assert len(statements) == 1
    finally:
        event.remove(connection, "before_cursor_execute", count_sql)
    assert {row[0] for row in actual if row[-1]} == {
        "scope-0",
        "scope-1",
        "scope-9",
        "scope-10",
        "shared",
    }
    assert {row[0] for row in actual} == scopes - {"missing-attempt", "no-owner"}


def test_active_scopes_preserve_exponent_duplicate_keys_and_json_null(scope_tables):
    connection, metadata = scope_tables
    owners = metadata.tables["weather_export_scope_owners"]
    attempts = metadata.tables["weather_export_attempts"]
    # jsonb 변환은 1e0을 1로 바꿀 수 있다. 원래 json decoder의 float/중복 키 판정이어야 한다.
    for i, raw in enumerate(['{"s0":1e0}', '{"s1":2,"s1":1}', "null"]):
        connection.execute(
            owners.insert().values(source_scope=f"s{i}", active_attempt_id=f"a{i}", owner_epoch=1)
        )
        connection.exec_driver_sql(
            f'INSERT INTO "{attempts.name}" (attempt_id, run_id, status, owner_epochs) '
            "VALUES (%s, %s, %s, %s::json)",
            (f"a{i}", "absent-run", "pending", raw),
        )
    result = sorted(_active_scope_states(connection, metadata, {"s0", "s1", "s2"}))
    assert result == _reference(connection, metadata, {"s0", "s1", "s2"})
    assert [row[-1] for row in result] == [False, True, False]
    assert all(row[4] is None and row[5] is None for row in result)
