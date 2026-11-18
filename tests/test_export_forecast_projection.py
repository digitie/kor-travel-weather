"""예보 필드 복사는 줄이되 chunk 전체 검산·응답·cursor 계약은 보존한다."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from test_export_database_read_window import store as store

from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore
from kortravelweather.export_forecasts import FrozenForecastExports
from kortravelweather.export_snapshots import ExportSnapshotUnavailable

FIELDS = (
    "provider", "forecast_style", "location_id", "target_at",
    "metric_key", "value_number", "value_text", "known_at",
)


def put_chunk(store, points):
    payload = b"".join(
        (json.dumps(point, sort_keys=True, separators=(",", ":")) + "\n").encode()
        for point in points
    )
    digest = hashlib.sha256(payload).hexdigest()
    with store.engine.begin() as connection:
        connection.execute(store.table.insert().values(
            digest=digest, payload=payload, byte_count=len(payload),
            point_count=len(points), created_at=datetime.now(UTC),
        ))
    return dict(artifact_digest=digest, byte_count=len(payload), point_count=len(points))


@pytest.mark.parametrize("warm", [False, True])
def test_projection_preserves_full_cache_and_selected_mutable_isolation(store, warm):
    points = [{"value_text": {"nested": [1]}, "observation_evidence": {"nested": [2]}},
              {"value_number": 3}]
    ref = put_chunk(store, points)
    if warm:
        assert store.read_many([ref])[ref["artifact_digest"]] == points
    projected = store.read_many([ref], fields=FIELDS)[ref["artifact_digest"]]
    assert projected == [{"value_text": {"nested": [1]}}, {"value_number": 3}]
    projected[0]["value_text"]["nested"].append("changed")
    assert store.read_many([ref])[ref["artifact_digest"]] == points
    assert store.read_many([ref], fields=FIELDS)[ref["artifact_digest"]] == [
        {"value_text": {"nested": [1]}}, {"value_number": 3},
    ]


@pytest.mark.parametrize("bad", ["hash", "line_count", "non_object", "unused_json"])
def test_projection_rejects_corrupt_tail_before_selecting_fields(bad):
    engine = create_engine("sqlite://")
    instance = DatabaseExportArtifactStore(SimpleNamespace(engine=engine))
    instance.table.create(engine)
    payload = b'{"value_number":1}\n{"ignored":2}\n'
    if bad == "non_object":
        payload = b'{"value_number":1}\n[2]\n'
    elif bad == "unused_json":
        payload = b'{"value_number":1}\n{"ignored":not_json}\n'
    digest = hashlib.sha256(payload).hexdigest()
    stored = payload.replace(b"2", b"3") if bad == "hash" else payload
    count = 3 if bad == "line_count" else 2
    with engine.begin() as connection:
        connection.execute(instance.table.insert().values(
            digest=digest, payload=stored, byte_count=len(stored), point_count=count,
            created_at=datetime.now(UTC),
        ))
    try:
        with pytest.raises((ExportArtifactError, ValueError)):
            instance.read_many([dict(
                artifact_digest=digest, byte_count=len(stored), point_count=count,
            )], fields=FIELDS)
    finally:
        engine.dispose()


class FullReads:
    """같은 DB batch를 쓰되 projection을 지정하지 않는 기존 읽기 계약."""

    def __init__(self, store):
        self.store = store

    def read_many(self, refs):
        return self.store.read_many(refs)

    def read_shared(self, *args, **kwargs):
        return self.store.read_shared(*args, **kwargs)


def reader(artifacts):
    result = object.__new__(FrozenForecastExports)
    result.artifacts, result.style = artifacts, "short"
    result.since, result.reuse_artifacts = None, set()
    result._expired_omitted = False
    return result


def fixture_points(now, location):
    def point(hours, metric, number, text=None):
        return dict(
            provider="python-kma-api", forecast_style="short", location_id=location,
            target_at=(now + timedelta(hours=hours)).isoformat(),
            known_at=(now + timedelta(minutes=-hours)).isoformat(), metric_key=metric,
            value_number=number, value_text=text,
            observation_evidence={"category": metric, "unused": [1, {"x": 2}]},
        )
    if location == "b":
        return [point(-1, "TMP", 1)]
    return [point(2, "TMP", 2), point(1, "TXT", None, "clear"),
            point(1, "TMP", 1), point(1, "TMP", 9), point(-1, "TMP", -1)]


@pytest.mark.parametrize("limit", [1, 200, 2000])
def test_forecast_projection_preserves_duplicates_order_expiry_and_cursor(store, limit):
    now = datetime.now(UTC)
    refs = [dict(put_chunk(store, fixture_points(now, key)), location_ids=[key])
            for key in ("a", "b", "c")]
    manifest = {"scopes": [dict(
        location_ids=["a", "b", "c"], refs=refs, generation_id="g", namespace="n",
        published_at=now.isoformat(),
    )]}
    full, projected = reader(FullReads(store)), reader(store)
    old_state, new_state = {"g": 0, "p": 0}, {"g": 0, "p": 0}
    output = []
    for _ in range(4):
        expected = full._page_items(manifest, old_state, limit, now)
        actual = projected._page_items(manifest, new_state, limit, now)
        assert actual == expected
        assert new_state == old_state
        assert projected.reuse_artifacts == full.reuse_artifacts
        assert projected._expired_omitted == full._expired_omitted
        output.extend(actual[0])
        if not actual[1]:
            break
    else:
        pytest.fail("cursor did not finish")
    assert [item["location_id"] for item in output] == ["a", "c"]
    assert output[0]["metrics"] == {"TMP": [9.0, 2.0], "TXT": ["clear", None]}
    assert output[0]["known_at"] == (now + timedelta(minutes=1)).isoformat()
    assert projected._expired_omitted


@pytest.mark.parametrize("field,value", [
    ("provider", "foreign"), ("forecast_style", "ultra_short"), ("location_id", "foreign"),
])
def test_projection_still_validates_expired_tail_scope(store, field, value):
    now = datetime.now(UTC)
    points = fixture_points(now, "a")
    points[-1][field] = value  # 과거 target도 index 대조에서 제외하지 않는다.
    ref = dict(put_chunk(store, points), location_ids=["a"])
    manifest = {"scopes": [dict(
        location_ids=["a"], refs=[ref], generation_id="g", namespace="n",
        published_at=now.isoformat(),
    )]}
    with pytest.raises(ExportSnapshotUnavailable):
        reader(store)._page_items(manifest, {"g": 0, "p": 0}, 200, now)
