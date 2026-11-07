from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from sqlalchemy import select, text, update

from kortravelweather.export_collector import publish_pending_exports
from kortravelweather.export_snapshots import export_snapshot_tables
from kortravelweather.models import WeatherLocation
from kortravelweather.repository import Base, WeatherRepository
from kortravelweather.settings import WeatherSettings

TOKEN = "private-service-token"
HEADERS = {"x-admin-token": TOKEN}


def test_location_full_small_full_then_catalog_loss_keeps_latest_full(repository, tmp_path):
    for key in ("a", "b", "c"):
        repository.create_location(WeatherLocation(
            location_id=key, name=key, latitude=37.5, longitude=127,
        ))
    client = _client(repository, tmp_path)
    url = "/v1/service/exports/locations"
    first = client.get(url, headers=HEADERS).json()
    catalog = Base.metadata.tables["weather_locations"]
    with repository.engine.begin() as c:
        c.execute(catalog.update().where(
            catalog.c.location_id.in_(["b", "c"])
        ).values(enabled=False))
    assert len(client.get(url, headers=HEADERS).json()["items"]) == 1
    with repository.engine.begin() as c:
        c.execute(catalog.update().values(enabled=True))
    restored = client.get(url, headers=HEADERS).json()
    assert len(restored["items"]) == 3
    assert restored["meta"]["snapshot_id"] != first["meta"]["snapshot_id"]
    with repository.engine.begin() as c:
        c.execute(catalog.delete().where(catalog.c.location_id.in_(["b", "c"])))
    fallback = client.get(url, headers=HEADERS).json()
    assert len(fallback["items"]) == 3 and fallback["meta"]["source_status"] == "stale"


def test_forecast_full_small_full_then_head_loss_keeps_latest_full(repository, tmp_path):
    from kortravelweather.export_heads import ExportHeads

    _collect_forecast(repository, tmp_path)
    client = _client(repository, tmp_path)
    url, params = "/v1/service/exports/forecasts", {"style": "ultra_short", "limit": 5000}
    first = client.get(url, params=params, headers=HEADERS).json()
    catalog = Base.metadata.tables["weather_locations"]
    with repository.engine.begin() as c:
        c.execute(catalog.update().where(catalog.c.location_id == "c").values(enabled=False))
    assert len(client.get(url, params=params, headers=HEADERS).json()["items"]) == 2
    with repository.engine.begin() as c:
        c.execute(catalog.update().values(enabled=True))
    restored = client.get(url, params=params, headers=HEADERS).json()
    assert len(restored["items"]) == 3
    assert restored["meta"]["snapshot_id"] != first["meta"]["snapshot_id"]
    heads = ExportHeads(repository)
    with repository.engine.begin() as c:
        c.execute(heads.heads.delete().where(heads.heads.c.namespace.in_(
            c.scalars(select(heads.plans.c.namespace).where(
                heads.plans.c.descriptor["entity_id"].as_string() == "grid:61:127",
            )).all(),
        )))
    fallback = client.get(url, params=params, headers=HEADERS).json()
    assert len(fallback["items"]) == 3 and fallback["meta"]["source_status"] == "stale"


