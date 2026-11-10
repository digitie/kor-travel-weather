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
    from kortravelweather import metrics

    counter = getattr(metrics, "EXPORT_ARTIFACT_CACHE", None)
    if counter is None:
        return {}
    return {
        sample.labels["outcome"]: sample.value
        for metric in counter.collect() for sample in metric.samples
        if sample.name.endswith("_total")
    }


class _Counted:
    """export_artifacts 모듈의 os.open·hashlib.sha256 호출을 센다(기존 코드에도 있는 경로)."""

    def __init__(self, monkeypatch):
        self.opens, self.hashes = 0, 0
        real_open, real_sha = export_artifacts.os.open, export_artifacts.hashlib.sha256

        def opened(*args, **kwargs):
            self.opens += 1
            return real_open(*args, **kwargs)

        def hashed(*args, **kwargs):
            self.hashes += 1
            return real_sha(*args, **kwargs)

        monkeypatch.setattr(export_artifacts.os, "open", opened)
        monkeypatch.setattr(export_artifacts.hashlib, "sha256", hashed)


def test_warm_hit_does_not_open_hash_or_guard_and_is_counted(tmp_path, monkeypatch):
    """검증한 같은 파일의 두 번째 읽기는 열지도, hash하지도, 공유 guard를 잡지도 않는다.

    odroid current snapshot은 3,302개 파일이고 전체를 cold로 읽고 hash하는 데 149초였다.
    """
    body = b'{"fixture":"warm-hit","value":1}
'
    digest, _path = _artifact(tmp_path, body)
    monkeypatch.setattr(export_artifacts, "_RACY_IDENTITY_NS", 0)  # 방금 쓴 파일도 믿는다
    store = ExportArtifactStore(tmp_path)
    before = _cache_counts()
    assert store.read_shared(digest, byte_count=len(body), point_count=1)[0]["value"] == 1
    counted = _Counted(monkeypatch)
    guards = []
    original_guard = ExportArtifactStore.guard

    def counted_guard(self, *args, **kwargs):
        guards.append(kwargs)
        return original_guard(self, *args, **kwargs)

    monkeypatch.setattr(ExportArtifactStore, "guard", counted_guard)
    assert store.read_shared(digest, byte_count=len(body), point_count=1)[0]["value"] == 1
    assert list(store.read(digest, byte_count=len(body), point_count=1))[0]["value"] == 1
    assert (counted.opens, counted.hashes, guards) == (0, 0, [])
    after = _cache_counts()
    assert after.get("miss", 0) - before.get("miss", 0) == 1
    assert after.get("hit", 0) - before.get("hit", 0) == 2


def test_recently_written_file_is_never_trusted_so_corruption_is_caught(tmp_path):
    """방금(2초 이내) 바뀐 파일은 identity를 믿지 않는다. 같은 tick의 같은 길이 변조도 잡는다."""
    import os

    body = b'{"fixture":"racy","value":1}
'
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


def test_old_hit_is_rehashed_so_metadata_preserving_corruption_is_found(tmp_path, monkeypatch):
    """metadata를 바꾸지 않는 손상(비트 손상)은 identity로 못 본다. 10분 뒤 다시 hash한다."""
    body = b'{"fixture":"aged","value":1}
'
    digest, _path = _artifact(tmp_path, body)
    monkeypatch.setattr(export_artifacts, "_RACY_IDENTITY_NS", 0)
    clock = [1000.0]
    monkeypatch.setattr(export_artifacts.time, "monotonic", lambda: clock[0])
    store = ExportArtifactStore(tmp_path)
    assert store.read_shared(digest, byte_count=len(body), point_count=1)
    counted = _Counted(monkeypatch)
    clock[0] += 599
    assert store.read_shared(digest, byte_count=len(body), point_count=1)
    assert counted.hashes == 0
    clock[0] += 2
    assert store.read_shared(digest, byte_count=len(body), point_count=1)
    assert counted.hashes == 1


def test_verification_scope_always_hashes_the_real_file(tmp_path, monkeypatch):
    """승격 검산처럼 verifying/guard 안의 읽기는 캐시를 쓰지 않고 실제 파일을 hash한다."""
    body = b'{"fixture":"verifying","value":1}
'
    digest, _path = _artifact(tmp_path, body)
    monkeypatch.setattr(export_artifacts, "_RACY_IDENTITY_NS", 0)
    store = ExportArtifactStore(tmp_path)
    assert store.read_shared(digest, byte_count=len(body), point_count=1)
    counted = _Counted(monkeypatch)
    with store.verifying():
        assert list(store.read(digest, byte_count=len(body), point_count=1))
        assert list(store.read(digest, byte_count=len(body), point_count=1))
    assert counted.hashes == 1  # scope 안에서는 실제로 한 번 hash(두 번째는 scope 중복 제거)


def test_replaced_file_with_same_content_is_reverified_not_trusted(tmp_path, monkeypatch):
    """교체(새 inode)는 hit가 아니다. 같은 내용이면 다시 hash해 통과한다."""
    import os

    body = b'{"fixture":"replaced","value":1}
'
    digest, path = _artifact(tmp_path, body)
    monkeypatch.setattr(export_artifacts, "_RACY_IDENTITY_NS", 0)
    store = ExportArtifactStore(tmp_path)
    assert list(store.read(digest, byte_count=len(body), point_count=1))
    fresh = tmp_path / "fresh"
    fresh.write_bytes(body)
    os.replace(fresh, path)
    counted = _Counted(monkeypatch)
    assert store.read_shared(digest, byte_count=len(body), point_count=1)[0]["value"] == 1
    assert counted.hashes == 1


def test_cache_eviction_is_bounded_and_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(export_artifacts, "_DECODED_ARTIFACT_ENTRIES", 2)
    before = _cache_counts()
    store = ExportArtifactStore(tmp_path)
    for index in range(4):
        body = f'{{"fixture":"evict","value":{index}}}
'.encode()
        digest, _path = _artifact(tmp_path, body)
        assert list(store.read(digest, byte_count=len(body), point_count=1))
    assert len(export_artifacts._decoded_artifacts) <= 2
    assert _cache_counts().get("evict", 0) - before.get("evict", 0) >= 2
