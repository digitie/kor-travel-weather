"""첫 예보의 요청별 fresh 선택을 같은 cutoff에서 재사용하며 실패·삭제·시간 경계를 보존한다."""

from datetime import UTC, datetime, timedelta

import pytest
from kortravelweather_api.routers import service_exports
from sqlalchemy import event, select
from test_frozen_service_exports import HEADERS, _client, _collect_forecast
from test_frozen_service_exports import repository as repository

from kortravelweather import export_forecasts, export_inventory_selection, export_snapshots
from kortravelweather.export_heads import ExportHeads
from kortravelweather.repository import Base


def clients(repository, tmp_path, monkeypatch, *, limit=200, boundary=None):
    _collect_forecast(repository, tmp_path)
    clock = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    for module in (
        export_forecasts,
        export_inventory_selection,
        export_snapshots,
        service_exports,
    ):
        monkeypatch.setattr(module, "datetime", Clock)
    if boundary:
        age = {
            "freshness": timedelta(hours=6),
            "expiry": timedelta(hours=24),
            "future": timedelta(seconds=-1),
            "stale": timedelta(hours=6, seconds=2),
        }[boundary]
        inventory = Base.metadata.tables["weather_export_approved_locations"]
        with repository.engine.begin() as c:
            c.execute(
                inventory.update()
                .where(inventory.c.location_id == "b")
                .values(
                    source_collected_at=clock[0] - age + timedelta(seconds=1),
                )
            )
    path = f"/v1/service/exports/forecasts?style=ultra_short&limit={limit}"
    old, new = _client(repository, tmp_path), _client(repository, tmp_path)
    old_first, new_first = old.get(path, headers=HEADERS), new.get(path, headers=HEADERS)
    assert old_first.status_code == new_first.status_code == 200
    assert old_first.json() == new_first.json()
    assert old_first.headers["etag"] == new_first.headers["etag"]
    return old, new, path, new_first, clock


def conditional(client, path, first, monkeypatch, *, original_health=False):
    if not original_health:
        return client.get(path, headers={**HEADERS, "if-none-match": first.headers["etag"]})
    # 같은 실제 DB bulk를 실행하고 proof 시각만 없애 기존 health 재조회와 비교한다.
    read = export_inventory_selection.approved_source_selection
    with monkeypatch.context() as patch:

        def without_proof(*args, **kwargs):
            kwargs["health_proof"] = False
            return read(*args, **kwargs)

        patch.setattr(export_inventory_selection, "approved_source_selection", without_proof)
        return client.get(path, headers={**HEADERS, "if-none-match": first.headers["etag"]})


@pytest.mark.parametrize("limit", [1, 200, 2000])
def test_same_cutoff_body_cursor_etag_and_one_fresh_inventory_read(
    repository,
    tmp_path,
    monkeypatch,
    limit,
):
    old, new, path, first, _ = clients(repository, tmp_path, monkeypatch, limit=limit)
    queries = []

    def observed(_c, _cur, statement, _params, _ctx, _many):
        queries.append(statement)

    event.listen(repository.engine, "before_cursor_execute", observed)
    try:
        baseline = conditional(old, path, first, monkeypatch, original_health=True)
        old_queries = list(queries)
        queries.clear()
        actual = conditional(new, path, first, monkeypatch)
    finally:
        event.remove(repository.engine, "before_cursor_execute", observed)
    assert baseline.status_code == actual.status_code == 304
    assert actual.content == baseline.content == b""
    assert actual.headers["etag"] == baseline.headers["etag"] == first.headers["etag"]
    assert actual.headers["x-export-artifact-read-calls"] == "0"
    assert sum("FROM weather_export_approved_locations " in sql for sql in queries) == 1
    assert sum("FROM weather_export_approved_locations " in sql for sql in old_queries) == 2
    assert not any("weather_export_artifacts.payload" in sql for sql in queries)
    assert any("weather_page_liveness_epochs" in sql for sql in queries)
    assert any("weather_export_artifacts.byte_count" in sql for sql in queries)
    cursor = first.json()["meta"]["next_cursor"]
    if cursor:
        left = old.get(path + "&cursor=" + cursor, headers=HEADERS)
        right = new.get(path + "&cursor=" + cursor, headers=HEADERS)
        assert left.status_code == right.status_code == 200
        assert left.json() == right.json() and left.headers["etag"] == right.headers["etag"]


