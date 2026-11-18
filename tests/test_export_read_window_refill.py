"""단일 scope 내부 window 고갈·cursor 재개를 실제 PG payload SELECT로 검증한다."""

from datetime import UTC, datetime, timedelta
from math import ceil

import pytest
from sqlalchemy import event
from test_export_database_read_window import _put
from test_export_database_read_window import store as store

from kortravelweather import export_snapshots
from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_forecasts import FrozenForecastExports
from kortravelweather.export_heads import _digest
from kortravelweather.export_snapshots import MAX_PAGE_ARTIFACTS, FrozenCurrentExports


class Reads:
    def __init__(self, store):
        self.store = store
        self.sizes = []
        self.digests = []

    def __enter__(self):
        event.listen(self.store.engine, "after_cursor_execute", self.after)
        return self

    def after(self, connection, cursor, statement, parameters, context, many):
        if "weather_export_artifacts.payload" in statement:
            self.sizes.append(cursor.rowcount)
            self.digests.extend(parameters.values())

    def __exit__(self, *args):
        event.remove(self.store.engine, "after_cursor_execute", self.after)


class Single:
    def __init__(self, store):
        self.store = store

    def __getattr__(self, name):
        if name == "read_many":
            raise AttributeError(name)
        return getattr(self.store, name)


def scope_fixture(store, count, now, *, forecast):
    points = [{
        "location_id": f"{i:04d}", "provider": "python-kma-api",
        "forecast_style": "short" if forecast else "nowcast",
        "target_at": (now + timedelta(hours=1)).isoformat(), "known_at": now.isoformat(),
        "metric_key": "TMP", "value_number": i, "value_text": None, "unit": "C",
    } for i in range(count)]
    refs = _put(store, points)
    for point, ref in zip(points, refs, strict=True):
        ref["location_ids"] = [point["location_id"]]
    return dict(
        location_ids=[p["location_id"] for p in points], refs=refs,
        generation_id="generation", namespace="namespace", source_scope="source",
        nx=60, ny=127, published_at=now.isoformat(),
    )


@pytest.mark.parametrize("count", [128, 256])
@pytest.mark.parametrize("limit", [17, 200, 2000])
def test_forecast_one_scope_refills_inside_scope_and_cursor_without_single_reads(
    store, count, limit,
):
    now = datetime.now(UTC)
    scope = scope_fixture(store, count, now, forecast=True)
    manifest = {"scopes": [scope]}

    def reader(artifacts):
        result = object.__new__(FrozenForecastExports)
        result.artifacts, result.style = artifacts, "short"
        result.since, result.reuse_artifacts = None, set()
        return result

    baseline, batched = reader(Single(store)), reader(store)
    old_state, new_state = {"g": 0, "p": 0}, {"g": 0, "p": 0}
    all_keys, pages = [], []
    while True:
        original = baseline._page_items(manifest, old_state, limit, now)
        start = new_state["p"]
        with Reads(store) as reads:
            result = batched._page_items(manifest, new_state, limit, now)
        assert result == original and new_state == old_state
        expected = min(count - start, limit, MAX_PAGE_ARTIFACTS)
        assert len(reads.sizes) == ceil(expected / 8), reads.sizes
        assert sum(reads.sizes) == expected <= MAX_PAGE_ARTIFACTS
        assert set(reads.digests) == {
            ref["artifact_digest"] for ref in scope["refs"][start:start + expected]
        }
        all_keys.extend(item["location_id"] for item in result[0])
        pages.append(dict(start=start, chunks=sum(reads.sizes), payload_selects=len(reads.sizes)))
        if not result[1]:
            break
    assert all_keys == scope["location_ids"]
    print(dict(resource="forecast", refs=count, limit=limit, pages=pages))
    assert store.engine.pool.checkedout() == 0


