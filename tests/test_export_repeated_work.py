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
