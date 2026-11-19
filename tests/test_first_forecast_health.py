"""첫 예보의 동일 RR 건강도 재사용과 실제 PG 시간 경계·fallback 회귀."""

from datetime import UTC, datetime, timedelta

import pytest
from kortravelweather_api.routers import service_exports
from sqlalchemy import event
from test_forecast_catalog_projection import collect_style
from test_frozen_service_exports import HEADERS, _client
from test_frozen_service_exports import repository as repository

from kortravelweather import export_forecasts, export_inventory_selection, export_snapshots
from kortravelweather.export_heads import ExportHeads
from kortravelweather.repository import Base


def clock_fixture(monkeypatch):
    wall = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return wall[0].astimezone(tz) if tz else wall[0].replace(tzinfo=None)

    for module in (export_forecasts, export_inventory_selection, export_snapshots, service_exports):
        monkeypatch.setattr(module, "datetime", Clock)
    return wall


def get_first(client, path, monkeypatch, *, baseline=False):
    if not baseline:
        return client.get(path, headers=HEADERS)
    original = export_inventory_selection.approved_source_selection
    with monkeypatch.context() as patch:
        def without_proof(*args, **kwargs):
            kwargs["health_proof"] = False
            return original(*args, **kwargs)
        patch.setattr(export_inventory_selection, "approved_source_selection", without_proof)
        return client.get(path, headers=HEADERS)


@pytest.mark.parametrize("style", ["short", "ultra_short"])
@pytest.mark.parametrize("limit", [1, 200, 2000])
def test_first_body_cursor_etag_equal_with_one_approval_read(
    repository, tmp_path, monkeypatch, style, limit,
):
    collect_style(repository, tmp_path, style)
    clock_fixture(monkeypatch)
    old, new = _client(repository, tmp_path), _client(repository, tmp_path)
    path = f"/v1/service/exports/forecasts?style={style}&limit={limit}"
    queries = []

    def observe(_c, _cur, sql, _params, _ctx, _many):
        queries.append(sql)

    event.listen(repository.engine, "before_cursor_execute", observe)
    try:
        baseline = get_first(old, path, monkeypatch, baseline=True)
        old_queries = list(queries)
        queries.clear()
        actual = get_first(new, path, monkeypatch)
    finally:
        event.remove(repository.engine, "before_cursor_execute", observe)
    assert baseline.status_code == actual.status_code == 200
    assert actual.json() == baseline.json()
    assert actual.headers["etag"] == baseline.headers["etag"]
    assert sum("FROM weather_export_approved_locations " in sql for sql in old_queries) == 2
    assert sum("FROM weather_export_approved_locations " in sql for sql in queries) == 1
    assert any("weather_page_liveness_epochs" in sql for sql in queries)
    assert int(actual.headers["x-export-artifact-read-calls"]) > 0
    cursor = actual.json()["meta"]["next_cursor"]
    if cursor:
        a = old.get(path + "&cursor=" + cursor, headers=HEADERS)
        b = new.get(path + "&cursor=" + cursor, headers=HEADERS)
        assert a.status_code == b.status_code == 200 and a.json() == b.json()
    old.close()
    new.close()


@pytest.mark.parametrize("boundary", ["freshness", "expiry", "backwards"])
def test_payload_crossing_boundary_keeps_full_health(
    repository, tmp_path, monkeypatch, boundary,
):
    collect_style(repository, tmp_path, "ultra_short")
    wall = clock_fixture(monkeypatch)
    start = wall[0]
    if boundary != "backwards":
        age = timedelta(hours=6 if boundary == "freshness" else 24)
        table = Base.metadata.tables["weather_export_approved_locations"]
        with repository.engine.begin() as c:
            c.execute(table.update().where(table.c.location_id == "forecast-0001").values(
                source_collected_at=start - age + timedelta(seconds=1),
            ))
    reader = export_forecasts.FrozenForecastExports
    original_items, original_health = reader._page_items, reader._inventory_forecast_health
    full_checks = []

    def move_after_payload(self, *args):
        result = original_items(self, *args)
        wall[0] += timedelta(seconds=-1 if boundary == "backwards" else 2)
        return result

    def observe_health(self, *args):
        full_checks.append(1)
        return original_health(self, *args)

    monkeypatch.setattr(reader, "_page_items", move_after_payload)
    monkeypatch.setattr(reader, "_inventory_forecast_health", observe_health)
    old, new = _client(repository, tmp_path), _client(repository, tmp_path)
    path = "/v1/service/exports/forecasts?style=ultra_short&limit=1"
    baseline = get_first(old, path, monkeypatch, baseline=True)
    wall[0] = start
    full_checks.clear()
    actual = get_first(new, path, monkeypatch)
    assert full_checks, "actual post-payload clock must invalidate the earlier health proof"
    assert actual.status_code == baseline.status_code
    if actual.status_code == 200:
        assert actual.json() == baseline.json()
        assert actual.headers["etag"] == baseline.headers["etag"]
    old.close()
    new.close()


def test_first_retry_then_previous_fallback_discards_old_success_proof(
    repository, tmp_path, monkeypatch,
):
    from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore

    collect_style(repository, tmp_path, "ultra_short")
    clock_fixture(monkeypatch)
    previous = _client(repository, tmp_path)
    path = "/v1/service/exports/forecasts?style=ultra_short&limit=1"
    assert previous.get(path, headers=HEADERS).status_code == 200
    reader = export_forecasts.FrozenForecastExports
    original_freeze, present = reader._freeze_forecasts, DatabaseExportArtifactStore.all_present
    original_health = reader._inventory_forecast_health
    freezes, presence, checks = [], [], []

    def first_presence_missing(self, refs):
        actual = present(self, refs)
        presence.append(1)
        return False if len(presence) == 1 else actual

    def second_issue_fails(self):
        freezes.append(1)
        if len(freezes) == 2:
            with repository.engine.begin() as c:
                c.execute(ExportHeads(repository).lifecycle.attempts.update().values(status="failed"))
            raise export_forecasts.ExportSnapshotUnavailable("fixture: second issue fallback")
        return original_freeze(self)

    def full_health(self, *args):
        checks.append(1)
        return original_health(self, *args)

    monkeypatch.setattr(DatabaseExportArtifactStore, "all_present", first_presence_missing)
    monkeypatch.setattr(reader, "_freeze_forecasts", second_issue_fails)
    monkeypatch.setattr(reader, "_inventory_forecast_health", full_health)
    fresh = _client(repository, tmp_path)
    result = fresh.get(path, headers=HEADERS)
    assert len(freezes) >= 2 and checks
    assert result.status_code == 200 and result.json()["meta"]["source_status"] == "stale"
    previous.close()
    fresh.close()
