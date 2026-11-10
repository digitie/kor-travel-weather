"""불변 snapshot pin은 Connection과 기존 Session fallback 모두에서 원자적으로 저장한다."""

import uuid

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kortravelweather.export_snapshot_refs import insert_snapshot_refs


@pytest.fixture
def ref_tables(weather_test_database_url):
    engine = create_engine(weather_test_database_url)
    schema = "refs_" + uuid.uuid4().hex
    metadata = MetaData(schema=schema)
    generations = Table("generations", metadata, Column("generation_id", String, primary_key=True))
    refs = Table("refs", metadata,
                 Column("snapshot_id", String, primary_key=True),
                 Column("ordinal", Integer, primary_key=True),
                 Column("generation_id", ForeignKey(generations.c.generation_id), nullable=False))
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        metadata.create_all(connection)
        connection.execute(generations.insert(), [{"generation_id": str(i)} for i in range(513)])
    try:
        yield engine, refs
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()


@pytest.mark.parametrize("binding", ["connection", "session", "table_session"])
def test_pin_ordinal_batching_preserves_connection_and_session_bind(ref_tables, binding):
    engine, refs = ref_tables
    context = (engine.begin() if binding == "connection" else
               Session(engine) if binding == "session" else Session(binds={refs: engine}))
    with context as writer:
        insert_snapshot_refs(writer, refs, "snapshot", (str(i) for i in range(513)))
        rows = writer.execute(select(refs.c.ordinal, refs.c.generation_id)
                              .order_by(refs.c.ordinal)).all()
        assert rows == [(i, str(i)) for i in range(513)]


@pytest.mark.parametrize("binding", ["connection", "session"])
def test_failed_later_batch_rolls_back_earlier_pins(ref_tables, binding):
    engine, refs = ref_tables
    context = engine.connect() if binding == "connection" else Session(engine)
    with context as writer:
        with pytest.raises(IntegrityError), writer.begin():
            insert_snapshot_refs(writer, refs, "snapshot", [str(i) for i in range(512)] + ["lost"])
        assert writer.execute(select(refs)).all() == []


@pytest.mark.parametrize("binding", ["connection", "session"])
def test_non_pg_pin_storage_retains_session_compatibility(binding):
    engine = create_engine("sqlite://")
    metadata = MetaData()
    refs = Table("refs", metadata, Column("snapshot_id", String), Column("ordinal", Integer),
                 Column("generation_id", String))
    metadata.create_all(engine)
    with engine.connect() if binding == "connection" else Session(engine) as writer:
        insert_snapshot_refs(writer, refs, "snapshot", ["a", "b"])
        assert writer.execute(select(refs.c.ordinal, refs.c.generation_id)).all() == [
            (0, "a"), (1, "b"),
        ]
    engine.dispose()