def test_location_reference_is_independent_of_weather_heads_and_keeps_unknown_clock(
    repository, tmp_path
):
    client = _client(repository, tmp_path)
    url = "/v1/service/exports/locations"
    assert client.get(url, headers=HEADERS).status_code == 503
    for key in ["a", "b", "c"]:
        repository.create_location(
            WeatherLocation(
                location_id=key,
                name="원래 " + key,
                latitude=37.5,
                longitude=127,
            )
        )
    first = client.get(url, params={"limit": 1}, headers=HEADERS)
    assert first.status_code == 200
    assert first.json()["meta"]["source_collected_at"] is None
    assert first.json()["meta"]["source_status"] == "stale"
    assert (
        client.get(
            url, params={"limit": 1}, headers={**HEADERS, "if-none-match": first.headers["etag"]}
        ).status_code
        == 304
    )
    catalog = Base.metadata.tables["weather_locations"]
    repository.create_location(
        WeatherLocation(
            location_id="d",
            name="추가",
            latitude=37.5,
            longitude=127,
        )
    )
    with repository.engine.begin() as c:
        c.execute(catalog.update().where(catalog.c.location_id == "b").values(name="변경"))
    old, cursor = list(first.json()["items"]), first.json()["meta"]["next_cursor"]
    while cursor:
        page = client.get(url, params={"limit": 1, "cursor": cursor}, headers=HEADERS)
        assert page.status_code == 200
        old.extend(page.json()["items"])
        cursor = page.json()["meta"]["next_cursor"]
    assert [(r["location_id"], r["name"]) for r in old] == [
        ("a", "원래 a"),
        ("b", "원래 b"),
        ("c", "원래 c"),
    ]
    latest = client.get(url, headers=HEADERS)
    assert latest.status_code == 200 and len(latest.json()["items"]) == 4
    with repository.engine.begin() as c:
        # 원천 catalog가 갑자기 빈 응답으로 대체된 장애를 재현한다.
        c.execute(catalog.delete())
    retained = client.get(url, headers=HEADERS)
    assert retained.status_code == 200 and retained.json()["items"] == latest.json()["items"]
    assert retained.json()["meta"]["source_status"] == "stale"


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
    assert sorted(seen) == ["a", "b", "c"] and len(sizes) >= 2


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
        # 잠금 대기는 export 부재가 아니다. 같은 요청을 다시 보내라는 구분 코드다.
        assert response.json()["code"] == "EXPORT_TEMPORARILY_UNAVAILABLE"
        assert response.headers["retry-after"] == "5"
    assert (
        client.get(
            "/v1/service/exports/forecasts", params={"style": "ultra_short"}, headers=HEADERS
        ).status_code
        == 200
    )


def test_reader_guard_wait_is_retryable_and_the_same_request_recovers(repository, tmp_path):
    from kortravelweather.export_artifacts import ExportArtifactStore

    _collect_forecast(repository, tmp_path)
    client = _client(repository, tmp_path)
    url, params = "/v1/service/exports/forecasts", {"style": "ultra_short"}
    with ExportArtifactStore(tmp_path).guard(timeout_seconds=5):  # 승격 writer가 잡은 상태
        busy = client.get(url, params=params, headers=HEADERS)
    assert busy.status_code == 503
    assert busy.json()["code"] == "EXPORT_TEMPORARILY_UNAVAILABLE"
    assert busy.headers["retry-after"] == "5" and "etag" not in busy.headers
    assert client.get(url, params=params, headers=HEADERS).status_code == 200


def test_reader_waits_out_a_short_exclusive_hold_instead_of_503(repository, tmp_path):
    """배타 보유가 2초를 조금 넘어도 reader는 기본 5초 안에서 기다려 200을 낸다(2026-11-05 운영)."""
    import threading
    import time

    from kortravelweather.export_artifacts import ExportArtifactStore

    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    held, release = threading.Event(), threading.Event()

    def writer():
        with ExportArtifactStore(tmp_path).guard(timeout_seconds=5):  # base에서도 같은 호출
            held.set()
            release.wait(10)

    thread = threading.Thread(target=writer)
    thread.start()
    assert held.wait(10)
    threading.Timer(3.0, release.set).start()
    started = time.monotonic()
    response = client.get("/v1/service/exports/current", headers=HEADERS)
    thread.join(10)
    assert response.status_code == 200, response.text
    assert 2.5 < time.monotonic() - started < 9


def test_reader_guard_wait_is_configurable_and_bounded():
    from kortravelweather.export_artifacts import ExportArtifactError, ExportArtifactStore

    assert WeatherSettings().service_export_reader_guard_seconds == 5.0
    configured = WeatherSettings(service_export_reader_guard_seconds=8)
    assert configured.service_export_reader_guard_seconds == 8
    for bad in (0, 0.1, 60, "x"):
        assert WeatherSettings(
            service_export_reader_guard_seconds=bad,
        ).service_export_reader_guard_seconds == 5.0
    with pytest.raises(ExportArtifactError):
        ExportArtifactStore(Path("/tmp/weather-guard-bound"), reader_guard_seconds=30)


