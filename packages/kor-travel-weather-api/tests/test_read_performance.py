"""공개 SQL 마감, 불변 artifact 재사용과 cursor TTL의 실제 PostgreSQL 검증."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from kortravelweather_api.app import create_app
from sqlalchemy import select, text, update
from test_frozen_service_exports import (
    HEADERS,
    _client,
    _collect,
    _collect_forecast,
)

from kortravelweather import export_snapshots
from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore
from kortravelweather.export_forecasts import FrozenForecastExports
from kortravelweather.export_read_cache import SnapshotMetadataCache
from kortravelweather.export_snapshots import MAX_PAGE_ARTIFACTS, export_snapshot_tables
from kortravelweather.repository import Base
from kortravelweather.settings import WeatherSettings


def test_public_statement_deadline_returns_503_and_releases_connection(repository, monkeypatch):
    def slow():
        with repository.engine.connect() as connection:
            connection.execute(text("SELECT pg_sleep(.3)"))
        return {"ready": False, "items": []}

    monkeypatch.setattr(repository, "region_locations", slow)
    settings = WeatherSettings(environment="development", public_read_timeout_seconds=.04)
    client = TestClient(create_app(settings, repository))
    response = client.get("/v1/weather/regions")
    assert response.status_code == 503
    assert response.json()["code"] == "READ_TIMEOUT"
    assert response.headers["retry-after"] == "1"
    assert repository.engine.pool.checkedout() == 0
    with repository.engine.connect() as connection:
        assert connection.scalar(text("SELECT 1")) == 1
        assert connection.scalar(text("SHOW statement_timeout")) != "40ms"


def test_public_multiple_statements_share_one_deadline(repository, monkeypatch):
    def slow():
        with repository.engine.connect() as connection:
            connection.execute(text("SELECT pg_sleep(.04)"))
            connection.execute(text("SELECT pg_sleep(.04)"))
        return {"ready": False, "items": []}

    monkeypatch.setattr(repository, "region_locations", slow)
    client = TestClient(create_app(WeatherSettings(
        environment="development", public_read_timeout_seconds=.06,
    ), repository))
    assert client.get("/v1/weather/regions").status_code == 503
    assert repository.engine.pool.checkedout() == 0


@pytest.mark.parametrize("forecast", [False, True])
def test_conditional_page_reuse_reads_no_artifact(repository, tmp_path, monkeypatch, forecast):
    (_collect_forecast if forecast else _collect)(repository, tmp_path)
    client = _client(repository, tmp_path)
    url = "/v1/service/exports/forecasts" if forecast else "/v1/service/exports/current"
    params = {"limit": 1, **({"style": "ultra_short"} if forecast else {})}
    first = client.get(url, params=params, headers=HEADERS)
    assert first.status_code == 200, first.text
    assert first.headers["x-export-read-trace"] == "1"
    assert int(first.headers["x-export-artifact-read-calls"]) > 0
    if forecast:
        assert first.headers["x-export-forecast-input-trace"] == "db-verified-v1"
        assert first.headers["x-export-forecast-input-budget-bytes"] == str(8 * 1024 * 1024)
        assert 0 < int(first.headers["x-export-forecast-input-bytes"]) <= 8 * 1024 * 1024
        assert int(first.headers["x-export-forecast-input-refs"]) > 0
    else:
        assert "x-export-forecast-input-trace" not in first.headers

    def forbidden(*args, **kwargs):
        pytest.fail("304 must use metadata proof, not read artifact payload")

    monkeypatch.setattr(DatabaseExportArtifactStore, "_payload", forbidden)
    monkeypatch.setattr(DatabaseExportArtifactStore, "read_many", forbidden)
    repeated = client.get(url, params=params, headers={
        **HEADERS, "if-none-match": first.headers["etag"],
    })
    assert repeated.status_code == 304, repeated.text
    assert not repeated.content
    assert repeated.headers["x-export-read-trace"] == "1"
    assert repeated.headers["x-export-artifact-read-calls"] == "0"
    if forecast:
        assert repeated.headers["x-export-forecast-input-bytes"] == "0"
        assert repeated.headers["x-export-forecast-input-refs"] == "0"
    assert not list(tmp_path.glob("*.jsonl"))


@pytest.mark.parametrize("forecast", [False, True])
def test_live_cursor_renews_beyond_fifteen_minutes_but_never_revives_expired(
    repository, tmp_path, monkeypatch, forecast,
):
    from kortravelweather import export_forecasts

    (_collect_forecast if forecast else _collect)(repository, tmp_path)
    client = _client(repository, tmp_path)
    url = "/v1/service/exports/forecasts" if forecast else "/v1/service/exports/current"
    params = {"limit": 1, **({"style": "ultra_short"} if forecast else {})}
    first = client.get(url, params=params, headers=HEADERS)
    assert first.status_code == 200
    data = first.json()
    sid, cursor = data["meta"]["snapshot_id"], data["meta"]["next_cursor"]
    snapshots, _ = export_snapshot_tables(Base.metadata)
    origin = datetime.now(UTC)
    clock = [origin]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz is not None else clock[0].replace(tzinfo=None)

    monkeypatch.setattr(export_snapshots, "datetime", Clock)
    monkeypatch.setattr(export_forecasts, "datetime", Clock)
    for minute in (14, 28, 42):
        clock[0] = origin + timedelta(minutes=minute)
        response = client.get(url, params={**params, "cursor": cursor}, headers=HEADERS)
        assert response.status_code == 200, response.text
        with repository.engine.connect() as connection:
            expiry = connection.scalar(select(snapshots.c.expires_at).where(
                snapshots.c.snapshot_id == sid,
            ))
        assert expiry > clock[0]
    expired = clock[0] - timedelta(seconds=1)
    with repository.engine.begin() as connection:
        connection.execute(update(snapshots).where(
            snapshots.c.snapshot_id == sid,
        ).values(expires_at=expired))
    response = client.get(url, params={**params, "cursor": cursor}, headers=HEADERS)
    assert response.status_code == 410
    with repository.engine.connect() as connection:
        assert connection.scalar(select(snapshots.c.expires_at).where(
            snapshots.c.snapshot_id == sid,
        )) == expired


def test_snapshot_metadata_cache_has_fixed_ttl_and_size_bound():
    clock, loads = [0], []
    cache = SnapshotMetadataCache(clock=lambda: clock[0], max_bytes=200)

    def load():
        loads.append(1)
        return {"manifest": "x" * 30}

    cache.read("snapshot", load)
    clock[0] = 59
    cache.read("snapshot", load)
    assert len(loads) == 1
    clock[0] = 60
    cache.read("snapshot", load)
    assert len(loads) == 2
    for i in range(20):
        cache.read(i, load)
    assert cache.bytes <= 200
    assert len(cache.entries) <= 16


def test_forecast_scope_budget_preserves_complete_cursor_traversal():
    now = datetime.now(UTC)
    scopes = []
    points = {}
    for ordinal in range(MAX_PAGE_ARTIFACTS * 2 + 3):
        key, digest = str(ordinal), f"{ordinal:064x}"
        scopes.append({
            "location_ids": [key], "generation_id": key, "namespace": key,
            "published_at": now.isoformat(), "provider": "python-kma-api",
            "forecast_style": "short", "refs": [{
                "location_ids": [key], "artifact_digest": digest,
                "byte_count": 1, "point_count": 1,
            }],
        })
        points[digest] = [{
            "location_id": key, "provider": "python-kma-api", "forecast_style": "short",
            "target_at": (now + timedelta(hours=1)).isoformat(), "known_at": now.isoformat(),
            "metric_key": "TMP", "value_number": 1, "value_text": None,
        }]

    class Artifacts:
        reads = 0

        def read_shared(self, digest, **kwargs):
            self.reads += 1
            return points[digest]

    reader = object.__new__(FrozenForecastExports)
    reader.artifacts = Artifacts()
    reader.style, reader.since, reader.reuse_artifacts = "short", None, set()
    state, actual = {"g": 0, "p": 0}, []
    while True:
        before = reader.artifacts.reads
        rows, more = reader._page_items({"scopes": scopes}, state, 2000, now)
        assert reader.artifacts.reads - before <= MAX_PAGE_ARTIFACTS
        actual.extend(row["location_id"] for row in rows)
        if not more:
            break
    assert actual == [str(i) for i in range(len(scopes))]


def test_snapshot_extension_is_coalesced_and_has_absolute_lifetime_cap(
    repository, tmp_path,
):
    from sqlalchemy import event

    from kortravelweather.export_snapshots import SNAPSHOT_MAX_LIFETIME, FrozenCurrentExports

    _collect(repository, tmp_path)
    first = _client(repository, tmp_path).get(
        "/v1/service/exports/current", params={"limit": 1}, headers=HEADERS,
    )
    assert first.status_code == 200
    sid = first.json()["meta"]["snapshot_id"]
    table, _ = export_snapshot_tables(Base.metadata)
    now = datetime.now(UTC)
    ceiling = now + timedelta(minutes=8)
    with repository.engine.begin() as connection:
        connection.execute(update(table).where(table.c.snapshot_id == sid).values(
            created_at=ceiling - SNAPSHOT_MAX_LIFETIME,
            expires_at=now + timedelta(minutes=2),
        ))
        frozen = dict(connection.execute(select(table).where(
            table.c.snapshot_id == sid,
        )).mappings().one())
    reader = FrozenCurrentExports(
        repository, DatabaseExportArtifactStore(repository), signing_key="bounded-ttl",
    )
    updates = []

    def capture(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("UPDATE WEATHER_EXPORT_SNAPSHOTS"):
            updates.append(1)

    event.listen(repository.engine, "before_cursor_execute", capture)
    try:
        renewed = reader._renew_reused(dict(frozen, _reused=True))
        assert renewed["expires_at"] == ceiling
        second = reader._renew_reused(dict(renewed, _reused=True))
        assert second["expires_at"] == ceiling
        assert len(updates) == 1
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture)


def test_export_metrics_distinguish_first_cursor_and_304_without_identifiers(
    repository, tmp_path,
):
    from kortravelweather.metrics import metrics_payload

    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    url, params = "/v1/service/exports/current", {"limit": 1}
    first = client.get(url, params=params, headers=HEADERS)
    assert first.status_code == 200
    cursor = first.json()["meta"]["next_cursor"]
    assert client.get(url, params={**params, "cursor": cursor}, headers=HEADERS).status_code == 200
    assert client.get(url, params=params, headers={
        **HEADERS, "if-none-match": first.headers["etag"],
    }).status_code == 304
    samples = [line for line in metrics_payload().decode().splitlines()
               if line.startswith("ktw_read_stage_duration_seconds_count{")]
    for page in ("first", "cursor", "304"):
        assert any(f'page="{page}"' in line and 'resource="current"' in line for line in samples)
    for stage in ("freeze", "metadata", "artifact", "render", "worker_queue"):
        assert any(f'stage="{stage}"' in line for line in samples)
    assert not any(cursor in line or "location_id=" in line for line in samples)


def test_pool_wait_uses_remaining_request_budget_and_keeps_shared_baseline(
    weather_test_database_url,
):
    from time import monotonic

    from sqlalchemy import create_engine
    from sqlalchemy.exc import TimeoutError as PoolTimeoutError

    from kortravelweather.read_deadline import PUBLIC_READ_DEADLINE, ReadDeadlineQueuePool

    engine = create_engine(
        weather_test_database_url, poolclass=ReadDeadlineQueuePool,
        pool_size=1, max_overflow=0, pool_timeout=15,
    )
    try:
        with engine.connect():
            started = monotonic()
            token = PUBLIC_READ_DEADLINE.set(started + .04)
            try:
                with pytest.raises(PoolTimeoutError):
                    engine.connect()
                assert monotonic() - started < .5
            finally:
                PUBLIC_READ_DEADLINE.reset(token)
            assert engine.pool.timeout() == 15
            assert engine.pool.checkedout() == 1  # 원래 보유한 연결 하나만 남는다
        assert engine.pool.checkedout() == 0
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
    finally:
        engine.dispose()


def test_cursor_metadata_reuses_manifest_but_reads_live_expiry_each_time(
    repository, tmp_path,
):
    from sqlalchemy import event

    _collect_forecast(repository, tmp_path)
    first = _client(repository, tmp_path).get(
        "/v1/service/exports/forecasts", params={"style": "ultra_short", "limit": 1},
        headers=HEADERS,
    )
    assert first.status_code == 200
    sid = first.json()["meta"]["snapshot_id"]
    reader = FrozenForecastExports(
        repository, DatabaseExportArtifactStore(repository), signing_key="manifest-cache",
        style="ultra_short", snapshot_cache=SnapshotMetadataCache(),
    )
    statements = []

    def capture(connection, cursor, statement, parameters, context, many):
        if (statement.lstrip().upper().startswith("SELECT")
                and "FROM weather_export_snapshots" in statement):
            statements.append(statement)

    event.listen(repository.engine, "before_cursor_execute", capture)
    try:
        cold = reader._read_cursor_snapshot(sid)
        assert cold["snapshot_id"] == sid
        assert len(statements) == 2
        for _page_limit in (200, 2000):
            statements.clear()
            warm = reader._read_cursor_snapshot(sid)
            assert warm["manifest_digest"] == cold["manifest_digest"]
            assert len(statements) == 1
            assert "weather_export_snapshots.manifest," not in statements[0]
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture)


def test_public_deadline_preserves_read_only_repeatable_read_setup(repository):
    from kortravelweather_api.read_limits import public_read_deadline

    create_app(WeatherSettings(environment="development"), repository)
    with public_read_deadline(1), repository.engine.connect() as connection:
        connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        assert connection.scalar(text("SELECT 1")) == 1
    assert repository.engine.pool.checkedout() == 0


def test_snapshot_expiring_while_waiting_for_row_lock_is_not_revived(repository, tmp_path):
    import threading

    from kortravelweather.export_snapshots import FrozenCurrentExports

    _collect(repository, tmp_path)
    first = _client(repository, tmp_path).get(
        "/v1/service/exports/current", params={"limit": 1}, headers=HEADERS,
    )
    assert first.status_code == 200
    sid = first.json()["meta"]["snapshot_id"]
    table, _ = export_snapshot_tables(Base.metadata)
    expires = datetime.now(UTC) + timedelta(milliseconds=150)
    with repository.engine.begin() as connection:
        connection.execute(update(table).where(
            table.c.snapshot_id == sid,
        ).values(expires_at=expires))
        frozen = dict(connection.execute(select(table).where(
            table.c.snapshot_id == sid,
        )).mappings().one())
    held, release = threading.Event(), threading.Event()

    def hold():
        with repository.engine.begin() as connection:
            connection.execute(select(table.c.snapshot_id).where(
                table.c.snapshot_id == sid,
            ).with_for_update())
            held.set()
            assert release.wait(3)

    worker = threading.Thread(target=hold)
    worker.start()
    assert held.wait(3)
    timer = threading.Timer(.3, release.set)
    timer.start()
    try:
        reader = FrozenCurrentExports(
            repository, DatabaseExportArtifactStore(repository), signing_key="expiry-lock-race",
        )
        assert reader._renew_reused(dict(frozen, _reused=True))["expires_at"] == expires
    finally:
        release.set()
        timer.cancel()
        worker.join(3)
    with repository.engine.connect() as connection:
        assert connection.scalar(select(table.c.expires_at).where(
            table.c.snapshot_id == sid,
        )) == expires
    assert not worker.is_alive()


def test_public_pool_wait_uses_deadline_without_changing_collector_pool(repository, monkeypatch):
    from contextlib import ExitStack
    from time import monotonic

    def read():
        with repository.engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
        return {"ready": False, "items": []}

    monkeypatch.setattr(repository, "region_locations", read)
    client = TestClient(create_app(WeatherSettings(
        environment="development", public_read_timeout_seconds=.05,
    ), repository))
    # 현재 공용 pool 10 + overflow 10을 실제 PostgreSQL 연결로 채운다.
    with ExitStack() as held:
        for _ in range(20):
            held.enter_context(repository.engine.connect())
        started = monotonic()
        response = client.get("/v1/weather/regions")
        assert response.status_code == 503
        assert response.json()["code"] == "POOL_BUSY"
        assert monotonic() - started < 1
    assert repository.engine.pool.checkedout() == 0
    assert repository.engine.pool.timeout() == 15
    assert client.get("/v1/weather/regions").status_code == 200


def test_forecast_input_headers_show_single_oversized_progress_cursor_and_reused_304(
    repository, tmp_path, monkeypatch,
):
    from kortravelweather import export_forecasts

    _collect_forecast(repository, tmp_path)
    monkeypatch.setattr(export_forecasts, "MAX_PAGE_INPUT_BYTES", 1)
    client = _client(repository, tmp_path)
    path, params = "/v1/service/exports/forecasts", {"style": "ultra_short", "limit": 2000}
    first = client.get(path, params=params, headers=HEADERS)
    assert first.status_code == 200, first.text
    assert first.headers.get("x-export-forecast-input-trace") == "db-verified-v1"
    assert first.headers["x-export-forecast-input-budget-bytes"] == "1"
    assert int(first.headers["x-export-forecast-input-bytes"]) > 1
    assert first.headers["x-export-forecast-input-refs"] == "1"
    assert first.json()["meta"]["has_more"]
    following = client.get(path, params={
        **params, "cursor": first.json()["meta"]["next_cursor"],
    }, headers=HEADERS)
    assert following.status_code == 200, following.text
    assert following.headers["x-export-forecast-input-refs"] == "1"
    assert following.headers["x-export-forecast-input-budget-bytes"] == "1"
    assert int(following.headers["x-export-forecast-input-bytes"]) > 1
    assert not following.json()["meta"]["has_more"]
    keys = [p["location_id"] for p in first.json()["items"] + following.json()["items"]]
    assert sorted(keys) == ["a", "b", "c"]
    repeated = client.get(path, params=params, headers={
        **HEADERS, "if-none-match": first.headers["etag"],
    })
    assert repeated.status_code == 304 and repeated.content == b""
    assert repeated.headers["x-export-forecast-input-trace"] == "db-verified-v1"
    assert repeated.headers["x-export-forecast-input-bytes"] == "0"
    assert repeated.headers["x-export-forecast-input-refs"] == "0"
    assert repeated.headers["x-export-forecast-input-budget-bytes"] == "1"