@pytest.mark.parametrize(
    "mutation",
    ["failed_attempt", "failed_run", "new_failed_cohort", "head", "plan", "generation", "artifact"],
)
def test_same_first_request_observes_failure_or_delete(
    repository,
    tmp_path,
    monkeypatch,
    mutation,
):
    old, new, path, first, _ = clients(repository, tmp_path, monkeypatch)
    assert first.json()["meta"]["source_status"] == "ok"
    heads = ExportHeads(repository)
    if mutation == "new_failed_cohort":
        run = repository.start_sync_run(
            provider="python-kma-api",
            dataset_key="kma_ultra_short_forecast",
            export_cohort_required=True,
        )
        repository.finish_sync_run(run.run_id, status="failed")
    with repository.engine.begin() as c:
        if mutation == "new_failed_cohort":
            pass
        elif mutation == "failed_attempt":
            c.execute(heads.lifecycle.attempts.update().values(status="failed"))
        elif mutation == "failed_run":
            c.execute(Base.metadata.tables["weather_sync_runs"].update().values(status="failed"))
        else:
            if mutation == "generation":
                # live FK를 우회하지 않고 참조 제거 뒤 실제 generation 삭제를 검증한다.
                for name in (
                    "weather_export_snapshot_refs",
                    "weather_export_approved_locations",
                    "weather_export_heads",
                    "weather_export_generation_chunks",
                    "weather_export_pending_refs",
                ):
                    c.execute(Base.metadata.tables[name].delete())
            table = {
                "head": heads.heads,
                "plan": heads.plans,
                "generation": heads.generations,
                "artifact": Base.metadata.tables["weather_export_artifacts"],
            }[mutation]
            c.execute(table.delete())
    baseline = conditional(old, path, first, monkeypatch, original_health=True)
    actual = conditional(new, path, first, monkeypatch)
    assert actual.status_code == baseline.status_code
    assert actual.headers.get("etag") == baseline.headers.get("etag")
    if actual.status_code == 200:
        assert actual.json() == baseline.json()
        assert actual.json()["meta"]["source_status"] == "stale"
        assert actual.headers["etag"] != first.headers["etag"]
    else:
        assert actual.status_code == 503 and actual.json()["code"] == baseline.json()["code"]


@pytest.mark.parametrize("boundary", ["freshness", "expiry", "backwards"])
def test_boundary_crossing_and_clock_rollback_keep_full_health_read(
    repository,
    tmp_path,
    monkeypatch,
    boundary,
):
    old, new, path, first, clock = clients(
        repository,
        tmp_path,
        monkeypatch,
        boundary=None if boundary == "backwards" else boundary,
    )
    freeze, health = (
        export_forecasts.FrozenForecastExports._freeze_forecasts,
        export_forecasts.FrozenForecastExports._inventory_forecast_health,
    )
    calls = []

    approvals = []
    selection = export_inventory_selection.approved_source_selection

    def observed_selection(*args, **kwargs):
        result = selection(*args, **kwargs)
        if kwargs.get("health_proof"):
            approvals.append(result)
        return result

    monkeypatch.setattr(export_inventory_selection, "approved_source_selection", observed_selection)

    def move_after_freeze(self):
        result = freeze(self)
        clock[0] += timedelta(seconds=-1 if boundary == "backwards" else 2)
        return result

    def observed(self, *args):
        calls.append(1)
        return health(self, *args)

    monkeypatch.setattr(
        export_forecasts.FrozenForecastExports, "_freeze_forecasts", move_after_freeze
    )
    monkeypatch.setattr(
        export_forecasts.FrozenForecastExports, "_inventory_forecast_health", observed
    )
    start = clock[0]
    baseline = conditional(old, path, first, monkeypatch, original_health=True)
    clock[0] = start
    calls.clear()
    approvals.clear()
    actual = conditional(new, path, first, monkeypatch)
    # snapshot 교체로 cold 재발급한 경우 경계 뒤의 새 full 승인이 proof를 만든다.
    # 이전 경계 전 proof를 재사용하거나, clock rollback을 무시해서는 안 된다.
    assert calls or boundary != "backwards" and any(
        result.health_checked_at is not None
        and start + timedelta(seconds=2) <= result.health_checked_at <= clock[0]
        and (result.health_recheck_at is None or clock[0] < result.health_recheck_at)
        for result in approvals
    ), "crossed temporal proof requires full health or a new post-boundary approval"
    assert actual.status_code == baseline.status_code
    if actual.status_code == 200:
        # 경계 시험은 baseline도 새 snapshot을 발급한다. 새 UUID 대신 데이터/상태와
        # 각 응답의 정확한 ETag 입력을 비교한다(동일 cutoff의304 시험은 ETag도 동일).
        from kortravelweather.export_heads import _digest

        assert actual.json()["items"] == baseline.json()["items"]
        assert actual.json()["meta"]["source_status"] == baseline.json()["meta"]["source_status"]
        for client, response in ((old, baseline), (new, actual)):
            entry = next(iter(client.app.state.export_page_cache.entries.values()))
            page, etag = entry.result
            expected = (
                'W/"'
                + _digest(
                    [
                        page["meta"]["snapshot_id"],
                        None,
                        200,
                        page["meta"]["source_status"],
                        page["items"],
                    ]
                )
                + '"'
            )
            assert response.headers["etag"] == etag == expected
            assert response.headers["etag"] != first.headers["etag"]
    else:
        assert actual.headers.get("etag") == baseline.headers.get("etag")