@pytest.mark.parametrize("limit", [17, 200, 2000])
def test_current_one_scope_refills_from_actual_ref_offset_and_preserves_etag(
    store, monkeypatch, limit,
):
    now = datetime.now(UTC)
    scope = scope_fixture(store, 128, now, forecast=False)
    manifest = {"scopes": [scope], "locations": []}
    frozen = dict(snapshot_id="00000000-0000-4000-8000-000000000001",
                  manifest=manifest, manifest_digest=_digest(manifest),
                  source_collected_at=now, data_revision_at=now, created_at=now,
                  expires_at=now + timedelta(minutes=15))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(export_snapshots, "datetime", Clock)

    def reader(artifacts):
        result = FrozenCurrentExports(store.repository, artifacts, signing_key="refill-test")
        monkeypatch.setattr(result, "_read_cursor_snapshot", lambda snapshot_id: frozen)
        monkeypatch.setattr(result, "_renew_reused", lambda row: row)
        return result

    baseline, batched = reader(Single(store)), reader(store)
    cursor = batched._encode(dict(v=1, s=frozen["snapshot_id"], r="current", l=limit, g=0, p=0))
    all_keys = []
    while cursor:
        original = baseline._page_guarded("current", limit=limit, cursor=cursor)
        with Reads(store) as reads:
            result = batched._page_guarded("current", limit=limit, cursor=cursor)
        assert result == original  # body/ETag/cursor/source_status/as_of/order
        assert len(reads.sizes) == 16, reads.sizes
        assert sum(reads.sizes) == MAX_PAGE_ARTIFACTS
        assert set(reads.digests) == {ref["artifact_digest"] for ref in scope["refs"]}
        all_keys.extend(item["location_id"] for item in result[0]["items"])
        cursor = result[0]["meta"]["next_cursor"]
    assert all_keys == scope["location_ids"]
    assert store.engine.pool.checkedout() == 0


def test_forecast_refills_byte_limited_pages_without_singleton_fallback(store, monkeypatch):
    import hashlib
    import json

    now = datetime.now(UTC)
    points = [{
        "location_id": str(i), "provider": "python-kma-api", "forecast_style": "short",
        "target_at": (now + timedelta(hours=1)).isoformat(), "known_at": now.isoformat(),
        "metric_key": "TMP", "value_number": i, "value_text": None,
        "padding": "x" * (4 * 1024 * 1024),
    } for i in range(3)]
    refs = _put(store, points)
    for i, ref in enumerate(refs):
        ref["location_ids"] = [str(i)]
    scope = dict(location_ids=[str(i) for i in range(3)], refs=refs,
                 published_at=now.isoformat())
    reader = object.__new__(FrozenForecastExports)
    reader.artifacts, reader.style, reader.since = store, "short", None
    reader.reuse_artifacts = set()

    def forbidden(*args, **kwargs):
        pytest.fail("DB refill must stay on the batch path even when the 8MiB cap fits one chunk")

    monkeypatch.setattr(store, "read_shared", forbidden)
    state = {"g": 0, "p": 0}
    all_rows, all_digests = [], []
    for i in range(3):
        with Reads(store) as reads:
            rows, more = reader._page_items({"scopes": [scope]}, state, 200, now)
        assert reads.sizes == [1] and more == (i < 2)
        all_rows.extend(rows)
        all_digests.extend(reads.digests)
    assert [item["location_id"] for item in all_rows] == ["0", "1", "2"]
    assert set(all_digests) == {
        hashlib.sha256((json.dumps(point, sort_keys=True, separators=(",", ":")) + "\n").encode())
        .hexdigest() for point in points
    }


@pytest.mark.parametrize("bad", ["size", "points", "missing"])
def test_refilled_batch_revalidates_tail_receipt_and_presence_on_warm_cache(store, bad):
    now = datetime.now(UTC)
    scope = scope_fixture(store, 10, now, forecast=True)
    store.read_many(scope["refs"][:8])
    store.read_many(scope["refs"][8:])
    ref = scope["refs"][8]
    if bad == "size":
        ref["byte_count"] += 1
    elif bad == "points":
        ref["point_count"] += 1
    else:
        with store.guard(), store.engine.begin() as c:
            c.execute(store.table.delete().where(store.table.c.digest == ref["artifact_digest"]))
    reader = object.__new__(FrozenForecastExports)
    reader.artifacts, reader.style, reader.since = store, "short", None
    reader.reuse_artifacts = set()
    with pytest.raises(ExportArtifactError), Reads(store) as reads:
        reader._page_items({"scopes": [scope]}, {"g": 0, "p": 0}, 200, now)
    assert reads.sizes[0] == 8 and len(reads.sizes) == 2
    assert store.engine.pool.checkedout() == 0
