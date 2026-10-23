"""새 manifest 예산 초과는 마지막 승인 vector를 오염시키지 않는다."""

import pytest
from sqlalchemy import func, select
from test_frozen_service_exports import HEADERS, _client, _collect, _collect_forecast
from test_frozen_service_exports import repository as repository

from kortravelweather import export_forecasts, export_snapshots
from kortravelweather.export_capacity import MetadataBudget
from kortravelweather.repository import Base


@pytest.mark.parametrize("kind", ["current", "forecasts"])
def test_manifest_overflow_keeps_previous_vector_stale_without_new_snapshot(
    repository, tmp_path, monkeypatch, kind,
):
    (_collect if kind == "current" else _collect_forecast)(repository, tmp_path)
    client = _client(repository, tmp_path)
    url = "/v1/service/exports/" + kind
    params = {} if kind == "current" else {"style": "ultra_short"}
    first = client.get(url, params={**params, "limit": 1}, headers=HEADERS)
    assert first.status_code == 200 and first.json()["items"]
    snapshots = export_snapshots.export_snapshot_tables(Base.metadata)[0]
    with repository.engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(snapshots))
    module = export_snapshots if kind == "current" else export_forecasts
    calls = 0

    def limited_budget(*args):
        nonlocal calls
        calls += 1
        # 새 freeze의 scopes만 거절한다. 보존된 작은 manifest의 복제는 검증한다.
        return MetadataBudget(1) if not args else MetadataBudget(*args)

    monkeypatch.setattr(module, "MetadataBudget", limited_budget)
    # current의 fallback clone은 같은 module 함수를 사용하므로 정상 예산으로 검증한다.
    original_fallback = export_snapshots.FrozenCurrentExports._capacity_fallback

    def fallback(reader, frozen):
        with monkeypatch.context() as patch:
            patch.setattr(export_snapshots, "MetadataBudget", MetadataBudget)
            return original_fallback(reader, frozen)

    monkeypatch.setattr(export_snapshots.FrozenCurrentExports, "_capacity_fallback", fallback)
    retained = client.get(url, params={**params, "limit": 1}, headers={
        **HEADERS, "if-none-match": first.headers["etag"],
    })
    assert retained.status_code == 200
    assert retained.json()["items"] == first.json()["items"]
    assert retained.json()["meta"]["source_status"] == "stale"
    cursor = retained.json()["meta"]["next_cursor"]
    assert cursor
    continued = client.get(url, params={**params, "limit": 1, "cursor": cursor}, headers=HEADERS)
    assert continued.status_code == 200 and continued.json()["meta"]["source_status"] == "stale"
    repeated = client.get(url, params={**params, "limit": 1}, headers=HEADERS)
    assert repeated.json()["meta"]["snapshot_id"] == retained.json()["meta"]["snapshot_id"]
    with repository.engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(snapshots)) == before + 1
    monkeypatch.undo()
    recovered = client.get(url, params={**params, "limit": 1}, headers={
        **HEADERS, "if-none-match": retained.headers["etag"],
    })
    assert recovered.status_code == 200 and recovered.json()["meta"]["source_status"] == "ok"


@pytest.mark.parametrize("kind", ["current", "forecasts"])
def test_manifest_overflow_without_previous_is_503(repository, tmp_path, monkeypatch, kind):
    (_collect if kind == "current" else _collect_forecast)(repository, tmp_path)
    module = export_snapshots if kind == "current" else export_forecasts
    monkeypatch.setattr(module, "MetadataBudget", lambda *args: MetadataBudget(1))
    response = _client(repository, tmp_path).get(
        "/v1/service/exports/" + kind,
        params={} if kind == "current" else {"style": "ultra_short"}, headers=HEADERS,
    )
    assert response.status_code == 503