def test_exclusive_guard_hold_is_measured_per_holder_and_slow_holds_are_logged(
    tmp_path, monkeypatch, caplog,
):
    import time

    from kortravelweather import export_artifacts
    from kortravelweather.export_artifacts import ExportArtifactStore

    seen = []
    monkeypatch.setattr(
        "kortravelweather.metrics.observe_export_guard_hold",
        lambda holder, seconds: seen.append((holder, seconds)),
    )
    monkeypatch.setattr(export_artifacts, "SLOW_EXCLUSIVE_HOLD_SECONDS", 0.05)
    monkeypatch.setattr(export_artifacts.logger, "disabled", False)
    store = ExportArtifactStore(tmp_path)
    with caplog.at_level("WARNING", logger="kortravelweather.export_artifacts"):
        with store.guard(holder="gc"):
            time.sleep(0.1)
        with store.guard(holder="not-a-holder"):
            pass
        with store.guard(shared=True):
            pass
    assert [h for h, _ in seen] == ["gc", "other"]  # 공유 guard는 세지 않는다
    assert seen[0][1] >= 0.1
    assert "weather_export_guard_hold holder=gc" in caplog.text


@pytest.fixture
def restore_daily_progress(repository):
    """daily checkpoint 표는 fixture TRUNCATE 밖이다. 미래로 옮긴 checkpoint가 뒤 테스트로 새면
    reducer가 새 generation을 영영 건너뛴다(CI 전체 실행 순서에서 실제로 깨졌다)."""
    from kortravelweather.export_daily_store import daily_summary_tables
    from kortravelweather.export_heads import ExportHeads

    _, progress = daily_summary_tables(ExportHeads(repository).metadata)
    with repository.engine.begin() as connection:
        saved = [dict(row) for row in connection.execute(progress.select()).mappings()]
    yield
    with repository.engine.begin() as connection:
        connection.execute(progress.delete())
        if saved:
            connection.execute(progress.insert(), saved)


def _make_old_generations_collectable(repository, root):
    """retention·일별 checkpoint·유예가 지키지 않게 만든다(snapshot ref·head만 남는다)."""
    import os
    import time as clock

    from kortravelweather import export_gc
    from kortravelweather.export_daily_store import daily_summary_tables
    from kortravelweather.export_heads import ExportHeads

    heads = ExportHeads(repository)
    _, progress = daily_summary_tables(heads.metadata)
    with repository.engine.begin() as connection:
        connection.execute(update(heads.generations).values(
            created_at=datetime.now(UTC) - timedelta(hours=30),
        ))
        connection.execute(progress.delete())
        connection.execute(progress.insert().values(
            progress_id=1, published_at=datetime.now(UTC) + timedelta(days=1),
            generation_id="f" * 36, updated_at=datetime.now(UTC),
        ))
    past = clock.time() - 10 * 3600
    for path in root.glob("*.jsonl"):
        os.utime(path, (past, past))
    export_gc.LATEST_SNAPSHOTS = 0


def test_gc_after_freeze_commit_cannot_remove_the_snapshot_files(
    repository, tmp_path, monkeypatch, restore_daily_progress,
):
    """commit된 snapshot ref가 파일을 지킨다 — guard 없이 GC가 파일 읽기 앞에 끼어들어도 읽는다.

    retention·일별 checkpoint·최근 snapshot 규칙을 모두 꺼서 유효 snapshot ref만 남긴다. 새 수집이
    head를 옮겨 이전 generation은 head도 아니다. GC는 그 사이 고아 파일은 실제로 지운다.
    """
    from kortravelweather import export_gc
    from kortravelweather.export_artifacts import ExportArtifactStore
    from kortravelweather.export_gc import collect_export_artifacts

    monkeypatch.setattr(export_gc, "LATEST_SNAPSHOTS", 64)
    _collect(repository, tmp_path, hours=2, value="11")
    client = _client(repository, tmp_path)
    orphan = tmp_path / ("e" * 64 + ".jsonl")
    orphan.write_bytes(b"{}\n")
    original = ExportArtifactStore.read_shared
    raced = {}

    def gc_then_read(self, *args, **kwargs):
        if not raced:
            _collect(repository, tmp_path, hours=0, value="22")  # head를 새 generation으로
            _make_old_generations_collectable(repository, tmp_path)
            raced["report"] = collect_export_artifacts(
                repository, tmp_path, mode="delete", deadline=float("inf"),
                retention=timedelta(hours=1),
            )
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ExportArtifactStore, "read_shared", gc_then_read)
    response = client.get("/v1/service/exports/current", headers=HEADERS)
    assert raced["report"]["deleted"] >= 1 and not orphan.exists()  # GC는 실제로 지웠다
    assert response.status_code == 200, response.text
    values = {item["value"] for item in response.json()["items"] if item["metric_key"] == "T1H"}
    assert values == {11.0}  # freeze한 이전 generation 그대로


