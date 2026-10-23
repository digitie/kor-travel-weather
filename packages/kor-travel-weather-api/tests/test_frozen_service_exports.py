from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from kma import KmaClient, KmaError
from kortravelweather_api.app import create_app
from kortravelweather_dagster.kma_weather import (
    KMA_ULTRA_SHORT_NOWCAST,
    WeatherTarget,
    run_weather_sync,
)
from sqlalchemy import text, update

from kortravelweather.export_collector import publish_pending_exports
from kortravelweather.export_snapshots import export_snapshot_tables
from kortravelweather.models import WeatherLocation
from kortravelweather.repository import Base, WeatherRepository
from kortravelweather.settings import WeatherSettings

TOKEN = "private-service-token"
HEADERS = {"x-admin-token": TOKEN}


@pytest.fixture
def repository():
    repository = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    repository.create_schema()

    def clean():
        with repository.engine.begin() as c:
            c.execute(
                text(
                    "TRUNCATE weather_export_snapshots, weather_export_scope_plans, "
                    "weather_export_heads, weather_export_generations, "
                    "weather_export_generation_chunks, "
                    "weather_export_pending_refs, weather_export_attempts, weather_locations, "
                    "weather_sync_runs RESTART IDENTITY CASCADE"
                )
            )

    clean()
    yield repository
    clean()
    repository.engine.dispose()


def _collect(repository, root, *, hours=1, value="18.4", failed=False):
    clock = datetime.now(UTC) - timedelta(hours=hours)

    class Client(KmaClient):
        async def now(self, **kwargs):
            return await super().now(**kwargs, when=clock)

    def respond(request):
        if failed:
            return httpx.Response(500)
        q = request.url.params
        rows = [
            dict(
                baseDate=q["base_date"],
                baseTime=q["base_time"],
                nx=int(q["nx"]),
                ny=int(q["ny"]),
                category=k,
                obsrValue=v,
            )
            for k, v in [
                ("T1H", value),
                ("REH", "50"),
                ("RN1", "0"),
                ("UUU", "0"),
                ("VVV", "0"),
                ("PTY", "0"),
                ("VEC", "120"),
                ("WSD", "1"),
            ]
        ]
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                    "body": {
                        "pageNo": 1,
                        "numOfRows": 1000,
                        "totalCount": len(rows),
                        "items": {"item": rows},
                    },
                }
            },
        )

    client = Client(
        "private-key", session=httpx.AsyncClient(transport=httpx.MockTransport(respond)), retries=0
    )
    targets = [
        WeatherTarget(
            WeatherLocation(
                location_id=key,
                name=key + "검증지점" * 20,
                latitude=37.5,
                longitude=127,
                nx=nx,
                ny=127,
            )
        )
        for key, nx in [("a", 60), ("b", 60), ("c", 61)]
    ]
    result = run_weather_sync(
        repository=repository,
        client=client,
        targets=targets,
        base_datasets=frozenset({KMA_ULTRA_SHORT_NOWCAST}),
        export_artifact_root=root,
    )
    assert result["status"] == "success"
    assert publish_pending_exports(repository, root).results == {"PUBLISHED": 2}


def _client(repository, tmp_path, *, providers=None, enabled=True):
    settings = WeatherSettings(
        environment="development",
        admin_token=TOKEN,
        enabled_providers=providers if providers is not None else ["python-kma-api"],
        service_exports_enabled=enabled,
        service_export_artifact_root=tmp_path,
    )
    return TestClient(create_app(settings, repository))


