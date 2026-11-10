"""캐시가 내용 검증을 생략하거나 호출자 변경·이전 roster를 숨기지 않는지 확인한다."""

import hashlib

import pytest

from kortravelweather import export_artifacts, export_inventory_selection
from kortravelweather.export_artifacts import ExportArtifactError, ExportArtifactStore
from kortravelweather.export_external import RetrievalScopeDescriptor
from kortravelweather.metrics import route_label


def test_decoded_artifact_cache_keeps_fd_verification_and_returns_independent_values(
    tmp_path, monkeypatch,
):
    payload = b'{"fixture":"cache-integrity","nested":{"values":[1,2]}}\n'
    digest = hashlib.sha256(payload).hexdigest()
    path = tmp_path / (digest + ".jsonl")
    path.write_bytes(payload)
    store = ExportArtifactStore(tmp_path)
    original = export_artifacts.json.loads
    parses = []

    def counted(value):
        parses.append(value)
        return original(value)

    monkeypatch.setattr(export_artifacts.json, "loads", counted)
    first = list(store.read(digest, byte_count=len(payload), point_count=1))
    first[0]["nested"]["values"].append(999)
    assert list(store.read(digest, byte_count=len(payload), point_count=1))[0]["nested"] == {
        "values": [1, 2],
    }
    assert len(parses) == 1
    # 같은 길이 변조·삭제·symlink 전부 warm 캐시 뒤에도 실패해야 한다.
    path.write_bytes(payload.replace(b"[1,2]", b"[8,9]"))
    with pytest.raises(ExportArtifactError):
        list(store.read(digest, byte_count=len(payload), point_count=1))
    path.unlink()
    with pytest.raises(FileNotFoundError):
        list(store.read(digest, byte_count=len(payload), point_count=1))
    target = tmp_path / "other"
    target.write_bytes(payload)
    path.symlink_to(target)
    with pytest.raises(OSError):
        list(store.read(digest, byte_count=len(payload), point_count=1))


def test_decoded_artifact_cache_has_a_budget_and_still_checks_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(export_artifacts, "_DECODED_ARTIFACT_BYTES", 4)
    payload = b'{"fixture":"over-budget"}\n'
    digest = hashlib.sha256(payload).hexdigest()
    (tmp_path / (digest + ".jsonl")).write_bytes(payload)
    store = ExportArtifactStore(tmp_path)
    assert list(store.read(digest, byte_count=len(payload), point_count=1))
    assert (digest, len(payload), 1) not in export_artifacts._decoded_artifacts
    with pytest.raises(ExportArtifactError):
        list(store.read(digest, byte_count=len(payload), point_count=2))


def test_warm_artifact_cannot_hide_changes_inside_the_same_guard(tmp_path):
    payload = b'{"fixture":"same-guard","value":1}\n'
    digest = hashlib.sha256(payload).hexdigest()
    path = tmp_path / (digest + ".jsonl")
    path.write_bytes(payload)
    store = ExportArtifactStore(tmp_path)
    with store.guard(shared=True):
        assert list(store.read(digest, byte_count=len(payload), point_count=1))[0]["value"] == 1
        path.write_bytes(payload.replace(b'"value":1', b'"value":2'))
        with pytest.raises(ExportArtifactError):
            list(store.read(digest, byte_count=len(payload), point_count=1))


def test_cohort_cache_is_content_addressed_and_bounded(monkeypatch):
    descriptor = RetrievalScopeDescriptor(
        provider="open_meteo", dataset_key="open_meteo_current", entity_id="cache-roster",
        forecast_style="observed", location_ids=("cache-roster",), latitude=37, longitude=127,
        normalizer_revision="open_meteo-v1",
    )
    row = dict(provider="open_meteo", dataset_key="open_meteo_current", targets=[],
               descriptors=[descriptor.model_dump(mode="json")])
    original = export_inventory_selection.parse_scope_descriptor
    parses = []

    def counted(value):
        parses.append(value)
        return original(value)

    monkeypatch.setattr(export_inventory_selection, "parse_scope_descriptor", counted)
    first = export_inventory_selection._cohort_descriptors(row)
    first.clear()
    assert export_inventory_selection._cohort_descriptors(row) == [descriptor]
    assert len(parses) == 1
    # run_id/updated_at이 같아도 실제 내용 변화는 다른 키여야 한다.
    row["descriptors"][0]["latitude"] = 38
    assert export_inventory_selection._cohort_descriptors(row)[0].latitude == 38
    row["descriptors"][0]["latitude"] = 200
    with pytest.raises(ValueError):
        export_inventory_selection._cohort_descriptors(row)
    monkeypatch.setattr(export_inventory_selection, "_COHORT_CACHE_BYTES", 1)
    row["descriptors"][0]["latitude"] = 39
    assert export_inventory_selection._cohort_descriptors(row)[0].latitude == 39
    assert export_inventory_selection._cohort_cache_bytes <= 1