def test_gc_before_freeze_commit_reissues_instead_of_serving_missing_files(
    repository, tmp_path, monkeypatch, restore_daily_progress,
):
    """freeze가 commit하기 전(GC가 ref를 못 보는 때)에 head가 바뀌고 GC가 지우면 다시 발급한다."""
    from sqlalchemy import event

    from kortravelweather import export_gc
    from kortravelweather.export_gc import collect_export_artifacts

    monkeypatch.setattr(export_gc, "LATEST_SNAPSHOTS", 64)
    _collect(repository, tmp_path, hours=2, value="11")
    client = _client(repository, tmp_path)
    raced = {}

    def before_snapshot_insert(_conn, _cursor, statement, _parameters, _context, _many):
        insert = statement.lstrip().upper().startswith("INSERT INTO WEATHER_EXPORT_SNAPSHOTS")
        if raced or not insert:
            return
        raced["hit"] = True
        _collect(repository, tmp_path, hours=0, value="22")
        _make_old_generations_collectable(repository, tmp_path)
        raced["report"] = collect_export_artifacts(
            repository, tmp_path, mode="delete", deadline=float("inf"),
            retention=timedelta(hours=1),
        )

    event.listen(repository.engine, "before_cursor_execute", before_snapshot_insert)
    try:
        response = client.get("/v1/service/exports/current", headers=HEADERS)
    finally:
        event.remove(repository.engine, "before_cursor_execute", before_snapshot_insert)
    assert raced["report"]["deleted"] >= 1  # 이전 generation 파일이 실제로 지워졌다
    assert response.status_code in {200, 503}, response.text
    if response.status_code == 200:
        values = {i["value"] for i in response.json()["items"] if i["metric_key"] == "T1H"}
        assert values == {22.0}  # 다시 발급해 새 head를 읽었다
    else:
        assert response.json()["code"] == "EXPORT_TEMPORARILY_UNAVAILABLE"


def test_writer_retries_a_short_guard_contention_instead_of_failing(tmp_path):
    """collector publish·승격 CAS는 guard 대기 상한에 한 번 걸려도 짧게 쉬고 다시 시도한다."""
    import threading
    import time as clock

    from kortravelweather.export_artifacts import ExportArtifactStore

    store = ExportArtifactStore(tmp_path)
    held, release = threading.Event(), threading.Event()

    def other_writer():
        with store.guard(timeout_seconds=5):
            held.set()
            release.wait(10)

    thread = threading.Thread(target=other_writer)
    thread.start()
    assert held.wait(10)
    threading.Timer(0.8, release.set).start()
    started = clock.monotonic()
    with store.exclusive(holder="publish", attempts=5, timeout_seconds=0.3):
        pass
    thread.join(10)
    assert clock.monotonic() - started >= 0.6  # 첫 시도는 상한에 걸렸다


class _Orig(Exception):
    def __init__(self, sqlstate):
        super().__init__("canceling statement")
        self.sqlstate = sqlstate


def _errors():
    # guard 대기는 위의 실제 flock 시험이 다룬다.
    from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError
    from sqlalchemy.exc import TimeoutError as PoolTimeoutError

    from kortravelweather.export_snapshots import ExportSnapshotUnavailable
    from kortravelweather.export_status_reader import ExportStatusPending

    retry, missing = "EXPORT_TEMPORARILY_UNAVAILABLE", "EXPORT_NOT_READY"
    return [
        (OperationalError("SELECT 1", {}, _Orig(None)), retry),  # 연결 단절(SQLSTATE 없음)
        (OperationalError("SELECT 1", {}, _Orig("57014")), retry),  # statement_timeout
        (DBAPIError("SELECT 1", {}, _Orig("55P03")), retry),  # lock_timeout
        (DBAPIError("SELECT 1", {}, _Orig("40001")), retry),  # serialization
        (OperationalError("SELECT 1", {}, _Orig("08006")), retry),  # 연결 실패
        (PoolTimeoutError("QueuePool limit"), retry),
        (ExportStatusPending("수집 상태 재투영을 기다리고 있습니다."), retry),
        (SQLAlchemyError("postgresql://user:secret@db/weather"), missing),
        (DBAPIError("SELECT 1", {}, _Orig("42703")), missing),  # 결함은 경합이 아니다
        (OperationalError("SELECT 1", {}, _Orig("28P01")), missing),  # 인증은 기다려도 같다
        (OperationalError("SELECT 1", {}, _Orig("53100")), missing),  # 디스크 부족
        (ExportSnapshotUnavailable("이전 승인 집합이 없습니다."), missing),
        (OSError("artifact"), missing),
    ]


