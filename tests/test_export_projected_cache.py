"""Projection cache의 실효 보관량·분리·삭제/내용 검산과 기존 공유 예산을 검증한다."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from test_export_database_read_window import _put
from test_export_database_read_window import store as store

from kortravelweather import export_artifacts, export_database_artifacts
from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore


def decoder_calls(monkeypatch):
    calls = []
    decode = export_database_artifacts._decode_json_line

    def counted(line):
        calls.append(1)
        return decode(line)

    monkeypatch.setattr(export_database_artifacts, "_decode_json_line", counted)
    return calls


def test_projected_revisit_hits_when_raw_set_exceeds_shared_budget(store, monkeypatch):
    monkeypatch.setattr(export_artifacts, "_DECODED_ARTIFACT_BYTES", 2048)
    points = [{"value_number": n, "observation_evidence": {"raw": "x" * 4096}} for n in range(8)]
    refs = _put(store, points)
    calls = decoder_calls(monkeypatch)
    payload_reads = []

    def observed(_c, _cursor, statement, _parameters, _ctx, _many):
        if "weather_export_artifacts.payload" in statement:
            payload_reads.append(1)

    event.listen(store.engine, "before_cursor_execute", observed)
    try:
        first = store.read_many(refs, fields=("value_number",))
        assert len(calls) == 8
        second = store.read_many(refs, fields=("value_number",))
    finally:
        event.remove(store.engine, "before_cursor_execute", observed)
    assert first == second
    assert len(calls) == 8, "same projected set must survive and skip repeat decode"
    assert len(payload_reads) == 2, "cache hit still reads and verifies the actual payload"
    assert sum(ref["byte_count"] for ref in refs) > 2048
    assert 0 < store._decoded.bytes <= 2048


def test_fieldset_order_and_full_read_never_reconstruct_missing_fields(store, monkeypatch):
    points = [{"value_number": 1, "value_text": {"nested": [2]}, "unused": {"raw": [3]}}]
    refs = _put(store, points)
    calls = decoder_calls(monkeypatch)
    first = store.read_many(refs, fields=("value_number", "value_text"))
    first[refs[0]["artifact_digest"]][0]["value_text"]["nested"].append("changed")
    again = store.read_many(refs, fields=("value_text", "value_number"))
    point = again[refs[0]["artifact_digest"]][0]
    assert list(point) == ["value_text", "value_number"]
    assert point == {"value_number": 1, "value_text": {"nested": [2]}}
    assert len(calls) == 1  # fieldset 순서만 다르면 같은 projection이다.
    assert store.read_many(refs, fields=("unused",))[refs[0]["artifact_digest"]] == [
        {"unused": {"raw": [3]}},
    ]
    assert len(calls) == 2
    assert store.read_many(refs)[refs[0]["artifact_digest"]] == points
    assert len(calls) == 3  # 전체 읽기는 projection으로 복원하지 않고 모든 JSON을 다시 읽는다.


@pytest.mark.parametrize("budget,entries", [(0, 8192), (67108864, 0), (1024, 2)])
def test_projection_obeys_same_byte_and_entry_caps(store, monkeypatch, budget, entries):
    monkeypatch.setattr(export_artifacts, "_DECODED_ARTIFACT_BYTES", budget)
    monkeypatch.setattr(export_artifacts, "_DECODED_ARTIFACT_ENTRIES", entries)
    refs = _put(store, [{"value_number": n} for n in range(3)])
    store.read_many(refs, fields=("value_number",))
    with store._decoded.lock:
        assert len(store._decoded.entries) <= entries
        assert store._decoded.bytes <= budget
        assert store._decoded.bytes == sum(entry[2] for entry in store._decoded.entries.values())
        if budget == 0 or entries == 0:
            assert not store._decoded.entries


def test_projection_weight_failure_bypasses_cache_without_changing_read(store, monkeypatch):
    refs = _put(store, [{"value_number": 1}])
    dumps = json.dumps

    def no_cache_weight(value, **kwargs):
        if kwargs.get("ensure_ascii") is True:
            raise ValueError("test: unmeasurable projection")
        return dumps(value, **kwargs)

    monkeypatch.setattr(export_database_artifacts.json, "dumps", no_cache_weight)
    result = store.read_many(refs, fields=("value_number",))
    assert result[refs[0]["artifact_digest"]] == [{"value_number": 1}]
    assert not store._decoded.entries


@pytest.mark.parametrize("bad", ["payload", "point_count", "deleted"])
def test_warm_projection_still_fails_actual_tamper_and_missing_rows(bad):
    engine = create_engine("sqlite://")
    instance = DatabaseExportArtifactStore(SimpleNamespace(engine=engine))
    instance.table.create(engine)
    payload = b'{"value_number":1,"unused":2}\n'
    digest = hashlib.sha256(payload).hexdigest()
    ref = dict(artifact_digest=digest, byte_count=len(payload), point_count=1)
    with engine.begin() as c:
        c.execute(
            instance.table.insert().values(
                digest=digest,
                payload=payload,
                byte_count=len(payload),
                point_count=1,
                created_at=datetime.now(UTC),
            )
        )
    try:
        assert instance.read_many([ref], fields=("value_number",))[digest] == [{"value_number": 1}]
        with engine.begin() as c:
            if bad == "payload":
                c.execute(
                    instance.table.update().values(
                        payload=payload.replace(b'"unused":2', b'"unused":3')
                    )
                )
            elif bad == "point_count":
                c.execute(instance.table.update().values(point_count=2))
            else:
                c.execute(instance.table.delete())
        if bad == "point_count":
            ref["point_count"] = 2  # row와 receipt가 같아도 실제 JSON line 수가 달라 실패해야 한다.
        with pytest.raises(ExportArtifactError):
            instance.read_many([ref], fields=("value_number",))
    finally:
        engine.dispose()


def test_concurrent_projected_reads_keep_independent_objects_and_accounting(store, monkeypatch):
    monkeypatch.setattr(export_artifacts, "_DECODED_ARTIFACT_BYTES", 2048)
    refs = _put(store, [{"value_text": {"values": [n]}, "unused": "x" * 4096} for n in range(8)])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.read_many(refs, fields=("value_text",)), range(2)))
    first_digest = refs[0]["artifact_digest"]
    results[0][first_digest][0]["value_text"]["values"].append("changed")
    assert results[1][first_digest][0]["value_text"] == {"values": [0]}
    assert store.read_many(refs, fields=("value_text",))[first_digest][0]["value_text"] == {
        "values": [0]
    }
    with store._decoded.lock:
        assert store._decoded.bytes == sum(entry[2] for entry in store._decoded.entries.values())
        assert store._decoded.bytes <= 2048


def test_engine_scope_and_delete_are_not_hidden_by_other_engine_projection(store):
    refs = _put(store, [{"value_number": 1}])
    expected = store.read_many(refs, fields=("value_number",))
    engine = create_engine(store.engine.url)
    other = DatabaseExportArtifactStore(SimpleNamespace(engine=engine))
    try:
        assert other._decoded is not store._decoded
        assert other.read_many(refs, fields=("value_number",)) == expected
        with store.engine.begin() as c:
            c.execute(store.table.delete())
        for reader in (store, other):
            with pytest.raises(ExportArtifactError):
                reader.read_many(refs, fields=("value_number",))
    finally:
        engine.dispose()


def test_warm_projection_does_not_hide_direct_approved_health_change(store, tmp_path, monkeypatch):
    from datetime import timedelta

    from test_frozen_service_exports import HEADERS, _client, _collect_forecast

    from kortravelweather.repository import Base

    repository = store.repository
    _collect_forecast(repository, tmp_path)
    calls = decoder_calls(monkeypatch)
    client = _client(repository, tmp_path)
    path = "/v1/service/exports/forecasts?style=ultra_short&limit=200"
    first = client.get(path, headers=HEADERS)
    assert first.status_code == 200 and first.json()["meta"]["source_status"] == "ok"
    decoded = len(calls)
    assert decoded > 0
    inventory = Base.metadata.tables["weather_export_approved_locations"]
    with repository.engine.begin() as c:
        c.execute(
            inventory.update().values(
                source_collected_at=datetime.now(UTC) - timedelta(hours=7),
            )
        )
    response = client.get(path, headers={**HEADERS, "if-none-match": first.headers["etag"]})
    assert response.status_code == 200 and response.json()["meta"]["source_status"] == "stale"
    assert response.headers["etag"] != first.headers["etag"]
    assert response.json()["items"] == first.json()["items"]
    assert len(calls) == decoded, (
        "health changes must be seen even when artifact projection is warm"
    )
    assert int(response.headers["x-export-artifact-read-calls"]) > 0
    client.close()