def test_health_boundary_uses_every_location_not_only_scope_representative(
    repository,
    tmp_path,
    monkeypatch,
):
    _, client, _, _, clock = clients(
        repository,
        tmp_path,
        monkeypatch,
        boundary="freshness",
    )
    from kortravelweather.export_inventory_selection import (
        approved_source_selection,
        catalog_source_projection,
    )

    catalog = Base.metadata.tables["weather_locations"]
    source_from, columns = catalog_source_projection(catalog)
    with (
        repository.engine.connect().execution_options(isolation_level="REPEATABLE READ") as c,
        c.begin(),
    ):
        locations = (
            c.execute(
                select(
                    catalog.c.location_id,
                    catalog.c.nx,
                    catalog.c.ny,
                    catalog.c.latitude,
                    catalog.c.longitude,
                    *columns,
                ).select_from(source_from)
            )
            .mappings()
            .all()
        )
        result = approved_source_selection(
            c,
            Base.metadata,
            locations,
            expected=[("python-kma-api", "kma_ultra_short_forecast")],
            health_proof=True,
        )
    assert result.health_checked_at == clock[0]
    assert result.health_recheck_at == clock[0] + timedelta(seconds=1)
    assert all(scope.source_collected_at > clock[0] - timedelta(hours=1) for scope in result.scopes)
    client.close()


@pytest.mark.parametrize("state", [None, "stale", "future"])
def test_fresh_selected_health_equals_full_health_at_same_cutoff(
    repository,
    tmp_path,
    monkeypatch,
    state,
):
    _, client, path, first, clock = clients(
        repository,
        tmp_path,
        monkeypatch,
        boundary=state,
    )
    freeze = export_forecasts.FrozenForecastExports._freeze_forecasts
    compared = []

    def checked_freeze(self):
        result = freeze(self)
        proof = getattr(self, "_fresh_forecast_health", None)
        if getattr(self, "_capture_forecast_health", False) and proof is not None:
            try:
                full = self._inventory_forecast_health(result["manifest"], result, clock[0])
                assert proof[2] == full
                compared.append((proof[2], full))
            finally:
                self._fresh_forecast_health = proof
        return result

    monkeypatch.setattr(export_forecasts.FrozenForecastExports, "_freeze_forecasts", checked_freeze)
    response = conditional(client, path, first, monkeypatch)
    assert compared and response.status_code == 304
    assert response.headers["etag"] == first.headers["etag"]
    assert response.headers["x-export-artifact-read-calls"] == "0"


def test_future_source_time_crossing_now_preserves_full_health_verdict(
    repository,
    tmp_path,
    monkeypatch,
):
    old, new, path, first, clock = clients(
        repository,
        tmp_path,
        monkeypatch,
        boundary="future",
    )
    freeze = export_forecasts.FrozenForecastExports._freeze_forecasts

    def cross_source_time(self):
        result = freeze(self)
        clock[0] += timedelta(seconds=3)
        return result

    monkeypatch.setattr(
        export_forecasts.FrozenForecastExports, "_freeze_forecasts", cross_source_time
    )
    start = clock[0]
    baseline = conditional(old, path, first, monkeypatch, original_health=True)
    clock[0] = start
    actual = conditional(new, path, first, monkeypatch)
    assert baseline.status_code == actual.status_code == 304
    assert baseline.headers["etag"] == actual.headers["etag"] == first.headers["etag"]


def test_retry_then_previous_issue_does_not_reuse_successful_first_attempt_health(
    repository,
    tmp_path,
    monkeypatch,
):
    from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore

    _, client, path, first, _ = clients(repository, tmp_path, monkeypatch)
    freeze = export_forecasts.FrozenForecastExports._freeze_forecasts
    present = DatabaseExportArtifactStore.all_present
    health = export_forecasts.FrozenForecastExports._inventory_forecast_health
    freezes, presence, full_checks = [], [], []

    def retry_presence(self, refs):
        actual = present(self, refs)
        presence.append(1)
        return False if len(presence) == 1 else actual

    def fallback_second_issue(self):
        freezes.append(1)
        if len(freezes) == 2:
            with repository.engine.begin() as c:
                c.execute(
                    ExportHeads(repository).lifecycle.attempts.update().values(status="failed")
                )
            raise export_forecasts.ExportSnapshotUnavailable("test: second issue fallback")
        return freeze(self)

    def full_health(self, *args):
        full_checks.append(1)
        return health(self, *args)

    monkeypatch.setattr(DatabaseExportArtifactStore, "all_present", retry_presence)
    monkeypatch.setattr(
        export_forecasts.FrozenForecastExports, "_freeze_forecasts", fallback_second_issue
    )
    monkeypatch.setattr(
        export_forecasts.FrozenForecastExports, "_inventory_forecast_health", full_health
    )
    response = conditional(client, path, first, monkeypatch)
    assert len(freezes) >= 2 and full_checks
    assert response.status_code == 200 and response.json()["meta"]["source_status"] == "stale"
    assert response.headers["etag"] != first.headers["etag"]
