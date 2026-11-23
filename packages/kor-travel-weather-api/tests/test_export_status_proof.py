"""current cursor 순회는 snapshot 하나의 상태 판정을 승인 수명 안에서 한 번만 계산한다."""

import pytest
from kortravelweather_dagster.kma_weather import KmaAllGridsFailedError
from test_all_source_exports import _selection_clock
from test_frozen_service_exports import HEADERS, _client, _collect
from test_frozen_service_exports import repository as repository

from kortravelweather import export_snapshots
from kortravelweather.export_selection_cache import ExportSelectionCache
from kortravelweather.export_snapshots import FrozenCurrentExports

URL = "/v1/service/exports/current"


def _count_state_checks(monkeypatch):
    calls = []
    original = FrozenCurrentExports._compute_state_stale

    def counted(self, *args, **kwargs):
        calls.append(kwargs.get("selection") is not None)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(FrozenCurrentExports, "_compute_state_stale", counted)
    return calls


def _walk(client, first):
    pages = [first]
    while pages[-1].json()["meta"]["next_cursor"]:
        page = client.get(URL, params={
            "limit": 1, "cursor": pages[-1].json()["meta"]["next_cursor"],
        }, headers=HEADERS)
        assert page.status_code == 200, page.text
        pages.append(page)
    return pages


def test_cursor_walk_computes_the_state_once_in_the_freeze_transaction(
    repository, tmp_path, monkeypatch,
):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    calls = _count_state_checks(monkeypatch)
    first = client.get(URL, params={"limit": 1}, headers=HEADERS)
    assert first.status_code == 200 and first.json()["meta"]["source_status"] == "ok"
    assert first.headers["x-export-status-proof"] == "frozen"
    pages = _walk(client, first)
    assert len(pages) == 24  # 3위치 × 8지표, limit 1
    assert {page.json()["meta"]["snapshot_id"] for page in pages} == {
        first.json()["meta"]["snapshot_id"]
    }
    assert {page.json()["meta"]["source_status"] for page in pages} == {"ok"}
    assert {page.headers["x-export-status-proof"] for page in pages[1:]} == {"reused"}
    # 첫 페이지 freeze RR의 판정 한 번뿐이다(승인 선택을 같은 RR에서 그대로 썼다).
    assert calls == [True]


def test_failed_collection_reaches_cursor_pages_within_the_approved_sixty_seconds(
    repository, tmp_path, monkeypatch,
):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    clock = _selection_clock(client, monkeypatch)
    first = client.get(URL, params={"limit": 1}, headers=HEADERS)
    second = client.get(URL, params={
        "limit": 1, "cursor": first.json()["meta"]["next_cursor"],
    }, headers=HEADERS)
    assert second.json()["meta"]["source_status"] == "ok"
    with pytest.raises(KmaAllGridsFailedError):
        _collect(repository, tmp_path, failed=True)
    # 수집 실패는 삭제가 아니다. 소유자가 허용한 최대 60초 안에서는 같은 판정을 잇는다.
    clock[0] += 49
    third = client.get(URL, params={
        "limit": 1, "cursor": second.json()["meta"]["next_cursor"],
    }, headers=HEADERS)
    assert third.status_code == 200
    assert third.headers["x-export-status-proof"] == "reused"
    assert third.json()["meta"]["source_status"] == "ok"
    # 남은 수명이 STATUS_PROOF_MIN_REMAINING보다 짧으면 다시 계산해 실패를 드러낸다.
    clock[0] += 11
    fourth = client.get(URL, params={
        "limit": 1, "cursor": third.json()["meta"]["next_cursor"],
    }, headers=HEADERS)
    assert fourth.status_code == 200, fourth.text
    assert fourth.headers["x-export-status-proof"] == "computed"
    assert fourth.json()["meta"]["source_status"] == "stale"
    assert fourth.json()["meta"]["snapshot_id"] == first.json()["meta"]["snapshot_id"]
    assert fourth.headers["etag"] != third.headers["etag"]


def test_head_delete_changes_the_epoch_and_is_reflected_on_the_next_cursor_page(
    repository, tmp_path, monkeypatch,
):
    from kortravelweather.export_heads import ExportHeads

    if repository.engine.dialect.name != "postgresql":
        pytest.skip("삭제 변경 번호는 PostgreSQL 전용이다.")
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    calls = _count_state_checks(monkeypatch)
    first = client.get(URL, params={"limit": 1}, headers=HEADERS)
    with repository.engine.begin() as connection:
        connection.execute(ExportHeads(repository).heads.delete())
    following = client.get(URL, params={
        "limit": 1, "cursor": first.json()["meta"]["next_cursor"],
    }, headers=HEADERS)
    assert following.status_code == 200
    assert following.headers["x-export-status-proof"] == "computed"
    assert following.json()["meta"]["source_status"] == "stale"
    assert calls == [True, False]