@pytest.mark.parametrize(("error", "code"), _errors(), ids=[
    "disconnect", "57014", "55P03", "40001", "08006", "pool", "status_pending",
    "bare", "42703", "28P01", "53100", "missing", "oserror",
])
def test_read_contention_is_retryable_503_and_missing_export_stays_not_ready(
    repository, tmp_path, monkeypatch, error, code
):
    """timeout·경합은 재시도 503, 진짜 부재·결함은 EXPORT_NOT_READY로 남긴다."""
    from kortravelweather_api.routers import service_exports

    from kortravelweather.export_snapshots import FrozenCurrentExports

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(FrozenCurrentExports, "page", fail)
    monkeypatch.setattr(service_exports, "read_status", fail)
    client = _client(repository, tmp_path)
    # status는 artifact 파일을 읽지 않으므로 OSError 분기가 없다.
    for resource in ("current", "status")[: 1 if isinstance(error, OSError) else 2]:
        response = client.get(f"/v1/service/exports/{resource}", headers=HEADERS)
        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["code"] == code and "items" not in body
        assert body["meta"]["source_status"] == "failed"
        assert ("retry-after" in response.headers) is (code != "EXPORT_NOT_READY")
        if code != "EXPORT_NOT_READY":
            assert int(response.headers["retry-after"]) >= 1
        assert "secret" not in response.text


@pytest.mark.parametrize("previous", [False, True])
def test_dense_forecast_grid_is_unavailable_or_previous_stale_not_500(
    repository, tmp_path, previous
):
    if previous:
        _collect_forecast(repository, tmp_path)
    client = _client(repository, tmp_path)
    url, params = "/v1/service/exports/forecasts", {"style": "ultra_short", "limit": 1}
    first = client.get(url, params=params, headers=HEADERS) if previous else None
    for i in range(49 if previous else 51):
        repository.create_location(
            WeatherLocation(
                location_id=f"dense-{i}",
                name=f"동일격자 {i}",
                latitude=37.5,
                longitude=127,
                nx=60,
                ny=127,
            )
        )
    if previous:
        # 신규 catalog만으로 실제 source intent를 만들지 않는다. 변경된 대상의
        # 다음 실제 수집이 전체 roster를 승인하지 못한 경우를 확인한다.
        _collect_forecast(repository, tmp_path, targets=[
            WeatherTarget(location) for location in repository.list_locations(
                enabled_only=True, limit=5000,
            )
        ])
    response = client.get(url, params=params, headers=HEADERS)
    if previous:
        assert response.status_code == 200
        assert response.json()["meta"]["source_status"] == "stale"
        assert [{k: v for k, v in item.items() if k != "published_at"}
                for item in response.json()["items"]] == [
                    {k: v for k, v in item.items() if k != "published_at"}
                    for item in first.json()["items"]
                ]
        continued = client.get(
            url,
            params={**params, "cursor": response.json()["meta"]["next_cursor"]},
            headers=HEADERS,
        )
        assert continued.status_code == 200 and continued.json()["meta"]["source_status"] == "stale"
    else:
        assert response.status_code == 503


def test_issued_forecast_watermark_survives_api_clock_rollback(repository, tmp_path, monkeypatch):
    from kortravelweather import export_forecasts

    _collect_forecast(repository, tmp_path)
    client = _client(repository, tmp_path)
    url = "/v1/service/exports/forecasts"
    first = client.get(url, params={"style": "ultra_short"}, headers=HEADERS)
    issued = datetime.fromisoformat(first.json()["meta"]["next_since"])

    class SlowClock(datetime):
        @classmethod
        def now(cls, tz=None):
            at = issued - timedelta(minutes=1)
            return at.astimezone(tz) if tz else at.replace(tzinfo=None)

    monkeypatch.setattr(export_forecasts, "datetime", SlowClock)
    valid = client.get(
        url, params={"style": "ultra_short", "since": issued.isoformat()}, headers=HEADERS
    )
    assert valid.status_code == 200 and valid.json()["items"] == []
    invalid = client.get(
        url,
        params={"style": "ultra_short", "since": (issued + timedelta(days=1)).isoformat()},
        headers=HEADERS,
    )
    assert invalid.status_code == 422