def test_export_metric_routes_are_distinct_and_query_values_are_not_labels():
    for resource in ("locations", "current", "forecasts", "daily-summaries"):
        route = "/v1/service/exports/" + resource
        assert route_label(route) == route
        assert route_label(route + "?cursor=private") == "unmatched"


def _artifact(tmp_path, body: bytes):
    digest = hashlib.sha256(body).hexdigest()
    path = tmp_path / (digest + ".jsonl")
    path.write_bytes(body)
    return digest, path


def _cache_counts():
    from kortravelweather.metrics import EXPORT_ARTIFACT_CACHE

    return {
        sample.labels["outcome"]: sample.value
        for metric in EXPORT_ARTIFACT_CACHE.collect() for sample in metric.samples
        if sample.name.endswith("_total")
    }


def test_warm_hit_does_not_read_or_hash_and_is_counted(tmp_path, monkeypatch):
    """같은 파일(identity 동일)의 두 번째 읽기는 파일을 열지도 hash하지도 않는다(2026-11-10).

    odroid current snapshot은 3,302개 파일이고 전체를 cold로 읽고 hash하는 데 149초였다.
    """
    digest, _path = _artifact(tmp_path, b'{"fixture":"warm-hit","value":1}\n')
    store = ExportArtifactStore(tmp_path)
    reads = []
    original = ExportArtifactStore._read_lines

    def counted(self, *args, **kwargs):
        reads.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ExportArtifactStore, "_read_lines", counted)
    before = _cache_counts()
    size = len(b'{"fixture":"warm-hit","value":1}\n')
    assert store.read_shared(digest, byte_count=size, point_count=1)[0]["value"] == 1
    # 공유 guard 없이도 hit는 성립한다(파일을 읽지 않으므로).
    monkeypatch.setattr(ExportArtifactStore, "guard", lambda *a, **k: pytest.fail("guard"))
    assert store.read_shared(digest, byte_count=size, point_count=1)[0]["value"] == 1
    assert list(store.read(digest, byte_count=size, point_count=1))[0]["value"] == 1
    assert reads == [1]
    after = _cache_counts()
    assert after.get("miss", 0) - before.get("miss", 0) == 1
    assert after.get("hit", 0) - before.get("hit", 0) == 2


def test_corruption_after_warm_hit_is_detected_even_if_mtime_is_restored(tmp_path):
    """같은 길이 변조 뒤 mtime을 되돌려도 ctime이 바뀌므로 다시 hash해 실패한다."""
    import os

    body = b'{"fixture":"restored-mtime","value":1}\n'
    digest, path = _artifact(tmp_path, body)
    store = ExportArtifactStore(tmp_path)
    assert list(store.read(digest, byte_count=len(body), point_count=1))
    before = os.stat(path)
    path.chmod(0o600)
    path.write_bytes(body.replace(b'"value":1', b'"value":2'))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ExportArtifactError):
        store.read_shared(digest, byte_count=len(body), point_count=1)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        store.read_shared(digest, byte_count=len(body), point_count=1)


def test_replaced_file_with_same_content_is_reverified_not_trusted(tmp_path, monkeypatch):
    """교체(새 inode)는 hit가 아니다. 같은 내용이면 다시 hash해 통과한다."""
    import os

    body = b'{"fixture":"replaced","value":1}\n'
    digest, path = _artifact(tmp_path, body)
    store = ExportArtifactStore(tmp_path)
    assert list(store.read(digest, byte_count=len(body), point_count=1))
    fresh = tmp_path / "fresh"
    fresh.write_bytes(body)
    os.replace(fresh, path)
    reads = []
    original = ExportArtifactStore._read_lines

    def counted(self, *args, **kwargs):
        reads.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ExportArtifactStore, "_read_lines", counted)
    assert store.read_shared(digest, byte_count=len(body), point_count=1)[0]["value"] == 1
    assert reads == [1]


def test_cache_eviction_is_bounded_and_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(export_artifacts, "_DECODED_ARTIFACT_ENTRIES", 2)
    before = _cache_counts()
    store = ExportArtifactStore(tmp_path)
    for index in range(4):
        body = f'{{"fixture":"evict","value":{index}}}\n'.encode()
        digest, _path = _artifact(tmp_path, body)
        assert list(store.read(digest, byte_count=len(body), point_count=1))
    assert len(export_artifacts._decoded_artifacts) <= 2
    assert _cache_counts().get("evict", 0) - before.get("evict", 0) >= 2