def test_state_proof_is_bound_to_the_snapshot_and_the_reader_selection(
    repository, tmp_path, monkeypatch,
):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    first = client.get(URL, params={"limit": 1}, headers=HEADERS)
    cache = client.app.state.export_selection_cache
    keys = list(cache.status_proofs)
    assert len(keys) == 1
    engine_id, signature, snapshot_id, digest = keys[0]
    assert engine_id == id(repository.engine)
    assert snapshot_id == first.json()["meta"]["snapshot_id"]
    assert signature[0] == "FrozenCurrentExports" and signature[2] is True
    assert len(digest) == 64


def test_current_page_scope_cap_is_separate_from_the_forecast_cap(
    repository, tmp_path, monkeypatch,
):
    assert export_snapshots.MAX_CURRENT_PAGE_SCOPES == 512
    assert export_snapshots.MAX_CURRENT_PAGE_ARTIFACTS == 512
    assert export_snapshots.MAX_PAGE_SCOPES == export_snapshots.MAX_PAGE_ARTIFACTS == 128
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    whole = client.get(URL, params={"limit": 5000}, headers=HEADERS).json()
    assert len(whole["items"]) == 24 and whole["meta"]["next_cursor"] is None
    monkeypatch.setattr(export_snapshots, "MAX_CURRENT_PAGE_SCOPES", 1)
    first = client.get(URL, params={"limit": 4999}, headers=HEADERS).json()
    assert first["meta"]["has_more"] is True
    following = client.get(URL, params={
        "limit": 4999, "cursor": first["meta"]["next_cursor"],
    }, headers=HEADERS).json()
    assert following["meta"]["next_cursor"] is None
    assert sorted(
        (row["location_id"], row["metric_key"]) for row in first["items"] + following["items"]
    ) == sorted((row["location_id"], row["metric_key"]) for row in whole["items"])


def test_status_proof_cache_needs_same_epoch_and_enough_remaining_lifetime():
    clock = [100.0]
    cache = ExportSelectionCache(clock=lambda: clock[0])
    cache.put_status("k", True, 160.0, (1, 1, 1))
    assert cache.read_status("k", None) is None
    assert cache.read_status("k", (1, 1, 2)) is None
    assert cache.read_status("k", (1, 1, 1)) == (True, 160.0)
    assert cache.read_status("k", (1, 1, 1), min_remaining=61) is None
    clock[0] = 160.0
    assert cache.read_status("k", (1, 1, 1)) is None and "k" not in cache.status_proofs
    cache.put_status("late", False, 150.0, (1, 1, 1))  # 이미 끝난 수명은 남기지 않는다.
    assert "late" not in cache.status_proofs
    cache.put_status("none", False, 999.0, None)  # epoch 없는 판정은 잇지 않는다.
    assert "none" not in cache.status_proofs
    for index in range(100):
        cache.put_status(index, False, 999.0, (1, 1, 1))
    assert len(cache.status_proofs) == 64


def test_current_window_reads_small_artifacts_in_wider_batches_within_the_byte_cap():
    from kortravelweather.export_database_artifacts import (
        MAX_ARTIFACT_READ_BATCH,
        MAX_ARTIFACT_READ_BATCH_LIMIT,
        MAX_ARTIFACT_READ_BYTES,
        DatabaseExportArtifactStore,
    )
    from kortravelweather.export_read_window import ArtifactReadWindow

    assert export_snapshots.CURRENT_ARTIFACT_READ_BATCH == MAX_ARTIFACT_READ_BATCH_LIMIT == 64
    captured = []

    class Bulk:
        def read_many(self, refs, **options):
            captured.append((len(refs), options))
            return {ref["artifact_digest"]: [] for ref in refs}

    refs = [dict(artifact_digest=f"{i:064x}", byte_count=100, point_count=1)
            for i in range(200)]
    wide = ArtifactReadWindow(Bulk(), batch=64)
    wide.prepare(refs[0], lambda: iter(refs), remaining_reads=512)
    narrow = ArtifactReadWindow(Bulk())
    narrow.prepare(refs[0], lambda: iter(refs), remaining_reads=512)
    # 기본 창(예보·중기)은 예전 호출 모양 그대로다.
    assert captured == [(64, {"max_batch": 64}), (MAX_ARTIFACT_READ_BATCH, {})]
    big = [dict(artifact_digest=f"{i:064x}", byte_count=MAX_ARTIFACT_READ_BYTES // 2 + 1,
                point_count=1) for i in range(3)]
    captured.clear()
    ArtifactReadWindow(Bulk(), batch=64).prepare(big[0], lambda: iter(big), remaining_reads=512)
    assert captured[0][0] == 1  # byte 상한은 개수와 무관하게 그대로다.
    store = object.__new__(DatabaseExportArtifactStore)
    for invalid in (0, MAX_ARTIFACT_READ_BATCH_LIMIT + 1):
        with pytest.raises(Exception, match="batch"):
            store.read_many(refs[:1], max_batch=invalid)
    with pytest.raises(Exception, match="batch"):
        store.read_many(refs[:MAX_ARTIFACT_READ_BATCH + 1])