def test_legacy_null_location_index_recovers_after_same_content_recollection(repository, tmp_path):
    from sqlalchemy import select

    from kortravelweather.export_artifacts import ExportArtifactStore
    from kortravelweather.export_heads import ExportHeads

    _collect_forecast(repository, tmp_path)
    heads = ExportHeads(repository)
    with repository.engine.begin() as c:
        original = [dict(row) for row in c.execute(select(heads.refs)).mappings()]
        c.execute(update(heads.refs).values(location_ids=None))
    known = {
        point["location_id"]: point["known_at"]
        for ref in original
        for point in ExportArtifactStore(tmp_path).read(
            ref["artifact_digest"], byte_count=ref["byte_count"], point_count=ref["point_count"]
        )
    }
    client = _client(repository, tmp_path)
    url, params = "/v1/service/exports/forecasts", {"style": "ultra_short"}
    assert client.get(url, params=params, headers=HEADERS).status_code == 503
    _collect_forecast(repository, tmp_path)
    recovered = client.get(url, params=params, headers=HEADERS)
    assert recovered.status_code == 200 and recovered.json()["meta"]["source_status"] == "ok"
    assert {item["location_id"]: item["known_at"] for item in recovered.json()["items"]} == known
    with repository.engine.connect() as c:
        current = list(
            c.execute(
                select(heads.refs.c.artifact_digest, heads.refs.c.location_ids).join(
                    heads.heads, heads.heads.c.generation_id == heads.refs.c.generation_id
                )
            )
        )
    assert {r.artifact_digest for r in current} == {r["artifact_digest"] for r in original}
    assert all(row.location_ids for row in current)


def test_legacy_clock_bootstrap_is_after_existing_future_publication(repository, tmp_path):
    from sqlalchemy import select

    from kortravelweather.export_heads import ExportHeads

    _collect_forecast(repository, tmp_path)
    heads = ExportHeads(repository)
    ahead = datetime.now(UTC) + timedelta(minutes=2)
    with repository.engine.begin() as c:
        c.execute(update(heads.generations).values(created_at=ahead))
        c.execute(Base.metadata.tables["weather_export_publication_clock"].delete())
    _collect_forecast(repository, tmp_path)
    with repository.engine.connect() as c:
        clock = c.scalar(
            select(Base.metadata.tables["weather_export_publication_clock"].c.published_at)
        )
    assert clock > ahead
    response = _client(repository, tmp_path).get(
        "/v1/service/exports/forecasts",
        params={"style": "ultra_short", "since": ahead.isoformat()},
        headers=HEADERS,
    )
    assert response.status_code == 200 and len(response.json()["items"]) == 3
    assert all(
        datetime.fromisoformat(item["published_at"]) > ahead for item in response.json()["items"]
    )


def test_real_approved_current_and_locations_have_etag_gzip_and_auth(repository, tmp_path):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    for resource, count in [("current", 24), ("locations", 3)]:
        url = f"/v1/service/exports/{resource}"
        assert client.get(url).status_code == 401
        r = client.get(url, headers=HEADERS)
        assert r.status_code == 200 and len(r.json()["items"]) == count
        assert r.json()["meta"]["source_status"] == ("ok" if resource == "current" else "stale")
        if resource == "locations":
            assert r.json()["meta"]["source_collected_at"] is None
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


def test_frozen_cursor_survives_new_success_head_and_bound_resource_limit(repository, tmp_path, monkeypatch):
    _collect(repository, tmp_path, hours=1)
    from test_all_source_exports import _selection_clock

    client = _client(repository, tmp_path)
    clock = _selection_clock(client, monkeypatch)
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
    clock[0] += 59
    within = client.get("/v1/service/exports/current", headers=HEADERS).json()
    assert within["meta"]["snapshot_id"] == first["meta"]["snapshot_id"]
    clock[0] += 1
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