def _collect_forecast(repository, root, *, targets=None, wait=None, run_scope=None):
    from kma.time_utils import parse_kma_datetime
    from kortravelweather_dagster.kma_weather import KMA_ULTRA_SHORT_FORECAST

    def respond(request):
        if wait:
            wait()
        q = request.url.params
        base = parse_kma_datetime(q["base_date"], q["base_time"])
        rows = []
        for hour in range(1, 7):
            at = (base + timedelta(hours=hour)).replace(minute=0)
            for category, value in {
                "T1H": "18.4",
                "RN1": "0",
                "REH": "50",
                "PTY": "0",
                "SKY": "1",
                "VEC": "120",
                "WSD": "1",
            }.items():
                rows.append(
                    dict(
                        baseDate=q["base_date"],
                        baseTime=q["base_time"],
                        nx=int(q["nx"]),
                        ny=int(q["ny"]),
                        category=category,
                        fcstValue=value,
                        fcstDate=at.strftime("%Y%m%d"),
                        fcstTime=at.strftime("%H%M"),
                    )
                )
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                    "body": {
                        "pageNo": 1,
                        "numOfRows": 1000,
                        "totalCount": len(rows),
                        "items": {"item": rows},
                    },
                }
            },
        )

    provider = KmaClient(
        "private-key", session=httpx.AsyncClient(transport=httpx.MockTransport(respond)), retries=0
    )
    if targets is None:
        targets = [
            WeatherTarget(
                WeatherLocation(
                    location_id=key,
                    name=key,
                    latitude=37.5,
                    longitude=127,
                    nx=nx,
                    ny=127,
                )
            )
            for key, nx in [("a", 60), ("b", 60), ("c", 61)]
        ]
    result = run_weather_sync(
        repository=repository,
        client=provider,
        targets=targets,
        base_datasets=frozenset({KMA_ULTRA_SHORT_FORECAST}),
        export_artifact_root=root,
        sync_run=(
            repository.start_sync_run(provider="python-kma-api", dataset_key=run_scope)
            if run_scope
            else None
        ),
    )
    assert result["status"] == "success"
    publish_pending_exports(repository, root)
    return targets


def test_forecast_groups_auth_gzip_cursor_and_empty_publication_delta(repository, tmp_path):
    _collect_forecast(repository, tmp_path)
    client = _client(repository, tmp_path)
    url = "/v1/service/exports/forecasts"
    params = {"style": "ultra_short", "limit": 1}
    assert client.get(url, params=params).status_code == 401
    first = client.get(url, params=params, headers=HEADERS)
    assert first.status_code == 200 and first.headers["content-encoding"] == "gzip"
    assert first.json()["meta"]["source_status"] == "ok"
    assert (
        client.get(
            url, params=params, headers={**HEADERS, "if-none-match": first.headers["etag"]}
        ).status_code
        == 304
    )
    groups, page = [], first.json()
    while True:
        groups.extend(page["items"])
        if not page["meta"]["has_more"]:
            break
        page = client.get(
            url, params={**params, "cursor": page["meta"]["next_cursor"]}, headers=HEADERS
        ).json()
    assert {g["location_id"] for g in groups} == {"a", "b", "c"}
    for group in groups:
        assert datetime.fromisoformat(group["known_at"]) < datetime.fromisoformat(
            group["published_at"]
        )
        assert all(len(v) == len(group["target_at"]) for v in group["metrics"].values())
        assert all(
            datetime.fromisoformat(t) > datetime.fromisoformat(page["meta"]["as_of"])
            for t in group["target_at"]
        )
    watermark = page["meta"]["next_since"]
    empty = client.get(url, params={"style": "ultra_short", "since": watermark}, headers=HEADERS)
    assert empty.status_code == 200 and empty.json()["items"] == []
    assert empty.json()["meta"]["source_status"] == "ok"
    _collect_forecast(repository, tmp_path)
    changed = client.get(url, params={"style": "ultra_short", "since": watermark}, headers=HEADERS)
    assert changed.status_code == 200 and len(changed.json()["items"]) == 3
    # 동일 bytes 재승격 때 원본 known_at은 보존하고 publication 시각만 바뀐다.
    assert {g["location_id"]: g["known_at"] for g in changed.json()["items"]} == {
        g["location_id"]: g["known_at"] for g in groups
    }
    assert changed.json()["meta"]["next_since"] > watermark
    assert (
        client.get(
            url,
            params={"style": "short", "limit": 1, "cursor": first.json()["meta"]["next_cursor"]},
            headers=HEADERS,
        ).status_code
        == 422
    )


