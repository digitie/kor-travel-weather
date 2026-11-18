"""느린 첫 예보의 완성 본문은 fresh proof 뒤 재사용하며 business 경계는 늘리지 않는다."""

from datetime import UTC, datetime, timedelta

import pytest
from test_frozen_service_exports import HEADERS, _client, _collect_forecast
from test_frozen_service_exports import repository as repository

from kortravelweather import export_forecasts, export_inventory_selection, export_snapshots
from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore
from kortravelweather.export_forecasts import FrozenForecastExports


@pytest.mark.parametrize("build_seconds,change_source", [(59, False), (65, False), (65, True)])
def test_slow_first_forecast_reuses_same_handle_etag_without_payload_after_proof(
    repository, tmp_path, monkeypatch, build_seconds, change_source,
):
    _collect_forecast(repository, tmp_path)
    client = _client(repository, tmp_path)
    # 유효한 첫 시간대 capture는 유지하고, 그 target 뒤에서 TTL만 검산한다.
    # 다음 미래 target까지 최소55분이 남아 완료+60초와 실제 시간 경계가 섞이지 않는다.
    origin = (datetime.now(UTC) + timedelta(hours=1)).replace(minute=5, second=0, microsecond=0)
    clock = [0.0]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = origin + timedelta(seconds=clock[0])
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    for module in (export_forecasts, export_snapshots, export_inventory_selection):
        monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(export_snapshots, "monotonic", lambda: clock[0])
    monkeypatch.setattr(export_forecasts, "monotonic", lambda: clock[0], raising=False)
    for name in ("export_page_cache", "export_selection_cache", "export_snapshot_cache"):
        getattr(client.app.state, name).clock = lambda: clock[0]
    page_items, freeze = FrozenForecastExports._page_items, FrozenForecastExports._freeze_forecasts
    builds, checking = [], [False]

    def slow_build(self, *args):
        builds.append(1)
        result = page_items(self, *args)
        clock[0] += build_seconds
        if change_source:
            _collect_forecast(repository, tmp_path)
        return result

    def slow_metadata(self):
        if checking[0]:
            clock[0] += 5  # the proof crosses the original request-start +60 deadline
        return freeze(self)

    monkeypatch.setattr(FrozenForecastExports, "_page_items", slow_build)
    monkeypatch.setattr(FrozenForecastExports, "_freeze_forecasts", slow_metadata)
    path = "/v1/service/exports/forecasts?style=ultra_short&limit=1"
    first = client.get(path, headers=HEADERS)
    if change_source:
        assert first.status_code == 503 and "etag" not in first.headers
        assert not client.app.state.export_page_cache.entries
        assert repository.engine.pool.checkedout() == 0
        return
    assert first.status_code == 200, first.text
    assert first.headers["x-export-read"] == "generated"
    entry = next(iter(client.app.state.export_page_cache.entries.values()))
    completed_at = clock[0]

    def no_payload(*args, **kwargs):
        pytest.fail("same-handle conditional proof re-read an artifact payload")

    monkeypatch.setattr(DatabaseExportArtifactStore, "_payload", no_payload)
    monkeypatch.setattr(DatabaseExportArtifactStore, "read_many", no_payload)
    checking[0] = True
    response = client.get(path, headers={**HEADERS, "if-none-match": first.headers["etag"]})
    assert response.status_code == 304 and not response.content
    assert response.headers["etag"] == first.headers["etag"]
    assert response.headers["x-export-read"] == "reused"
    assert len(builds) == 1
    assert entry.reader.page_valid_until > completed_at + 60
    assert entry.deadline == completed_at + 60  # hits never slide the completed-body TTL
    assert repository.engine.pool.checkedout() == 0


@pytest.mark.parametrize("boundary", ["target", "snapshot"])
def test_slow_forecast_completion_does_not_move_absolute_business_boundary(
    repository, tmp_path, monkeypatch, boundary,
):
    import json

    from sqlalchemy import select, update

    _collect_forecast(repository, tmp_path)
    client = _client(repository, tmp_path)
    origin = datetime.now(UTC)
    if boundary == "target":
        store = DatabaseExportArtifactStore(repository)
        with repository.engine.connect() as c:
            payload = c.scalar(select(store.table.c.payload).limit(1))
        earliest = min(datetime.fromisoformat(p["target_at"])
                       for p in (json.loads(line) for line in payload.splitlines())
                       if datetime.fromisoformat(p["target_at"]) > origin)
        origin = earliest - timedelta(seconds=30)
    clock = [0.0]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = origin + timedelta(seconds=clock[0])
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    for module in (export_forecasts, export_snapshots, export_inventory_selection):
        monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(export_snapshots, "monotonic", lambda: clock[0])
    monkeypatch.setattr(export_forecasts, "monotonic", lambda: clock[0], raising=False)
    for name in ("export_page_cache", "export_selection_cache", "export_snapshot_cache"):
        getattr(client.app.state, name).clock = lambda: clock[0]
    page_items, freeze = FrozenForecastExports._page_items, FrozenForecastExports._freeze_forecasts

    def slow_build(self, *args):
        result = page_items(self, *args)
        clock[0] += 20
        return result

    def bounded_snapshot(self):
        frozen = freeze(self)
        if boundary == "snapshot":
            expires = origin + timedelta(seconds=30)
            with repository.engine.begin() as c:
                c.execute(update(self.snapshots).where(
                    self.snapshots.c.snapshot_id == frozen["snapshot_id"],
                ).values(expires_at=expires))
            frozen = dict(frozen, expires_at=expires)
        return frozen

    monkeypatch.setattr(FrozenForecastExports, "_page_items", slow_build)
    monkeypatch.setattr(FrozenForecastExports, "_freeze_forecasts", bounded_snapshot)
    first = client.get("/v1/service/exports/forecasts?style=ultra_short&limit=1", headers=HEADERS)
    assert first.status_code == 200, first.text
    entry = next(iter(client.app.state.export_page_cache.entries.values()))
    assert clock[0] == 20 and entry.deadline == 30  # never 50 or completion+60