def test_new_catalog_reference_is_visible_without_weather_and_existing_cursor_stays_fixed(
    repository, tmp_path
):
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
    assert {row["location_id"] for row in latest.json()["items"]} == {"a", "b", "c", "new"}
    assert latest.json()["meta"]["source_collected_at"] is None
    continued = client.get(
        "/v1/service/exports/locations",
        params={"limit": 1, "cursor": first["meta"]["next_cursor"]},
        headers=HEADERS,
    )
    assert continued.status_code == 200 and continued.json()["meta"]["source_status"] == "stale"
    assert all(row["location_id"] != "new" for row in continued.json()["items"])


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
    from kortravelweather.export_locations import FrozenLocationExports
    from kortravelweather.export_snapshots import MAX_PAGE_BYTES

    _collect(repository, tmp_path)
    reader = FrozenLocationExports(repository, ExportArtifactStore(tmp_path), signing_key=TOKEN)
    frozen = reader._freeze_locations()
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
    monkeypatch.setattr(FrozenLocationExports, "_freeze_locations", lambda self: frozen)
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


def test_forecast_location_with_only_past_targets_is_omitted_instead_of_503(
    repository, tmp_path, monkeypatch
):
    """미래 target이 없는 위치 하나는 빠지고 나머지는 stale200으로 끝까지 받는다."""
    from kortravelweather.export_artifacts import ExportArtifactStore

    _collect_forecast(repository, tmp_path)
    original = ExportArtifactStore.read
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    def expired_c(self, *args, **kwargs):
        for point in original(self, *args, **kwargs):
            yield {**point, "target_at": past} if point["location_id"] == "c" else point

    monkeypatch.setattr(ExportArtifactStore, "read", expired_c)
    client = _client(repository, tmp_path)
    url = "/v1/service/exports/forecasts"
    whole = client.get(url, params={"style": "ultra_short", "limit": 5000}, headers=HEADERS)
    assert whole.status_code == 200, whole.text
    assert {item["location_id"] for item in whole.json()["items"]} == {"a", "b"}
    assert whole.json()["meta"]["source_status"] == "stale"
    # stale은 그 위치를 건너뛴 페이지의 표시다. 다른 페이지는 원래 상태를 유지한다.
    params, seen, statuses = {"style": "ultra_short", "limit": 1}, [], []
    while True:
        page = client.get(url, params=params, headers=HEADERS)
        assert page.status_code == 200, page.text
        statuses.append(page.json()["meta"]["source_status"])
        seen.extend(item["location_id"] for item in page.json()["items"])
        if not page.json()["meta"]["has_more"]:
            break
        params["cursor"] = page.json()["meta"]["next_cursor"]
    assert sorted(seen) == ["a", "b"]
    assert "stale" in statuses