def test_late_forecast_commit_after_watermark_is_in_next_delta(repository, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    targets = _collect_forecast(repository, tmp_path)
    entered, release = Event(), Event()

    def wait():
        entered.set()
        assert release.wait(20)

    client = _client(repository, tmp_path)
    url = "/v1/service/exports/forecasts"
    with ThreadPoolExecutor(1) as pool:
        late = pool.submit(
            _collect_forecast,
            repository,
            tmp_path,
            targets=targets[:2],
            wait=wait,
            run_scope="kma_ultra_short_forecast",
        )
        assert entered.wait(10)
        try:
            _collect_forecast(
                repository, tmp_path, targets=targets[2:], run_scope="kma_weather_bundle"
            )
            checkpoint = client.get(url, params={"style": "ultra_short"}, headers=HEADERS).json()[
                "meta"
            ]["next_since"]
        finally:
            release.set()
        late.result(timeout=30)
    delta = client.get(url, params={"style": "ultra_short", "since": checkpoint}, headers=HEADERS)
    assert delta.status_code == 200
    assert {g["location_id"] for g in delta.json()["items"]} == {"a", "b"}
    assert all(
        datetime.fromisoformat(g["known_at"])
        < datetime.fromisoformat(checkpoint)
        < datetime.fromisoformat(g["published_at"])
        for g in delta.json()["items"]
    )


def test_forecast_publication_clock_increases_when_server_time_repeats(
    repository, tmp_path, monkeypatch
):
    from sqlalchemy import literal, select

    from kortravelweather import export_heads

    _collect_forecast(repository, tmp_path)
    clock = Base.metadata.tables["weather_export_publication_clock"]
    with repository.engine.connect() as c:
        first = c.scalar(select(clock.c.published_at))
    monkeypatch.setattr(export_heads.func, "clock_timestamp", lambda: literal(first))
    _collect_forecast(repository, tmp_path)
    with repository.engine.connect() as c:
        second = c.scalar(select(clock.c.published_at))
    assert second > first
    _collect_forecast(repository, tmp_path)
    with repository.engine.connect() as c:
        third = c.scalar(select(clock.c.published_at))
    assert third > second


def test_forecast_missing_head_remains_stale_on_cursor_and_does_not_replace_current_fallback(
    repository, tmp_path
):
    from kortravelweather.export_heads import ExportHeads

    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    current = client.get("/v1/service/exports/locations", headers=HEADERS)
    _collect_forecast(repository, tmp_path)
    params = {"style": "ultra_short", "limit": 1}
    url = "/v1/service/exports/forecasts"
    initial = client.get(url, params=params, headers=HEADERS)
    cursor = initial.json()["meta"]["next_cursor"]
    heads = ExportHeads(repository)
    with repository.engine.begin() as c:
        c.execute(heads.heads.delete())
    fallback = client.get(url, params=params, headers=HEADERS)
    continued = client.get(url, params={**params, "cursor": cursor}, headers=HEADERS)
    assert fallback.status_code == continued.status_code == 200
    assert (
        fallback.json()["meta"]["source_status"]
        == continued.json()["meta"]["source_status"]
        == "stale"
    )
    locations = client.get("/v1/service/exports/locations", headers=HEADERS)
    assert locations.status_code == 200 and locations.json()["items"] == current.json()["items"]
    assert locations.json()["meta"]["source_status"] == "stale"


def test_forecast_four_mib_cap_cursor_keeps_every_last_included_location(
    repository, tmp_path, monkeypatch
):
    from kortravelweather.export_artifacts import ExportArtifactStore

    _collect_forecast(repository, tmp_path)
    original = ExportArtifactStore.read

    def large_projection(self, *args, **kwargs):
        # byte-budget 단위 검증: 실제 승인/검증된 file을 읽은 뒤 DTO에서 허용하는
        # 긴 문자열 값을 투영해, 3개 위치만으로 실제 4MiB 경계를 넘긴다.
        for point in original(self, *args, **kwargs):
            yield {**point, "value_number": None, "value_text": "x" * 40_000}

    monkeypatch.setattr(ExportArtifactStore, "read", large_projection)
    client = _client(repository, tmp_path)
    params, seen, sizes = {"style": "ultra_short", "limit": 5000}, [], []
    while True:
        response = client.get("/v1/service/exports/forecasts", params=params, headers=HEADERS)
        assert response.status_code == 200
        assert len(response.content) <= 4 * 1024 * 1024
        page = response.json()
        seen.extend(item["location_id"] for item in page["items"])
        sizes.append(len(page["items"]))
        if not page["meta"]["has_more"]:
            break
        assert page["items"]
        params["cursor"] = page["meta"]["next_cursor"]
    assert seen == ["a", "b", "c"] and len(sizes) >= 2


def test_forecast_snapshot_metadata_lock_has_bounded_wait_and_recovers(repository, tmp_path):
    import time

    _collect_forecast(repository, tmp_path)
    client = _client(repository, tmp_path)
    with repository.engine.begin() as lock:
        lock.execute(text("LOCK TABLE weather_export_heads IN ACCESS EXCLUSIVE MODE"))
        started = time.monotonic()
        response = client.get(
            "/v1/service/exports/forecasts", params={"style": "ultra_short"}, headers=HEADERS
        )
        elapsed = time.monotonic() - started
        assert response.status_code == 503 and elapsed < 7
    assert (
        client.get(
            "/v1/service/exports/forecasts", params={"style": "ultra_short"}, headers=HEADERS
        ).status_code
        == 200
    )


def test_real_approved_current_and_locations_have_etag_gzip_and_auth(repository, tmp_path):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    for resource, count in [("current", 24), ("locations", 3)]:
        url = f"/v1/service/exports/{resource}"
        assert client.get(url).status_code == 401
        r = client.get(url, headers=HEADERS)
        assert r.status_code == 200 and len(r.json()["items"]) == count
        assert r.json()["meta"]["source_status"] == "ok"
        assert r.headers["content-encoding"] == "gzip"
        assert (
            client.get(
                url,
                headers={
                    **HEADERS,
                    "if-none-match": '"other", ' + r.headers["etag"].removeprefix("W/"),
                },
            ).status_code
            == 304
        )
        plain = client.get(url, headers={**HEADERS, "accept-encoding": "gzip;q=0"})
        assert plain.status_code == 200 and "content-encoding" not in plain.headers
        assert r.headers["etag"].startswith('W/"') and plain.headers["etag"] == r.headers["etag"]
        assert plain.json()["meta"]["generated_at"] != r.json()["meta"]["generated_at"]
        assert (
            client.get(
                url,
                headers={
                    **HEADERS,
                    "accept-encoding": "identity",
                    "if-none-match": r.headers["etag"],
                },
            ).status_code
            == 304
        )


def test_frozen_cursor_survives_new_success_head_and_bound_resource_limit(repository, tmp_path):
    _collect(repository, tmp_path, hours=1)
    client = _client(repository, tmp_path)
    url = "/v1/service/exports/current?limit=1"
    first = client.get(url, headers=HEADERS).json()
    cursor = first["meta"]["next_cursor"]
    _collect(repository, tmp_path, hours=0, value="20.4")
    values, pages = first["items"], []
    while cursor:
        page = client.get(url, params={"cursor": cursor, "limit": 1}, headers=HEADERS).json()
        assert page["meta"]["snapshot_id"] == first["meta"]["snapshot_id"]
        values.extend(page["items"])
        pages.append(page)
        cursor = page["meta"]["next_cursor"]
    assert len(values) == 24 and all(
        row["value"] == 18.4 for row in values if row["metric_key"] == "T1H"
    )
    latest = client.get("/v1/service/exports/current", headers=HEADERS).json()
    assert latest["meta"]["snapshot_id"] != first["meta"]["snapshot_id"]
    assert all(row["value"] == 20.4 for row in latest["items"] if row["metric_key"] == "T1H")
    initial = first["meta"]["next_cursor"]
    for path in ["/v1/service/exports/current?limit=2", "/v1/service/exports/locations?limit=1"]:
        assert client.get(path, params={"cursor": initial}, headers=HEADERS).status_code == 422
    assert client.get(url, params={"cursor": initial + "x"}, headers=HEADERS).status_code == 422


def test_failed_new_source_keeps_old_data_stale_and_changes_etag(repository, tmp_path):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    first = client.get("/v1/service/exports/current", headers=HEADERS)
    with pytest.raises(KmaError):
        _collect(repository, tmp_path, failed=True)
    stale = client.get(
        "/v1/service/exports/current", headers={**HEADERS, "if-none-match": first.headers["etag"]}
    )
    assert stale.status_code == 200 and stale.json()["meta"]["source_status"] == "stale"
    assert stale.json()["items"] == first.json()["items"]
    assert stale.headers["etag"] != first.headers["etag"]


def test_removed_head_remains_stale_on_existing_and_fallback_cursors(repository, tmp_path):
    from kortravelweather.export_heads import ExportHeads

    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    url = "/v1/service/exports/current?limit=1"
    first = client.get(url, headers=HEADERS)
    cursor = first.json()["meta"]["next_cursor"]
    before = client.get(url, params={"cursor": cursor, "limit": 1}, headers=HEADERS)
    assert before.json()["meta"]["source_status"] == "ok"
    with repository.engine.begin() as connection:
        connection.execute(ExportHeads(repository).heads.delete())
    fallback = client.get(url, headers={**HEADERS, "if-none-match": first.headers["etag"]})
    assert fallback.status_code == 200
    assert fallback.json()["meta"]["source_status"] == "stale"
    for continued_cursor in [cursor, fallback.json()["meta"]["next_cursor"]]:
        continued = client.get(
            url,
            params={"cursor": continued_cursor, "limit": 1},
            headers={**HEADERS, "if-none-match": before.headers["etag"]},
        )
        assert continued.status_code == 200
        assert continued.json()["meta"]["source_status"] == "stale"
        assert continued.json()["items"] == before.json()["items"]


def test_expired_cursor_returns_410_without_success_or_etag(repository, tmp_path):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    page = client.get("/v1/service/exports/locations?limit=1", headers=HEADERS).json()
    table, _ = export_snapshot_tables(Base.metadata)
    with repository.engine.begin() as c:
        c.execute(
            update(table)
            .where(table.c.snapshot_id == page["meta"]["snapshot_id"])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    r = client.get(
        "/v1/service/exports/locations?limit=1",
        params={"cursor": page["meta"]["next_cursor"], "limit": 1},
        headers=HEADERS,
    )
    assert r.status_code == 410 and r.json()["code"] == "EXPORT_SNAPSHOT_EXPIRED"
    assert "etag" not in r.headers


def test_unready_partial_provider_and_missing_file_never_empty_ok(repository, tmp_path):
    client = _client(repository, tmp_path)
    assert client.get("/v1/service/exports/current", headers=HEADERS).status_code == 503
    _collect(repository, tmp_path)
    all_providers = _client(repository, tmp_path, providers=["python-kma-api", "open_meteo"])
    assert all_providers.get("/v1/service/exports/current", headers=HEADERS).status_code == 503
    assert (
        all_providers.get(
            "/v1/service/exports/current?provider=python-kma-api", headers=HEADERS
        ).status_code
        == 200
    )
    next(tmp_path.glob("*.jsonl")).unlink()
    r = client.get("/v1/service/exports/current", headers=HEADERS)
    assert r.status_code == 503 and "items" not in r.json() and "etag" not in r.headers


def test_artifact_read_does_not_hold_pg_connection(repository, tmp_path, monkeypatch):
    from kortravelweather.export_artifacts import ExportArtifactStore

    _collect(repository, tmp_path)
    original = ExportArtifactStore.read

    def read(self, *args, **kwargs):
        assert repository.engine.pool.checkedout() == 0
        yield from original(self, *args, **kwargs)

    monkeypatch.setattr(ExportArtifactStore, "read", read)
    assert (
        _client(repository, tmp_path)
        .get("/v1/service/exports/current", headers=HEADERS)
        .status_code
        == 200
    )


def test_disabled_default_preserves_503_even_when_success_heads_exist(repository, tmp_path):
    _collect(repository, tmp_path)
    assert (
        _client(repository, tmp_path, enabled=False)
        .get("/v1/service/exports/current", headers=HEADERS)
        .status_code
        == 503
    )


def test_new_unconfirmed_catalog_scope_keeps_previous_success_stale(repository, tmp_path):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    first = client.get("/v1/service/exports/locations?limit=1", headers=HEADERS).json()
    repository.create_location(
        WeatherLocation(
            location_id="new", name="아직 수집 전", latitude=37.5, longitude=127, nx=62, ny=127
        )
    )
    latest = client.get("/v1/service/exports/locations", headers=HEADERS)
    assert latest.status_code == 200 and latest.json()["meta"]["source_status"] == "stale"
    assert {row["location_id"] for row in latest.json()["items"]} == {"a", "b", "c"}
    continued = client.get(
        "/v1/service/exports/locations",
        params={"limit": 1, "cursor": first["meta"]["next_cursor"]},
        headers=HEADERS,
    )
    assert continued.status_code == 200 and continued.json()["meta"]["source_status"] == "stale"


def test_identical_manifest_after_reader_ttl_gets_new_etag_and_usable_cursor(
    repository, tmp_path, monkeypatch
):
    from kortravelweather import export_snapshots

    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    url = "/v1/service/exports/current"
    first = client.get(url, params={"limit": 1}, headers=HEADERS)
    base = datetime.now(UTC)

    class AdvancedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (base + timedelta(minutes=16)).astimezone(tz or UTC)

    monkeypatch.setattr(export_snapshots, "datetime", AdvancedClock)
    renewed = client.get(
        url, params={"limit": 1}, headers={**HEADERS, "if-none-match": first.headers["etag"]}
    )
    assert renewed.status_code == 200 and renewed.headers["etag"] != first.headers["etag"]
    assert renewed.json()["meta"]["snapshot_id"] != first.json()["meta"]["snapshot_id"]
    next_page = client.get(
        url, params={"limit": 1, "cursor": renewed.json()["meta"]["next_cursor"]}, headers=HEADERS
    )
    assert next_page.status_code == 200
    assert (
        client.get(
            url, params={"limit": 1, "cursor": first.json()["meta"]["next_cursor"]}, headers=HEADERS
        ).status_code
        == 410
    )


def test_locations_page_byte_cap_preserves_all_valid_unicode_items(
    repository, tmp_path, monkeypatch
):
    import copy
    from uuid import uuid4

    from kortravelweather.export_artifacts import ExportArtifactStore
    from kortravelweather.export_snapshots import MAX_PAGE_BYTES, FrozenCurrentExports

    _collect(repository, tmp_path)
    reader = FrozenCurrentExports(repository, ExportArtifactStore(tmp_path), signing_key=TOKEN)
    frozen = reader._freeze()
    frozen = dict(frozen, snapshot_id=str(uuid4()), manifest=copy.deepcopy(frozen["manifest"]))
    frozen["manifest"]["locations"] = [
        dict(
            location_id=f"loc-{i:05d}-" + "a" * 110,
            name="🙂" * 200,
            latitude=37.5,
            longitude=127.0,
            legal_dong_code=None,
            sido_name=None,
            sigungu_name=None,
        )
        for i in range(5000)
    ]
    table, _ = export_snapshot_tables(Base.metadata)
    with repository.engine.begin() as connection:
        connection.execute(table.insert().values(**frozen))
    monkeypatch.setattr(FrozenCurrentExports, "_freeze", lambda self: frozen)
    client = _client(repository, tmp_path)
    first = client.get(
        "/v1/service/exports/locations",
        params={"limit": 5000},
        headers={**HEADERS, "accept-encoding": "identity"},
    )
    assert first.status_code == 200 and len(first.content) <= MAX_PAGE_BYTES
    assert 0 < len(first.json()["items"]) < 5000 and first.json()["meta"]["has_more"]
    next_page = client.get(
        "/v1/service/exports/locations",
        params={"limit": 5000, "cursor": first.json()["meta"]["next_cursor"]},
        headers={**HEADERS, "accept-encoding": "identity"},
    )
    assert next_page.status_code == 200 and len(next_page.content) <= MAX_PAGE_BYTES
    assert not next_page.json()["meta"]["has_more"]
    items = first.json()["items"] + next_page.json()["items"]
    assert len(items) == len({row["location_id"] for row in items}) == 5000


def test_reverse_address_not_found_clears_old_payload_and_code_is_ascii(repository, tmp_path):
    from kortravelcommon.address_providers import VworldKakaoAddressResolver
    from kortravelcommon.reverse_address import AddressTarget

    _collect(repository, tmp_path)
    entries = Base.metadata.tables["reverse_address_entries"]
    identity = dict(provider_id=VworldKakaoAddressResolver.provider_id, target_key="a")

    def write(status, code):
        with repository.engine.begin() as c:
            c.execute(entries.delete().where(entries.c.target_key == "a"))
            c.execute(
                entries.insert().values(
                    **identity,
                    longitude=127,
                    latitude=37.5,
                    coordinate_key=AddressTarget("a", 127, 37.5).coordinate_key,
                    status=status,
                    checked_at=datetime.now(UTC),
                    failures=0,
                    payload={
                        "address": {"legal_dong_code": code},
                        "region": {"sido": "서울", "sigungu": "종로구"},
                    },
                )
            )

    client = _client(repository, tmp_path)
    for status, expected in [("ok", "1111000000"), ("error", "1111000000"), ("not_found", None)]:
        write(status, "1111000000")
        r = client.get("/v1/service/exports/locations", headers=HEADERS)
        assert r.status_code == 200
        item = next(x for x in r.json()["items"] if x["location_id"] == "a")
        assert item["legal_dong_code"] == expected
        if status == "not_found":
            assert item["sido_name"] is None and item["sigungu_name"] is None
    write("ok", "١١١١٠٠٠٠٠٠")
    r = client.get("/v1/service/exports/locations", headers=HEADERS)
    assert (
        r.status_code == 200
        and next(x for x in r.json()["items"] if x["location_id"] == "a")["legal_dong_code"] is None
    )


def test_location_snapshot_does_not_read_arbitrary_unused_metadata(repository, tmp_path):
    from sqlalchemy import event

    _collect(repository, tmp_path)
    catalog = Base.metadata.tables["weather_locations"]
    with repository.engine.begin() as c:
        c.execute(update(catalog).values(metadata={"unused_large": "x" * 200_000}))
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "FROM weather_locations" in statement:
            statements.append(statement)

    event.listen(repository.engine, "before_cursor_execute", record)
    try:
        r = _client(repository, tmp_path).get("/v1/service/exports/locations", headers=HEADERS)
    finally:
        event.remove(repository.engine, "before_cursor_execute", record)
    assert r.status_code == 200 and statements
    assert all("weather_locations.metadata" not in sql for sql in statements)
    assert "unused_large" not in r.text
