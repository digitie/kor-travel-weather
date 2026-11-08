"""인증 뒤 상태 캐시, 만료된 200/304 차단과 갱신 경합의 HTTP 계약."""

from time import monotonic

from test_frozen_service_exports import HEADERS, _client, _collect
from test_frozen_service_exports import repository as repository

from kortravelweather import export_inventory_selection, export_snapshots
from kortravelweather.export_artifacts import ExportGuardTimeout


def test_worker_queue_before_read_does_not_spend_state_lease(repository, tmp_path, monkeypatch):
    from kortravelweather_api.routers import service_exports

    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    clock = [monotonic()]
    monkeypatch.setattr(export_snapshots, "monotonic", lambda: clock[0])
    client.app.state.export_selection_cache.clock = lambda: clock[0]
    original = service_exports._render_page

    def before_read(*args, **kwargs):
        # worker 시작 전 대기와 같은 시간 차이다. 아직 승인/상태 조회는 실행하지 않았다.
        clock[0] += 61
        return original(*args, **kwargs)

    monkeypatch.setattr(service_exports, "_render_page", before_read)
    response = client.get("/v1/service/exports/current?limit=1", headers=HEADERS)
    assert response.status_code == 200 and response.headers["x-export-read"] == "generated"


def test_expiring_during_render_never_returns_200_or_304(repository, tmp_path, monkeypatch):
    from kortravelweather_api.routers import service_exports

    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    clock = [monotonic()]
    monkeypatch.setattr(export_snapshots, "monotonic", lambda: clock[0])
    client.app.state.export_selection_cache.clock = lambda: clock[0]
    url = "/v1/service/exports/current"
    first = client.get(url, headers=HEADERS)
    assert first.status_code == 200
    collected = first.json()["meta"]["source_collected_at"]
    original = service_exports._render_page

    def late(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] += 60
        return result

    monkeypatch.setattr(service_exports, "_render_page", late)
    for headers in (HEADERS, {**HEADERS, "if-none-match": first.headers["etag"]}):
        response = client.get(url, headers=headers)
        assert response.status_code == 503
        assert response.json()["code"] == "EXPORT_TEMPORARILY_UNAVAILABLE"
        assert response.headers["retry-after"] == "5"
        assert "etag" not in response.headers
    monkeypatch.setattr(service_exports, "_render_page", original)
    refreshed = client.get(url, headers=HEADERS)
    assert refreshed.status_code == 200
    assert refreshed.json()["meta"]["source_collected_at"] == collected


def test_expired_cache_refresh_error_never_falls_back_to_cached_ok(
    repository, tmp_path, monkeypatch
):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    clock = [monotonic()]
    monkeypatch.setattr(export_snapshots, "monotonic", lambda: clock[0])
    client.app.state.export_selection_cache.clock = lambda: clock[0]
    url = "/v1/service/exports/current"
    first = client.get(url, headers=HEADERS)
    assert first.status_code == 200
    clock[0] += 60

    def contention(*args, **kwargs):
        raise ExportGuardTimeout("검증용 갱신 경합")

    monkeypatch.setattr(export_inventory_selection, "approved_source_selection", contention)
    response = client.get(url, headers={**HEADERS, "if-none-match": first.headers["etag"]})
    assert response.status_code == 503
    assert response.json()["code"] == "EXPORT_TEMPORARILY_UNAVAILABLE"
    assert "etag" not in response.headers


def test_warm_selection_does_not_hide_lost_immutable_plan(repository, tmp_path):
    from sqlalchemy import delete, select

    from kortravelweather.export_heads import ExportHeads

    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    url = "/v1/service/exports/current"
    first = client.get(url, headers=HEADERS)
    assert first.status_code == 200 and first.json()["meta"]["source_status"] == "ok"
    assert client.app.state.export_page_cache.entries
    heads = ExportHeads(repository)
    with repository.engine.begin() as c:
        attempt_id = c.scalar(select(heads.generations.c.attempt_id).limit(1))
        c.execute(delete(heads.plans).where(heads.plans.c.attempt_id == attempt_id))
    current = client.get(url, headers={**HEADERS, "if-none-match": first.headers["etag"]})
    assert current.status_code == 503 or (
        current.status_code == 200 and current.json()["meta"]["source_status"] == "stale"
    )