def test_export_page_is_validated_serialized_and_compressed_off_the_event_loop(
    repository, tmp_path, monkeypatch
):
    """4MiB까지의 페이지 직렬화·gzip이 event loop(일반 API·health)를 막지 않는다."""
    import threading

    from kortravelweather_api.routers import service_exports

    _collect(repository, tmp_path)
    threads = []

    original = service_exports.ExportPage.model_dump_json

    def dump_json(self, *args, **kwargs):
        threads.append(threading.current_thread().name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(service_exports.ExportPage, "model_dump_json", dump_json)
    client = _client(repository, tmp_path)
    response = client.get("/v1/service/exports/current", headers=HEADERS)
    assert response.status_code == 200 and response.headers["content-encoding"] == "gzip"
    assert client.get("/v1/service/exports/current", headers={
        **HEADERS, "if-none-match": response.headers["etag"],
    }).status_code == 304
    assert threads and all(name == "AnyIO worker thread" for name in threads)


def test_unavailable_export_logs_only_sanitized_cause(repository, tmp_path, monkeypatch, caplog):
    """503 원인은 분류만 로그에 남기고 메시지·DSN은 남기지 않는다. 상태 코드는 그대로다."""
    from sqlalchemy.exc import SQLAlchemyError

    from kortravelweather.export_snapshots import FrozenCurrentExports

    def fail(self, *args, **kwargs):
        raise SQLAlchemyError("postgresql://user:secret@db/weather")

    from kortravelweather_api.routers import service_exports

    monkeypatch.setattr(FrozenCurrentExports, "page", fail)
    # 같은 세션의 alembic fileConfig가 이미 있던 logger를 끄므로 이 시험에서만 다시 켠다.
    monkeypatch.setattr(service_exports.logger, "disabled", False)
    logger = "kortravelweather_api.routers.service_exports"
    with caplog.at_level("WARNING", logger=logger):
        failed = _client(repository, tmp_path).get(
            "/v1/service/exports/current", headers={**HEADERS, "x-request-id": "trace-503"},
        )
        mixed = _client(repository, tmp_path, providers=["python-kma-api", "tomorrow_io"])
        unsupported = mixed.get("/v1/service/exports/current", headers=HEADERS)
    assert failed.status_code == unsupported.status_code == 503
    assert failed.json()["code"] == unsupported.json()["code"] == "EXPORT_NOT_READY"
    assert "resource=current reason=SQLAlchemyError request_id=trace-503" in caplog.text
    assert "resource=current reason=source_selection" in caplog.text
    assert "secret" not in caplog.text and "postgresql" not in caplog.text


@pytest.mark.parametrize("resource", ["current", "forecasts", "locations"])
def test_reused_first_page_handle_renews_its_ttl(repository, tmp_path, monkeypatch, resource):
    """만료 직전 handle을 재사용한 새 pagination도 다음 페이지에서410이 되지 않는다."""
    from kortravelweather import export_forecasts, export_snapshots
    from kortravelweather.export_snapshots import SNAPSHOT_TTL

    if resource == "current":
        _collect(repository, tmp_path)
        url, params, module = "/v1/service/exports/current", {"limit": 1}, export_snapshots
    elif resource == "forecasts":
        _collect_forecast(repository, tmp_path)
        url, params = "/v1/service/exports/forecasts", {"style": "ultra_short", "limit": 1}
        module = export_forecasts
    else:
        from kortravelweather import export_locations

        _collect(repository, tmp_path)
        url, params, module = "/v1/service/exports/locations", {"limit": 1}, export_locations
    client = _client(repository, tmp_path)
    first = client.get(url, params=params, headers=HEADERS)
    assert first.status_code == 200 and first.json()["meta"]["has_more"]
    snapshot_id = first.json()["meta"]["snapshot_id"]
    table, _ = export_snapshot_tables(Base.metadata)
    # 분 단위 여유를 둔 새 handle은 같은 시각의 조건부 요청마다 UPDATE하지 않는다.
    from sqlalchemy import event

    from kortravelweather import export_locations

    fixed = datetime.now(UTC)

    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz or UTC)

    for reader_module in (export_snapshots, export_forecasts, export_locations):
        monkeypatch.setattr(reader_module, "datetime", Fixed)
    with repository.engine.begin() as c:
        c.execute(update(table).where(table.c.snapshot_id == snapshot_id).values(
            expires_at=fixed + SNAPSHOT_TTL + timedelta(minutes=1),
        ))
    writes = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("UPDATE WEATHER_EXPORT_SNAPSHOTS"):
            writes.append(statement)

    event.listen(repository.engine, "before_cursor_execute", record)
    try:
        for _ in range(2):
            unchanged = client.get(
                url, params=params, headers={**HEADERS, "If-None-Match": first.headers["etag"]},
            )
            assert unchanged.status_code == 304
        assert writes == []
    finally:
        event.remove(repository.engine, "before_cursor_execute", record)
    with repository.engine.begin() as c:
        c.execute(update(table).where(table.c.snapshot_id == snapshot_id).values(
            expires_at=datetime.now(UTC) + timedelta(seconds=5),
        ))
    again = client.get(url, params=params, headers=HEADERS)
    assert again.status_code == 200 and again.json()["meta"]["snapshot_id"] == snapshot_id
    assert again.headers["etag"] == first.headers["etag"]
    with repository.engine.connect() as c:
        expires = c.scalar(select(table.c.expires_at).where(table.c.snapshot_id == snapshot_id))
    assert expires >= datetime.now(UTC) + SNAPSHOT_TTL - timedelta(seconds=30)
    later = datetime.now(UTC) + timedelta(seconds=6)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return later.astimezone(tz or UTC)

    monkeypatch.setattr(module, "datetime", Later)
    following = client.get(
        url, params={**params, "cursor": again.json()["meta"]["next_cursor"]}, headers=HEADERS,
    )
    assert following.status_code == 200
