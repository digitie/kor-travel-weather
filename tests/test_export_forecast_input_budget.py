"""한 페이지의 실제 payload I/O(선읽기 포함)와 cursor 전체 walk를 검증한다."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from test_export_database_read_window import store as store
from test_export_forecast_projection import put_chunk, reader
from test_export_read_window_refill import Single

from kortravelweather import export_forecasts
from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore

NOW = datetime(2026, 11, 19, tzinfo=UTC)


def point(key, *, size, future=True):
    result = dict(
        provider="python-kma-api", forecast_style="short", location_id=key,
        target_at=(NOW + timedelta(hours=1 if future else -1)).isoformat(),
        known_at=NOW.isoformat(), metric_key="TMP", value_number=1.23456789012345,
        value_text=None, padding="",
    )
    used = len((json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode())
    result["padding"] = "x" * (size - used)
    assert len((json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()) == size
    return result


def manifest(store, sizes, *, expired=()):
    keys = [f"location-{i:03d}" for i in range(len(sizes))]
    refs = [dict(put_chunk(store, [point(key, size=size, future=i not in expired)]),
                 location_ids=[key]) for i, (key, size) in enumerate(zip(keys, sizes, strict=True))]
    return {"scopes": [dict(location_ids=keys, refs=refs, published_at=NOW.isoformat())]}


def actual_reads(monkeypatch, store):
    reads = []
    verify = store._verify_payload_row

    def observed(row, digest, size, count):
        reads.append((digest, len(row.payload)))
        return verify(row, digest, size, count)

    monkeypatch.setattr(store, "_verify_payload_row", observed)
    return reads


def budget(monkeypatch, size):
    # RED에서도 import 실패 대신 실제 old I/O 초과로 실패해야 한다.
    monkeypatch.setattr(export_forecasts, "MAX_PAGE_INPUT_BYTES", size, raising=False)


def test_cumulative_input_budget_includes_every_prefetched_window(store, monkeypatch):
    budget(monkeypatch, 10 * 1024)
    frozen = manifest(store, [1024] * 25)
    reads = actual_reads(monkeypatch, store)
    state = {"g": 0, "p": 0}
    rows, more = reader(store)._page_items(frozen, state, 200, NOW)
    assert sum(size for _, size in reads) == 10 * 1024
    assert len(reads) == len(rows) == 10
    assert more and state == {"g": 0, "p": 10}
    assert len(set(digest for digest, _ in reads)) == len(reads)


def test_default_page_budget_is_eight_mib_not_per_window(store, monkeypatch):
    frozen = manifest(store, [1024 * 1024] * 10)
    reads = actual_reads(monkeypatch, store)
    state = {"g": 0, "p": 0}
    rows, more = reader(store)._page_items(frozen, state, 2000, NOW)
    assert sum(size for _, size in reads) == 8 * 1024 * 1024
    assert len(rows) == 8 and more and state == {"g": 0, "p": 8}


@pytest.mark.parametrize("bulk", [False, True])
def test_first_single_oversized_artifact_progresses_without_prefetching_next(
    store, monkeypatch, bulk,
):
    budget(monkeypatch, 1024)
    frozen = manifest(store, [2048, 512])
    reads = actual_reads(monkeypatch, store)
    instance = reader(store if bulk else Single(store))
    state = {"g": 0, "p": 0}
    rows, more = instance._page_items(frozen, state, 200, NOW)
    assert [size for _, size in reads] == [2048]
    assert len(rows) == 1 and more and state == {"g": 0, "p": 1}
    reads.clear()
    rows, more = instance._page_items(frozen, state, 200, NOW)
    assert [size for _, size in reads] == [512]
    assert len(rows) == 1 and not more and state == {"g": 1, "p": 0}


@pytest.mark.parametrize("bulk", [False, True])
@pytest.mark.parametrize("limit", [1, 200, 2000])
def test_complete_walk_preserves_items_precision_order_and_empty_page_progress(
    store, monkeypatch, bulk, limit,
):
    budget(monkeypatch, 2048)
    sizes = [2048, 512, 512, 1024, 512, 1024]
    frozen = manifest(store, sizes, expired={0, 3})
    reads = actual_reads(monkeypatch, store)
    instance = reader(store if bulk else Single(store))
    state = {"g": 0, "p": 0}
    items, pages = [], []
    for _ in range(20):
        previous = dict(state)
        reads.clear()
        rows, more = instance._page_items(frozen, state, limit, NOW)
        assert sum(size for _, size in reads) <= 2048
        assert len(rows) <= limit and (state != previous or not more)
        items.extend(rows)
        pages.append(rows)
        if not more:
            break
    else:
        pytest.fail("bounded cursor walk did not complete")
    expected = [dict(
        location_id=f"location-{i:03d}", provider="python-kma-api", forecast_style="short",
        known_at=NOW.isoformat(), published_at=NOW.isoformat(),
        target_at=[(NOW + timedelta(hours=1)).isoformat()],
        metrics={"TMP": [1.23456789012345]},
    ) for i in (1, 2, 4, 5)]
    assert items == expected
    assert len({row["location_id"] for row in items}) == len(items)
    assert pages[0] == [] and instance._expired_omitted


@pytest.mark.parametrize("bad", ["sha", "bytes", "points", "missing"])
def test_next_page_rechecks_every_input_even_after_decoded_cache_warm(monkeypatch, bad):
    # SQLite에만 SHA CHECK가 없다. DB 불변성 검사가 빠져도 reader 검산이 남는지 시험한다.
    engine = create_engine("sqlite://")
    artifacts = DatabaseExportArtifactStore(SimpleNamespace(engine=engine))
    artifacts.table.create(engine)
    budget(monkeypatch, 1024)
    try:
        frozen = manifest(artifacts, [1024, 1024])
        tail = frozen["scopes"][0]["refs"][1]
        artifacts.read_many([tail])
        instance = reader(artifacts)
        state = {"g": 0, "p": 0}
        rows, more = instance._page_items(frozen, state, 200, NOW)
        assert len(rows) == 1 and more and state == {"g": 0, "p": 1}
        with engine.begin() as connection:
            query = artifacts.table.update().where(
                artifacts.table.c.digest == tail["artifact_digest"],
            )
            if bad == "missing":
                connection.execute(artifacts.table.delete().where(
                    artifacts.table.c.digest == tail["artifact_digest"],
                ))
            elif bad == "sha":
                payload = connection.scalar(artifacts.table.select().with_only_columns(
                    artifacts.table.c.payload,
                ).where(artifacts.table.c.digest == tail["artifact_digest"]))
                replacement = payload.replace(b'"padding":"x', b'"padding":"y', 1)
                assert len(replacement) == len(payload)
                assert hashlib.sha256(replacement).hexdigest() != tail["artifact_digest"]
                connection.execute(query.values(payload=replacement))
            elif bad == "bytes":
                tail["byte_count"] -= 1
            else:
                tail["point_count"] += 1
        with pytest.raises(ExportArtifactError):
            instance._page_items(frozen, state, 200, NOW)
    finally:
        engine.dispose()


def test_since_skipped_scopes_do_not_spend_the_page_input_budget(store, monkeypatch):
    budget(monkeypatch, 1024)
    first = manifest(store, [1024])["scopes"][0]
    second = manifest(store, [512])["scopes"][0]
    first["published_at"] = (NOW - timedelta(minutes=1)).isoformat()
    second["published_at"] = (NOW + timedelta(minutes=1)).isoformat()
    reads = actual_reads(monkeypatch, store)
    instance = reader(store)
    instance.since = NOW
    rows, more = instance._page_items(
        {"scopes": [first, second]}, {"g": 0, "p": 0}, 200, NOW,
    )
    assert [size for _, size in reads] == [512]
    assert len(rows) == 1 and not more
    assert rows[0]["published_at"] == second["published_at"]


def test_first_oversized_progress_does_not_bypass_hard_artifact_bounds(store, monkeypatch):
    budget(monkeypatch, 1024)
    frozen = manifest(store, [1024])
    frozen["scopes"][0]["refs"][0]["byte_count"] = 8 * 1024 * 1024 + 1
    reads = actual_reads(monkeypatch, store)
    with pytest.raises(ExportArtifactError):
        reader(store)._page_items(frozen, {"g": 0, "p": 0}, 200, NOW)
    assert reads == []


def test_verified_input_trace_counts_prefetch_and_isolates_parallel_workers(store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from kortravelweather.export_read_window import ArtifactReadWindow
    from kortravelweather.read_performance import ReadTrace, read_trace

    refs = manifest(store, [1024] * 10)["scopes"][0]["refs"]
    own, other = ReadTrace("forecasts", "first"), ReadTrace("forecasts", "first")
    barrier = Barrier(2, timeout=10)

    def worker(trace, prefetch):
        with read_trace(trace):
            barrier.wait()
            if prefetch:
                window = ArtifactReadWindow(store, max_bytes=8 * 1024)
                window.prepare(refs[0], lambda: iter(refs), remaining_reads=128)
                window.read(refs[0])  # 1개만 소비해도 선읽기8개 전부를 trace에 센다.
            else:
                store.read_many(refs[8:])

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(worker, own, True), pool.submit(worker, other, False)
        a.result()
        b.result()
    assert getattr(own, "forecast_input_bytes", 0) == 8 * 1024
    assert getattr(own, "forecast_input_refs", 0) == 8
    assert getattr(other, "forecast_input_bytes", 0) == 2 * 1024
    assert getattr(other, "forecast_input_refs", 0) == 2
    store.read_many(refs[:1])  # trace 밖의 동시/후속 읽기는 귀속되지 않는다.
    assert own.forecast_input_bytes == 8 * 1024 and own.forecast_input_refs == 8


def test_verified_input_trace_counts_single_reads_only_after_receipt_checks(store):
    from kortravelweather.read_performance import ReadTrace, read_trace

    ref = manifest(store, [1024])["scopes"][0]["refs"][0]
    trace = ReadTrace("forecasts", "first")
    with read_trace(trace):
        store.read_shared(ref["artifact_digest"], byte_count=1024, point_count=1)
        with pytest.raises(ExportArtifactError):
            store.read_shared(ref["artifact_digest"], byte_count=1024, point_count=2)
    assert getattr(trace, "forecast_input_bytes", 0) == 1024
    assert getattr(trace, "forecast_input_refs", 0) == 1
