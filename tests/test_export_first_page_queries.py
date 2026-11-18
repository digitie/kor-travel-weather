"""작은 RR 묶음 조회가 일별/현재의 값·cursor·즉시 변경 반영을 유지한다."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from math import ceil

import pytest
from kortravelcommon.address_providers import VworldKakaoAddressResolver
from kortravelcommon.reverse_address import AddressTarget
from sqlalchemy import event, select
from test_export_daily import daily_repository as daily_repository
from test_export_heads import _candidate

from kortravelweather.export_daily import KST, SLOTS
from kortravelweather.export_daily_reader import DailySummaryExports
from kortravelweather.export_daily_store import refresh_daily_summaries
from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore
from kortravelweather.export_heads import ScopeDescriptor
from kortravelweather.export_snapshots import FrozenCurrentExports
from kortravelweather.models import WeatherLocation


@pytest.fixture
def approved_catalog(daily_repository, tmp_path):
    repo = daily_repository
    keys = [f"batch-{number:03}" for number in range(20)] + ["batch-A", "batch-a"]
    for key in keys:
        repo.create_location(WeatherLocation(
            location_id=key, name=key + "힣휴게소", latitude=37.5, longitude=127, nx=60, ny=127,
        ))
    descriptor = ScopeDescriptor(
        entity_type="weather_response", entity_id="grid:60:127", nx=60, ny=127,
        location_ids=tuple(sorted(keys)), normalizer_revision="kma-v1",
        winner_policy_revision="kma-base-v1",
    )
    yesterday = (datetime.now(KST) - timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0,
    )
    store = DatabaseExportArtifactStore(repo)
    heads, identity, capture, *_ = _candidate(
        repo, tmp_path, descriptor, revision=yesterday, value="12.25", cohort=True,
        artifacts=store,
    )
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    assert refresh_daily_summaries(repo, store)["processed"] == 1
    return repo, store, keys


def daily_reader(repo, store, **kwargs):
    return DailySummaryExports(
        repo, store, signing_key="query-fixture", source_inventory=True,
        expected_sources=(("python-kma-api", "kma_ultra_short_nowcast"),), **kwargs,
    )


@contextmanager
def summary_queries(engine):
    calls = []

    def capture(_connection, _cursor, statement, parameters, _context, _many):
        if statement.startswith("SELECT") and "FROM weather_daily_summaries " in statement:
            calls.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield calls
    finally:
        event.remove(engine, "before_cursor_execute", capture)


@pytest.mark.parametrize("limit", [200, 2000])
def test_daily_scalar_queries_use_bounded_groups_and_preserve_values(approved_catalog, limit):
    repo, store, keys = approved_catalog
    reader = daily_reader(repo, store)
    with summary_queries(repo.engine) as calls:
        page, _tag = reader.page(limit=limit, cursor=None)
    assert len(page["items"]) == min(limit, len(keys) * 14 * len(SLOTS))
    assert {item["temp_avg"] for item in page["items"] if item["temp_avg"] is not None} == {12.25}
    assert all("hourly_winners" not in sql for sql, _ in calls)
    needed = min(len(keys), ceil(limit / (14 * len(SLOTS))) + 1)
    assert len(calls) <= ceil(needed / 8)
    for _sql, params in calls:
        groups = [value for value in params.values() if isinstance(value, list)]
        assert len(groups) == 1 and 1 <= len(groups[0]) <= 8


def test_daily_walk_keeps_frozen_order_no_missing_or_duplicate_rows(approved_catalog):
    repo, store, keys = approved_catalog
    first, _ = daily_reader(repo, store, days=2).page(limit=7, cursor=None)
    with repo.engine.connect() as connection:
        snapshots = daily_reader(repo, store).snapshots
        manifest = connection.scalar(select(snapshots.c.manifest).where(
            snapshots.c.snapshot_id == first["meta"]["snapshot_id"],
        ))
    # DB collation이 Python 정렬과 같다고 가정하지 않고 frozen roster를 기준으로 한다.
    expected = [(key, (datetime.fromisoformat(manifest["first_date"]).date()
                       + timedelta(days=offset)).isoformat(), slot)
                for key in manifest["locations"] for offset in range(2) for slot in SLOTS]
    pages, page = [first], first
    while page["meta"]["has_more"]:
        page, _ = daily_reader(repo, store, days=2).page(
            limit=7, cursor=page["meta"]["next_cursor"],
        )
        assert page["meta"]["snapshot_id"] == first["meta"]["snapshot_id"]
        pages.append(page)
    actual = [(item["location_id"], item["date"], item["slot"])
              for page in pages for item in page["items"]]
    assert actual == expected and len(actual) == len(set(actual)) == len(keys) * 2 * len(SLOTS)


def test_daily_batch_preserves_rr_and_reads_next_request_update_immediately(approved_catalog):
    repo, store, _keys = approved_catalog
    reader = daily_reader(repo, store)
    catalog = reader.heads.metadata.tables["weather_locations"]
    with repo.engine.connect() as connection:
        last = connection.scalar(select(catalog.c.location_id).order_by(
            catalog.c.location_id.desc(),
        ).limit(1))
        target = connection.execute(select(reader.table.c.date, reader.table.c.slot).where(
            reader.table.c.location_id == last,
        )).one()
    changed = False

    def update_during_first_select(_c, _cursor, statement, _parameters, _context, _many):
        nonlocal changed
        if (changed or not statement.startswith("SELECT")
                or "FROM weather_daily_summaries " not in statement):
            return
        changed = True
        with repo.engine.begin() as c:
            item = dict(c.scalar(select(reader.table.c.summary).where(
                reader.table.c.location_id == last,
                reader.table.c.date == target.date, reader.table.c.slot == target.slot,
            )))
            item["temp_avg"] = 99.125
            c.execute(reader.table.update().where(
                reader.table.c.location_id == last,
                reader.table.c.date == target.date, reader.table.c.slot == target.slot,
            ).values(summary=item))

    event.listen(repo.engine, "after_cursor_execute", update_during_first_select)
    try:
        before, tag_before = reader.page(limit=2000, cursor=None)
    finally:
        event.remove(repo.engine, "after_cursor_execute", update_during_first_select)
    assert changed
    after, tag_after = daily_reader(repo, store).page(limit=2000, cursor=None)

    def selected(page):
        return next(item for item in page["items"] if (
            item["location_id"] == last and item["date"] == target.date.isoformat()
            and item["slot"] == target.slot
        ))

    assert selected(before)["temp_avg"] == 12.25
    assert selected(after)["temp_avg"] == 99.125 and tag_after != tag_before


def test_current_address_array_keeps_status_coordinate_and_latest_input_checks(approved_catalog):
    repo, store, keys = approved_catalog
    reader = FrozenCurrentExports(
        repo, store, signing_key="query-fixture", source_inventory=True,
        expected_sources=(("python-kma-api", "kma_ultra_short_nowcast"),),
    )
    entries = reader.heads.metadata.tables["reverse_address_entries"]
    now = datetime.now(UTC)
    with repo.engine.begin() as c:
        c.execute(entries.insert(), [dict(
            provider_id=VworldKakaoAddressResolver.provider_id, target_key=key,
            longitude=127, latitude=37.5,
            coordinate_key=AddressTarget(key, 128 if index == 4 else 127, 37.5).coordinate_key,
            status="not_found" if index == 2 else "error" if index == 1 else "ok",
            checked_at=now, failures=0,
            payload={"address": {"legal_dong_code": "invalid" if index == 3 else "1111000000"},
                     "region": {"sido": "서울", "sigungu": "종로구"}},
        ) for index, key in enumerate(keys[:5])])
    calls = []

    def capture(_c, _cursor, statement, parameters, _context, _many):
        if statement.startswith("SELECT") and "FROM reverse_address_entries" in statement:
            calls.append(parameters)

    event.listen(repo.engine, "before_cursor_execute", capture)
    try:
        first = reader._freeze()
    finally:
        event.remove(repo.engine, "before_cursor_execute", capture)
    items = {item["location_id"]: item for item in first["manifest"]["locations"]}
    assert set(items) == set(keys)
    assert [items[key]["legal_dong_code"] for key in keys[:5]] == [
        "1111000000", "1111000000", None, None, None,
    ]
    assert items[keys[2]]["sido_name"] is None and items[keys[4]]["sido_name"] is None
    assert items[keys[3]]["sido_name"] == "서울"
    groups = [value for value in calls[0].values() if isinstance(value, list)]
    assert len(groups) == 1 and set(groups[0]) == set(keys)
    with repo.engine.begin() as c:
        c.execute(entries.update().where(entries.c.target_key == keys[0]).values(
            payload={"address": {"legal_dong_code": "2222000000"}, "region": {}},
        ))
    latest = reader._freeze()
    assert next(item for item in latest["manifest"]["locations"]
                if item["location_id"] == keys[0])["legal_dong_code"] == "2222000000"
    assert latest["manifest_digest"] != first["manifest_digest"]
