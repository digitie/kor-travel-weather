"""최초 조회의 왕복·중복 검산을 줄여도 원천 선택과 MVCC 의미를 유지한다."""

from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    select,
)

from kortravelweather.export_inventory_selection import _latest_cohort_runs
from kortravelweather.export_selection_cache import ExportSelectionCache


def test_latest_cohort_batch_keeps_ties_missing_failed_and_noncohort_selection():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    runs = Table("runs", metadata, Column("run_id", String, primary_key=True),
                 Column("provider", String), Column("dataset_key", String),
                 Column("started_at", DateTime), Column("export_cohort_required", Boolean),
                 Column("status", String))
    metadata.create_all(engine)
    rows = [dict(run_id=key, provider="p", dataset_key=dataset,
                 started_at=datetime(2026, 11, day), export_cohort_required=cohort,
                 status=status) for key, dataset, day, cohort, status in [
        ("a", "d", 1, True, "success"), ("b", "d", 1, True, "failed"),
        ("c", "d", 2, False, "success"), ("z", "other", 3, True, "running"),
    ]]
    with engine.begin() as connection:
        connection.execute(runs.insert(), rows)
        expected = {(provider, dataset): connection.scalar(select(runs.c.run_id).where(
            runs.c.provider == provider, runs.c.dataset_key == dataset,
            runs.c.export_cohort_required.is_(True),
        ).order_by(runs.c.started_at.desc(), runs.c.run_id.desc()).limit(1))
                    for provider, dataset in [("p", "d"), ("p", "other"), ("none", "d")]}
        queries = []
        event.listen(connection, "before_cursor_execute", lambda *args: queries.append(1))
        assert _latest_cohort_runs(connection, runs, expected) == expected
        assert len(queries) == 1
        assert _latest_cohort_runs(connection, runs, ()) == {} and len(queries) == 1


def test_catalog_key_preserves_scalar_types_float_sign_and_row_order_independence():
    connection = SimpleNamespace(engine=object())
    def key(value):
        return ExportSelectionCache.key(connection, value, [("p", "d")], (), None)
    original = [{"location_id": "a", "nx": 1, "latitude": 0.0}, {"location_id": "b"}]
    assert key(original) == key(original[::-1])
    for changed in [dict(nx="1"), dict(nx=True), dict(nx=1.0), dict(latitude=-0.0),
                    dict(point_station_name="None")]:
        assert key(original) != key([{**original[0], **changed}, original[1]])


def test_daily_first_page_coverage_uses_one_rr_and_does_not_escape_request(
    external_repository, tmp_path, monkeypatch,
):
    from fastapi.testclient import TestClient
    from kortravelweather_api.app import create_app
    from test_export_daily_sources import _drain
    from test_export_heads import _candidate, _descriptor

    from kortravelweather.export_daily_reader import DailySummaryExports
    from kortravelweather.settings import WeatherSettings

    repo = external_repository
    heads, identity, capture, *_ = _candidate(repo, tmp_path, _descriptor(repo), cohort=True)
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    _drain(repo, tmp_path)
    seen, readers = [], []
    original = DailySummaryExports._coverage_rows
    def coverage(reader, connection, keys):
        assert connection is reader._daily_connection
        seen.append(connection)
        readers.append(reader)
        return original(reader, connection, keys)
    monkeypatch.setattr(DailySummaryExports, "_coverage_rows", coverage)
    client = TestClient(create_app(WeatherSettings(
        admin_token="daily-one-rr", enabled_providers=["python-kma-api"],
        service_exports_enabled=True, service_export_artifact_root=tmp_path,
    ), repo))
    first = client.get("/v1/service/exports/daily-summaries", params={"days": 1, "limit": 1},
                       headers={"x-admin-token": "daily-one-rr"})
    assert first.status_code == 200 and first.json()["meta"]["source_status"] == "ok"
    assert len(seen) == 1
    assert readers[0]._daily_connection is None and readers[0]._daily_coverage_memo is None
    cursor = first.json()["meta"]["next_cursor"]
    second = client.get("/v1/service/exports/daily-summaries", params={
        "days": 1, "limit": 1, "cursor": cursor,
    }, headers={"x-admin-token": "daily-one-rr"})
    assert second.status_code == 200 and len(seen) == 2 and seen[0] is not seen[1]
    assert (first.json()["meta"]["source_collected_at"]
            == second.json()["meta"]["source_collected_at"])
    assert all(r._daily_connection is None and r._daily_coverage_memo is None for r in readers)


# 실제 PostgreSQL fixture를 다른 export 시험과 같이 사용한다.
from test_export_external import external_repository as external_repository  # noqa: E402


def test_only_committed_atomic_pin_skips_duplicate_file_presence_and_retry_is_retained():
    from kortravelweather.export_snapshots import ensure_snapshot_files

    class Store:
        def __init__(self):
            self.calls = 0
        def all_present(self, refs):
            self.calls += 1
            return self.calls > 1
    store = Store()
    pinned = {"manifest": {"scopes": []}, "_atomic_files_pinned": True}
    assert ensure_snapshot_files(store, lambda: pinned) is pinned
    assert store.calls == 0 and "_atomic_files_pinned" not in pinned
    issued = []
    def issue():
        issued.append(True)
        return {"manifest": {"scopes": []}}
    ensure_snapshot_files(store, issue)
    assert len(issued) == 2 and store.calls == 2
