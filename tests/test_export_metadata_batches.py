"""같은 MVCC의 바이트 상한과 순서·FK·발급 TX 원자성을 대조한다."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    select,
)
from sqlalchemy.exc import IntegrityError

from kortravelweather import export_capacity as capacity
from kortravelweather.export_snapshot_refs import insert_snapshot_refs


@pytest.fixture
def connection():
    engine = create_engine(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
        yield c
    engine.dispose()


@pytest.mark.parametrize("large", [False, True])
def test_plan_stream_bytes_allow_large_batch_only_for_uncompressed_small_total(connection, large):
    table = Table("batch_" + uuid4().hex, MetaData(),
                  Column("attempt_id", String, primary_key=True), Column("descriptor", JSON),
                  prefixes=["TEMPORARY"])
    table.create(connection)
    rows = [{"attempt_id": str(uuid4()), "descriptor": {"label": "한글", "ordinal": i}}
            for i in range(600)]
    if large:
        rows[0]["descriptor"]["padding"] = "x" * 100_000
    connection.execute(table.insert(), rows)
    observed = []

    def inspect(c, cursor, statement, params, ctx, many):
        if ctx.execution_options.get("stream_results"):
            observed.append((bool(cursor.name), ctx.execution_options["yield_per"]))

    event.listen(connection.engine, "before_cursor_execute", inspect)
    try:
        actual = capacity.read_scope_plans(connection, table, [r["attempt_id"] for r in rows])
    finally:
        event.remove(connection.engine, "before_cursor_execute", inspect)
    assert actual == {r["attempt_id"]: r["descriptor"] for r in rows}
    assert observed == [(True, capacity.PLAN_FETCH_ROWS if large else capacity.FAST_FETCH_ROWS)]


def test_fast_stream_keeps_missing_and_total_budget_rejection(connection, monkeypatch):
    table = Table("batch_" + uuid4().hex, MetaData(),
                  Column("attempt_id", String, primary_key=True), Column("descriptor", JSON),
                  prefixes=["TEMPORARY"])
    table.create(connection)
    ids = [str(uuid4()) for _ in range(600)]
    connection.execute(table.insert(), [{"attempt_id": key, "descriptor": {"x": key}}
                                       for key in ids])
    with pytest.raises(capacity.ExportCapacityExceeded):
        capacity.read_scope_plans(connection, table, ids + [str(uuid4())])
    budget = capacity.MetadataBudget
    monkeypatch.setattr(capacity, "MetadataBudget", lambda: budget(1))
    with pytest.raises(capacity.ExportCapacityExceeded):
        capacity.read_scope_plans(connection, table, ids)
    assert connection.exec_driver_sql("SELECT 1").scalar() == 1


def test_snapshot_refs_one_insert_preserves_duplicates_ordinals_and_fk_atomicity(connection):
    m = MetaData()
    prefix = "refs_" + uuid4().hex
    snapshots = Table(prefix + "_snapshots", m, Column("id", String, primary_key=True),
                      prefixes=["TEMPORARY"])
    generations = Table(prefix + "_generations", m, Column("id", String, primary_key=True),
                        prefixes=["TEMPORARY"])
    refs = Table(prefix, m,
                 Column("snapshot_id", String, ForeignKey(snapshots.c.id), primary_key=True),
                 Column("ordinal", Integer, primary_key=True),
                 Column("generation_id", String, ForeignKey(generations.c.id), nullable=False),
                 prefixes=["TEMPORARY"])
    m.create_all(connection)
    ids = [str(uuid4()), str(uuid4())]
    sid = str(uuid4())
    connection.execute(generations.insert(), [{"id": key} for key in ids])
    connection.execute(snapshots.insert(), {"id": sid})
    wanted = [ids[1], ids[0], ids[1]] * 2204
    observed = []

    def inspect(c, cursor, statement, params, ctx, many):
        if ctx.isinsert:
            observed.append((many, len(params)))

    event.listen(connection.engine, "before_cursor_execute", inspect)
    try:
        insert_snapshot_refs(connection, refs, sid, wanted)
    finally:
        event.remove(connection.engine, "before_cursor_execute", inspect)
    assert len(observed) == 1 and not observed[0][0] and observed[0][1] <= 3
    assert list(connection.scalars(select(refs.c.generation_id).order_by(refs.c.ordinal))) == wanted
    bad = str(uuid4())
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(snapshots.insert(), {"id": bad})
        insert_snapshot_refs(connection, refs, bad, [ids[0], str(uuid4()), ids[1]])
    assert connection.scalar(select(snapshots.c.id).where(snapshots.c.id == bad)) is None
    assert connection.scalar(select(refs.c.snapshot_id).where(refs.c.snapshot_id == bad)) is None
