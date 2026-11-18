"""실제 PG에서 per-page bounded batch의 응답 동등성·쿼리 수·무결성을 검증한다."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event

from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_database_artifacts import (
    MAX_ARTIFACT_READ_BATCH,
    MAX_ARTIFACT_READ_BYTES,
    DatabaseExportArtifactStore,
)
from kortravelweather.export_forecasts import FrozenForecastExports
from kortravelweather.export_read_window import ArtifactReadWindow
from kortravelweather.export_snapshots import MAX_PAGE_ARTIFACTS
from kortravelweather.repository import WeatherRepository


@pytest.fixture
def store(weather_test_database_url):
    repository = WeatherRepository(weather_test_database_url)
    repository.create_schema()
    yield DatabaseExportArtifactStore(repository)
    repository.engine.dispose()


def _put(store, points):
    rows, refs = [], []
    for point in points:
        payload = (json.dumps(point, sort_keys=True, separators=(",", ":")) + "\n").encode()
        digest = hashlib.sha256(payload).hexdigest()
        rows.append(dict(digest=digest, payload=payload, byte_count=len(payload), point_count=1,
                         created_at=datetime.now(UTC)))
        refs.append(dict(artifact_digest=digest, byte_count=len(payload), point_count=1))
    with store.engine.begin() as connection:
        connection.execute(store.table.insert(), rows)
    return refs


def _observe_payloads(store):
    calls = []

    def observed(connection, cursor, statement, parameters, context, many):
        if "weather_export_artifacts.payload" in statement:
            calls.append(1)

    event.listen(store.engine, "before_cursor_execute", observed)
    return calls, observed


def test_batch_matches_single_reads_and_keeps_returned_objects_independent(store):
    refs = _put(store, [{"n": i, "nested": {"x": [i]}} for i in range(8)])
    expected = {ref["artifact_digest"]: store.read_shared(
        ref["artifact_digest"], byte_count=ref["byte_count"], point_count=ref["point_count"],
    ) for ref in refs}
    calls, listener = _observe_payloads(store)
    try:
        assert store.read_many(refs) == expected
        assert len(calls) == 1
        result = store.read_many(refs)
        result[refs[0]["artifact_digest"]][0]["nested"]["x"].append("changed")
        assert store.read_many(refs) == expected
    finally:
        event.remove(store.engine, "before_cursor_execute", listener)
    assert store.engine.pool.checkedout() == 0


@pytest.mark.parametrize("bad", ["size", "points", "missing"])
def test_batch_still_checks_receipts_and_missing_rows_on_warm_cache(store, bad):
    refs = _put(store, [{"n": 1}, {"n": 2}])
    store.read_many(refs)
    if bad == "size":
        refs[0] = dict(refs[0], byte_count=refs[0]["byte_count"] + 1)
    elif bad == "points":
        refs[0] = dict(refs[0], point_count=2)
    else:
        with store.engine.begin() as connection:
            connection.execute(store.table.delete().where(
                store.table.c.digest == refs[0]["artifact_digest"],
            ))
    with pytest.raises(ExportArtifactError):
        store.read_many(refs)
    assert store.engine.pool.checkedout() == 0


@pytest.mark.parametrize("bad", ["hash", "line_count"])
def test_batch_rechecks_actual_payload_even_when_database_has_no_sha_check(bad):
    engine = create_engine("sqlite://")
    instance = DatabaseExportArtifactStore(SimpleNamespace(engine=engine))
    instance.table.create(engine)
    good, bad_payload = b'{"n":1}\n', b'{"n":2}\n'
    digest = hashlib.sha256(good).hexdigest()
    count = 2 if bad == "line_count" else 1
    with engine.begin() as connection:
        connection.execute(instance.table.insert().values(
            digest=digest, payload=good if bad == "line_count" else bad_payload,
            byte_count=len(good), point_count=count, created_at=datetime.now(UTC),
        ))
    try:
        with pytest.raises(ExportArtifactError):
            instance.read_many([dict(
                artifact_digest=digest, byte_count=len(good), point_count=count,
            )])
    finally:
        engine.dispose()


@pytest.mark.parametrize("bad", ["count", "bytes", "conflicting_receipt"])
def test_batch_bound_and_conflicting_receipts_fail_before_payload_sql(store, bad):
    refs = [dict(artifact_digest=f"{i:064x}", byte_count=1, point_count=1)
            for i in range(MAX_ARTIFACT_READ_BATCH + 1)]
    if bad == "bytes":
        refs = [dict(ref, byte_count=MAX_ARTIFACT_READ_BYTES // 2 + 1) for ref in refs[:2]]
    elif bad == "conflicting_receipt":
        refs = [refs[0], dict(refs[0], point_count=2)]
    calls, listener = _observe_payloads(store)
    try:
        with pytest.raises(ExportArtifactError):
            store.read_many(refs)
        assert not calls
    finally:
        event.remove(store.engine, "before_cursor_execute", listener)


@pytest.mark.parametrize("limit", [200, 2000])
def test_forecast_915_scope_fixture_batches_only_the_128_scope_page_and_preserves_output(
    store, limit,
):
    now = datetime.now(UTC)
    points = [{
        "location_id": str(i), "provider": "python-kma-api", "forecast_style": "short",
        "target_at": (now + timedelta(hours=1)).isoformat(), "known_at": now.isoformat(),
        "metric_key": "TMP", "value_number": i, "value_text": None,
    } for i in range(915)]
    refs = _put(store, points)
    scopes = [dict(location_ids=[str(i)], generation_id=str(i), namespace=str(i),
                   published_at=now.isoformat(), refs=[dict(ref, location_ids=[str(i)])])
              for i, ref in enumerate(refs)]

    class SingleReads:
        def read_shared(self, *args, **kwargs):
            return store.read_shared(*args, **kwargs)

    def page(artifacts):
        reader = object.__new__(FrozenForecastExports)
        reader.style, reader.since, reader.reuse_artifacts = "short", None, set()
        reader.artifacts = artifacts
        state = {"g": 0, "p": 0}
        result = reader._page_items({"scopes": scopes}, state, limit, now)
        return result, state

    calls, listener = _observe_payloads(store)
    try:
        original = page(SingleReads())
        original_calls = len(calls)
        calls.clear()
        batched = page(store)
        assert batched == original
        assert len(batched[0][0]) == MAX_PAGE_ARTIFACTS
        assert original_calls == MAX_PAGE_ARTIFACTS
        assert len(calls) == MAX_PAGE_ARTIFACTS // MAX_ARTIFACT_READ_BATCH
    finally:
        event.remove(store.engine, "before_cursor_execute", listener)


def test_read_window_limits_canonical_bytes_without_claiming_a_python_heap_budget():
    captured = []

    class Bulk:
        def read_many(self, refs):
            captured.append(refs)
            return {r["artifact_digest"]: [] for r in refs}

    refs = [dict(artifact_digest=f"{i:064x}", byte_count=MAX_ARTIFACT_READ_BYTES // 2 + 1,
                 point_count=1) for i in range(100)]
    window = ArtifactReadWindow(Bulk())
    window.prepare(refs[0], lambda: iter(refs), remaining_reads=128)
    assert len(captured[0]) == 1
    assert sum(r["byte_count"] for r in captured[0]) <= MAX_ARTIFACT_READ_BYTES


@pytest.mark.parametrize("fields", [None, ("n",)])
def test_batch_shared_guard_blocks_gc_until_hash_and_decode_complete(store, monkeypatch, fields):
    import threading

    from kortravelweather.export_artifacts import ExportGuardTimeout

    refs = _put(store, [{"n": 1}, {"n": 2}])
    waiting, release = threading.Event(), threading.Event()
    verify = store._verify_payload_row
    results, failures = [], []

    def blocked(*args):
        waiting.set()
        assert release.wait(3)
        return verify(*args)

    monkeypatch.setattr(store, "_verify_payload_row", blocked)

    def read():
        try:
            results.append(store.read_many(refs, fields=fields))
        except BaseException as error:
            failures.append(error)

    reader = threading.Thread(target=read)
    reader.start()
    assert waiting.wait(3)
    writer = DatabaseExportArtifactStore(store.repository)
    try:
        with pytest.raises(ExportGuardTimeout), writer.guard(timeout_seconds=.03):
            pytest.fail("GC entered while the batch still holds its shared proof")
    finally:
        release.set()
        reader.join(3)
    assert not reader.is_alive() and not failures and results
    monkeypatch.setattr(store, "_verify_payload_row", verify)
    with writer.guard(timeout_seconds=1):
        deleted = writer.delete_many(
            [r["artifact_digest"] for r in refs],
            created_before=datetime.now(UTC) + timedelta(days=1),
        )
    assert len(deleted) == 2
    with pytest.raises(ExportArtifactError):
        store.read_many(refs, fields=fields)


def test_window_rechecks_receipt_when_reusing_an_already_buffered_digest():
    class Bulk:
        def read_many(self, refs):
            return {r["artifact_digest"]: [] for r in refs}

    ref = dict(artifact_digest="a" * 64, byte_count=1, point_count=1)
    window = ArtifactReadWindow(Bulk())
    window.prepare(ref, lambda: iter([ref]), remaining_reads=128)
    assert window.read(ref) == []
    with pytest.raises(ExportArtifactError):
        window.read(dict(ref, point_count=2))
