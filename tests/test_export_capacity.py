"""승인 vector와 manifest를 늘려도 DB/JSON 입력은 별도 예산으로 거절한다."""

import json
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, create_engine, event

from kortravelweather import export_capacity as capacity


def test_budget_matches_digest_json_utf8_and_exact_boundary():
    value = {"한글": [None, '"\\\n', Decimal("1.5"), datetime(2026, 10, 24, tzinfo=UTC)]}
    expected = len(json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"),
    ).encode("utf-8"))
    assert capacity.MetadataBudget(expected).add(value) == expected
    with pytest.raises(capacity.ExportCapacityExceeded):
        capacity.MetadataBudget(expected - 1).add(value)
    budget = capacity.MetadataBudget(expected * 2)
    budget.add(value)
    budget.add(value)
    with pytest.raises(capacity.ExportCapacityExceeded):
        budget.add(None)


def test_source_vector_is_independent_of_location_cohort_and_artifact_caps():
    from kortravelweather.export_inventory_selection import MAX_SELECTED_ROWS
    from kortravelweather.export_snapshots import MAX_PAGE_BYTES, MAX_SNAPSHOT_LOCATIONS

    assert capacity.MAX_SOURCE_SCOPES == 8192
    assert MAX_SNAPSHOT_LOCATIONS == 5000 and MAX_SELECTED_ROWS == 20000
    assert MAX_PAGE_BYTES == 4 * 1024 * 1024
    with pytest.raises(capacity.ExportCapacityExceeded):
        capacity.read_generation_refs(None, None, map(str, range(8193)))


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), "\ud800", {1: 1, "a": 2}])
def test_invalid_json_is_a_capacity_error(invalid):
    with pytest.raises(capacity.ExportCapacityExceeded):
        capacity.MetadataBudget().add(invalid)


@pytest.fixture
def refs_connection():
    engine = create_engine(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    metadata = MetaData()
    table = Table(
        "capacity_" + uuid.uuid4().hex, metadata,
        Column("generation_id", String(36), primary_key=True),
        Column("ordinal", Integer, primary_key=True),
        Column("artifact_digest", String(64), nullable=False),
        Column("byte_count", Integer, nullable=False),
        Column("point_count", Integer, nullable=False),
        Column("location_ids", JSON), prefixes=["TEMPORARY"],
    )
    with engine.connect() as connection, connection.begin():
        table.create(connection)
        yield connection, table
    engine.dispose()


def ref_row(generation="generation", ordinal=0, locations=None):
    return dict(generation_id=generation, ordinal=ordinal, artifact_digest="d" * 64,
                byte_count=1024, point_count=1, location_ids=locations)


def test_pg_server_cursor_fetch64_and_legacy_null_projection(refs_connection):
    connection, table = refs_connection
    rows = [ref_row(ordinal=n, locations=[f"장소-{n}"]) for n in range(130)]
    rows.append(ref_row("legacy", locations=None))
    connection.execute(table.insert(), rows)
    observed = []

    def inspect_cursor(connection, cursor, statement, parameters, context, many):
        if context.execution_options.get("stream_results"):
            observed.append((bool(cursor.name), context.execution_options["yield_per"]))

    event.listen(connection.engine, "before_cursor_execute", inspect_cursor)
    try:
        result = capacity.read_generation_refs(connection, table, ["generation", "legacy"])
    finally:
        event.remove(connection.engine, "before_cursor_execute", inspect_cursor)
    assert observed == [(True, 64)]
    assert result["generation"] == rows[:-1]
    assert result["legacy"][0]["location_ids"] is None
    assert connection.exec_driver_sql("SELECT 1").scalar() == 1


@pytest.mark.parametrize("boundary", ["locations", "rows", "bytes"])
def test_pg_oversized_refs_fail_closed_and_close_cursor(refs_connection, monkeypatch, boundary):
    connection, table = refs_connection
    locations = ["x" * (capacity.MAX_REF_LOCATION_BYTES + 1)] if boundary == "locations" else ["a"]
    connection.execute(table.insert(), [ref_row(ordinal=n, locations=locations) for n in range(2)])
    if boundary == "rows":
        monkeypatch.setattr(capacity, "MAX_GENERATION_REFS", 1)
    elif boundary == "bytes":
        original = capacity.MetadataBudget
        monkeypatch.setattr(capacity, "MetadataBudget", lambda: original(1))
    with pytest.raises(capacity.ExportCapacityExceeded):
        capacity.read_generation_refs(connection, table, ["generation"])
    assert connection.exec_driver_sql("SELECT 1").scalar() == 1
