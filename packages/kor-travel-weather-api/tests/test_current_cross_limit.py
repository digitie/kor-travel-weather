"""CURRENT의 limit 변경은 원래 lease만 잇고 값/DTO/전체 건강도를 다시 읽는다."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from time import perf_counter

import pytest
from sqlalchemy import delete, event, select
from test_export_heads import _candidate
from test_frozen_service_exports import HEADERS, _client, _collect
from test_frozen_service_exports import repository as repository

from kortravelweather import export_snapshots
from kortravelweather.export_capacity import ExportCapacityExceeded
from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore
from kortravelweather.export_heads import ExportHeads, ScopeDescriptor
from kortravelweather.export_snapshots import ExportSnapshotUnavailable, FrozenCurrentExports
from kortravelweather.models import WeatherLocation


def _entry(client, limit):
    return next(
        v
        for k, v in client.app.state.export_page_cache.entries.items()
        if k[1:4] == ("current", limit, None)
    )


def _clock(client, monkeypatch):
    clock = [export_snapshots.monotonic()]
    monkeypatch.setattr(export_snapshots, "monotonic", lambda: clock[0])
    for cache in (
        client.app.state.export_page_cache,
        client.app.state.export_selection_cache,
        client.app.state.export_snapshot_cache,
    ):
        cache.clock = lambda: clock[0]
    return clock


def _freeze_calls(monkeypatch):
    calls = []
    original = FrozenCurrentExports._freeze

    def counted(self):
        calls.append(1)
        return original(self)

    monkeypatch.setattr(FrozenCurrentExports, "_freeze", counted)
    return calls


def _large_catalog(repo, root):
    store = DatabaseExportArtifactStore(repo)
    for nx in (60, 61):
        keys = tuple(f"g{nx}-{i:03d}" for i in range(180))
        for key in keys:
            repo.create_location(
                WeatherLocation(
                    location_id=key,
                    name=key,
                    latitude=37.5,
                    longitude=127,
                    nx=nx,
                    ny=127,
                )
            )
        descriptor = ScopeDescriptor(
            entity_type="weather_response",
            entity_id=f"grid:{nx}:127",
            nx=nx,
            ny=127,
            location_ids=keys,
            normalizer_revision="kma-v1",
            winner_policy_revision="kma-base-v1",
        )

        def many_metrics(record, nx=nx):
            payload = record["payload"]
            row = payload["rows"][0]
            payload["rows"] = [
                dict(row, nx=nx, category=k, obsrValue=v)
                for k, v in (
                    ("T1H", "18.4"),
                    ("REH", "50"),
                    ("RN1", "0"),
                    ("UUU", "0"),
                    ("VVV", "0"),
                    ("PTY", "0"),
                    ("VEC", "120"),
                    ("WSD", "1"),
                )
            ]
            meta = payload["response_metadata"]
            meta["request_params"]["nx"] = nx
            meta["pagination"].update(expected_total_count=8, received_item_count=8)

        heads, identity, capture, *_ = _candidate(
            repo,
            root,
            descriptor,
            artifacts=store,
            record_mutator=many_metrics,
            revision=datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
            - timedelta(hours=1),
        )
        assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    return store


def _body(response):
    value = response.json()
    value["meta"].pop("generated_at")
    return value


def test_200_then_2000_reads_new_artifacts_matches_real_cold_and_keeps_cursor(
    repository,
    tmp_path,
    monkeypatch,
):
    _large_catalog(repository, tmp_path)
    client = _client(repository, tmp_path)
    calls = _freeze_calls(monkeypatch)
    first = client.get("/v1/service/exports/current?limit=200", headers=HEADERS)
    assert first.status_code == 200 and len(first.json()["items"]) == 200
    original = _entry(client, 200)
    statements = []

    def capture(_c, _cursor, sql, _params, _context, _many):
        statements.append(sql)

    event.listen(repository.engine, "before_cursor_execute", capture)
    started = perf_counter()
    try:
        second = client.get(
            "/v1/service/exports/current?limit=2000",
            headers={**HEADERS, "if-none-match": first.headers["etag"]},
        )
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture)
    seeded_ms = (perf_counter() - started) * 1000
    assert second.status_code == 200 and len(second.json()["items"]) == 2000
    assert len(calls) == 1
    assert int(second.headers["x-export-artifact-read-calls"]) > 0
    current = _entry(client, 2000)
    assert current.result is not original.result
    for name in ("reuse_generations", "reuse_namespaces", "reuse_artifacts"):
        assert getattr(current.reader, name) is not getattr(original.reader, name)
    assert len(current.reader.reuse_artifacts) > len(original.reader.reuse_artifacts)
    assert current.deadline <= original.deadline
    assert current.reader.selection_checked_after == original.reader.selection_checked_after
    assert current.reader.reuse_wall_started == original.reader.reuse_wall_started
    assert any("weather_locations" in sql and sql.startswith("SELECT") for sql in statements)
    assert any("weather_export_scope_owners" in sql for sql in statements)
    assert not any(sql.startswith("UPDATE weather_export_snapshots") for sql in statements)
    cold_client = _client(repository, tmp_path)
    started = perf_counter()
    cold = cold_client.get("/v1/service/exports/current?limit=2000", headers=HEADERS)
    cold_ms = (perf_counter() - started) * 1000
    assert cold.status_code == 200 and len(calls) == 2
    assert _body(second) == _body(cold)
    assert second.headers["etag"] == cold.headers["etag"] != first.headers["etag"]
    cursor = second.json()["meta"]["next_cursor"]
    assert cursor and cursor == cold.json()["meta"]["next_cursor"]
    wrong_limit = client.get(
        "/v1/service/exports/current", params={"limit": 200, "cursor": cursor}, headers=HEADERS
    )
    assert wrong_limit.status_code == 422
    following = client.get(
        "/v1/service/exports/current", params={"limit": 2000, "cursor": cursor}, headers=HEADERS
    )
    assert following.status_code == 200
    assert client.get("/v1/service/exports/current?limit=2000").status_code == 401
    assert (
        client.get(
            "/v1/service/exports/current?limit=2000",
            headers={**HEADERS, "if-none-match": second.headers["etag"]},
        ).status_code
        == 304
    )
    print(
        "CROSS_LIMIT_LOCAL_PROOF "
        + json.dumps(
            {
                "production": False,
                "firstLimit": 200,
                "newLimit": 2000,
                "freezeAdditional": 0,
                "newArtifactReadCalls": int(second.headers["x-export-artifact-read-calls"]),
                "seededWallMs": seeded_ms,
                "coldWallMs": cold_ms,
                "bodySHA256": hashlib.sha256(
                    json.dumps(_body(second), sort_keys=True).encode()
                ).hexdigest(),
                "bodyEtagCursorEqual": True,
                "newArtifactRefs": len(current.reader.reuse_artifacts),
                "oldArtifactRefs": len(original.reader.reuse_artifacts),
            }
        )
    )


def test_seed_does_not_renew_db_expiry_or_restart_clocks(repository, tmp_path, monkeypatch):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    clock = _clock(client, monkeypatch)
    assert client.get("/v1/service/exports/current?limit=1", headers=HEADERS).status_code == 200
    first = _entry(client, 1)
    expiry = first.reader.reuse_snapshot_expires_at
    wall = datetime.now(UTC) + timedelta(seconds=40)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return wall.astimezone(tz)

    monkeypatch.setattr(export_snapshots, "datetime", Later)
    clock[0] += 40
    statements = []

    def capture(_c, _cursor, sql, _params, _context, _many):
        statements.append(sql)

    event.listen(repository.engine, "before_cursor_execute", capture)
    try:
        response = client.get("/v1/service/exports/current?limit=2", headers=HEADERS)
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    second = _entry(client, 2)
    assert second.deadline <= first.deadline
    assert second.reader.reuse_snapshot_expires_at == expiry
    assert second.reader.reuse_wall_started == first.reader.reuse_wall_started
    with repository.engine.connect() as c:
        assert (
            c.scalar(
                select(first.reader.snapshots.c.expires_at).where(
                    first.reader.snapshots.c.snapshot_id == first.reader.reuse_snapshot[0],
                )
            )
            == expiry
        )
    assert not any(sql.startswith("UPDATE weather_export_snapshots") for sql in statements)
    # metadata cache를 비워 다시 DB에서 읽고 limit을 또 바꿔도 원래 수명만 남는다.
    metadata_cache = client.app.state.export_snapshot_cache
    metadata_cache.entries.clear()
    metadata_cache.bytes = 0
    clock[0] += 10
    wall += timedelta(seconds=10)
    assert client.get("/v1/service/exports/current?limit=3", headers=HEADERS).status_code == 200
    third = _entry(client, 3)
    assert third.deadline <= first.deadline
    assert third.reader.reuse_snapshot_expires_at == expiry
    assert third.reader.selection_checked_after == first.reader.selection_checked_after


def test_expired_cross_limit_build_restarts_once_with_clean_fresh_proof(
    repository, tmp_path, monkeypatch,
):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    clock = _clock(client, monkeypatch)
    calls, before_fresh = [], []
    original_freeze = FrozenCurrentExports._freeze
    old = None

    def counted_freeze(self):
        calls.append(1)
        if old is not None:
            # fallback은 새 reader와 같은 초기 상태에서 전체 승인을 다시 읽어야 한다.
            assert self.reuse_snapshot is self.reuse_liveness is None
            assert not self.reuse_generations and not self.reuse_namespaces
            assert not self.reuse_artifacts
            assert self.selection_deadline is None and self.selection_min_remaining is None
            assert getattr(self, "_current_seed_expiry", None) is None
            with repository.engine.connect() as c:
                expiry = c.scalar(select(self.snapshots.c.expires_at).where(
                    self.snapshots.c.snapshot_id == old.reader.reuse_snapshot[0],
                ))
            before_fresh.append(expiry)
            assert expiry == old.reader.reuse_snapshot_expires_at
        return original_freeze(self)

    monkeypatch.setattr(FrozenCurrentExports, "_freeze", counted_freeze)
    assert client.get("/v1/service/exports/current?limit=1", headers=HEADERS).status_code == 200
    old = _entry(client, 1)
    old_artifacts = set(old.reader.reuse_artifacts)
    clock[0] = old.deadline - 0.1
    original = DatabaseExportArtifactStore.read_many

    def expire_during_seed_read(store, refs, **kwargs):
        result = original(store, refs, **kwargs)
        clock[0] = old.deadline + 0.001
        return result

    with monkeypatch.context() as patch:
        patch.setattr(DatabaseExportArtifactStore, "read_many", expire_during_seed_read)
        response = client.get("/v1/service/exports/current?limit=2", headers=HEADERS)
    assert response.status_code == 200
    assert len(calls) == 2 and len(before_fresh) == 1
    fresh = _entry(client, 2)
    assert fresh.deadline > old.deadline
    assert fresh.reader.selection_checked_after >= old.deadline
    assert old.reader.reuse_artifacts == old_artifacts
    assert old.reader.selection_deadline <= old.deadline
    # 같은 시각의 원래 cold 요청과 출력/ETag/cursor를 직접 대조한다.
    baseline = _client(repository, tmp_path)
    for cache in (baseline.app.state.export_page_cache,
                  baseline.app.state.export_selection_cache,
                  baseline.app.state.export_snapshot_cache):
        cache.clock = lambda: clock[0]
    cold = baseline.get("/v1/service/exports/current?limit=2", headers=HEADERS)
    assert cold.status_code == 200 and len(calls) == 3
    assert _body(response) == _body(cold)
    assert response.headers["etag"] == cold.headers["etag"]


@pytest.mark.parametrize("target", ["head", "generation", "plan", "snapshot", "expired_snapshot"])
def test_cross_limit_keeps_delete_invalidation(repository, tmp_path, target):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    assert client.get("/v1/service/exports/current?limit=1", headers=HEADERS).status_code == 200
    heads = ExportHeads(repository)
    tables = {
        "head": heads.heads,
        "generation": heads.generations,
        "plan": heads.plans,
        "snapshot": _entry(client, 1).reader.snapshots,
    }
    with repository.engine.begin() as c:
        if target == "generation":
            # 실제 FK를 해제한 뒤 삭제한다. 무결성/liveness trigger는 비활성화하지 않는다.
            for table in (
                heads.heads,
                heads.refs,
                _entry(client, 1).reader.refs,
                heads.metadata.tables["weather_export_approved_locations"],
            ):
                c.execute(delete(table))
        if target == "expired_snapshot":
            table = tables["snapshot"]
            c.execute(table.update().values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        else:
            c.execute(delete(tables[target]))
    response = client.get("/v1/service/exports/current?limit=2", headers=HEADERS)
    if target in {"generation", "plan"}:
        assert response.status_code == 503 and "etag" not in response.headers
    else:
        assert response.status_code == 200
        if target == "head":
            assert response.json()["meta"]["source_status"] == "stale"
        else:
            assert (
                response.json()["meta"]["snapshot_id"] != _entry(client, 1).reader.reuse_snapshot[0]
            )


@pytest.mark.parametrize("capacity", [False, True])
def test_fallback_current_is_never_seeded_or_upgraded_to_ok(
    repository,
    tmp_path,
    monkeypatch,
    capacity,
):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    assert client.get("/v1/service/exports/current?limit=1", headers=HEADERS).status_code == 200
    client.app.state.export_page_cache.entries.clear()
    client.app.state.export_page_cache.bytes = 0
    calls = []

    def unavailable(self):
        calls.append(1)
        if capacity:
            raise ExportCapacityExceeded("fixture capacity fallback")
        raise ExportSnapshotUnavailable("fixture source fallback")

    monkeypatch.setattr(FrozenCurrentExports, "_freeze", unavailable)
    first = client.get("/v1/service/exports/current?limit=1", headers=HEADERS)
    assert first.status_code == 200 and first.json()["meta"]["source_status"] == "stale"
    assert not _entry(client, 1).reader._current_seed_eligible
    second = client.get("/v1/service/exports/current?limit=2", headers=HEADERS)
    assert second.status_code == 200 and second.json()["meta"]["source_status"] == "stale"
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["deleted", "corrupt"])
def test_new_limit_validates_previously_unread_artifact_tail(
    repository,
    tmp_path,
    monkeypatch,
    failure,
):
    store = _large_catalog(repository, tmp_path)
    client = _client(repository, tmp_path)
    calls = _freeze_calls(monkeypatch)
    assert client.get("/v1/service/exports/current?limit=200", headers=HEADERS).status_code == 200
    first = _entry(client, 200)
    with repository.engine.connect() as c:
        all_digests = set(c.scalars(select(store.table.c.digest)))
    unread = all_digests - {ref[0] for ref in first.reader.reuse_artifacts}
    assert len(unread) == 1
    digest = unread.pop()
    if failure == "deleted":
        with store.guard(holder="fixture-tail-delete"):
            count, _ = store.delete_many([digest], created_before=datetime.max.replace(tzinfo=UTC))
            assert count == 1
    else:
        # 실제 DB SELECT 후 fault를 주입한다. 원래 SHA/bytes/count 검산은 그대로 호출한다.
        from types import SimpleNamespace

        original = DatabaseExportArtifactStore._verify_payload_row

        def corrupted(row, actual_digest, byte_count, point_count):
            if actual_digest == digest:
                payload = bytes(row.payload)
                row = SimpleNamespace(
                    payload=b"!" + payload[1:],
                    byte_count=row.byte_count,
                    point_count=row.point_count,
                )
            return original(row, actual_digest, byte_count, point_count)

        monkeypatch.setattr(
            DatabaseExportArtifactStore, "_verify_payload_row", staticmethod(corrupted)
        )
    response = client.get("/v1/service/exports/current?limit=2000", headers=HEADERS)
    assert response.status_code == 503 and "etag" not in response.headers
    assert len(calls) == 1


def test_seed_keeps_global_failed_run_health_and_new_etag(repository, tmp_path, monkeypatch):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    calls = _freeze_calls(monkeypatch)
    first = client.get("/v1/service/exports/current?limit=1", headers=HEADERS)
    assert first.status_code == 200 and first.json()["meta"]["source_status"] == "ok"
    runs = ExportHeads(repository).metadata.tables["weather_sync_runs"]
    with repository.engine.begin() as c:
        c.execute(runs.update().values(status="failed"))
    second = client.get("/v1/service/exports/current?limit=2", headers=HEADERS)
    assert second.status_code == 200 and second.json()["meta"]["source_status"] == "stale"
    assert second.headers["etag"] != first.headers["etag"]
    assert second.json()["meta"]["snapshot_id"] == first.json()["meta"]["snapshot_id"]
    assert len(calls) == 1


def test_seed_keeps_global_source_age_boundary(repository, tmp_path, monkeypatch):
    from kortravelweather import export_inventory_selection

    _collect(repository, tmp_path)
    heads = ExportHeads(repository)
    with repository.engine.connect() as c:
        revision = min(c.scalars(select(heads.generations.c.data_revision_at)))
    wall = revision + timedelta(hours=3) - timedelta(seconds=20)

    class WallClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return wall.astimezone(tz)

    monkeypatch.setattr(export_snapshots, "datetime", WallClock)
    monkeypatch.setattr(export_inventory_selection, "datetime", WallClock)
    client = _client(repository, tmp_path)
    clock = _clock(client, monkeypatch)
    calls = _freeze_calls(monkeypatch)
    first = client.get("/v1/service/exports/current?limit=1", headers=HEADERS)
    assert first.status_code == 200 and first.json()["meta"]["source_status"] == "ok"
    wall += timedelta(seconds=30)
    clock[0] += 30
    second = client.get("/v1/service/exports/current?limit=2", headers=HEADERS)
    assert second.status_code == 200 and second.json()["meta"]["source_status"] == "stale"
    assert len(calls) == 1


def test_expired_seed_does_not_retry_actual_sql_error(repository, tmp_path, monkeypatch):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    clock = _clock(client, monkeypatch)
    calls = _freeze_calls(monkeypatch)
    assert client.get("/v1/service/exports/current?limit=1", headers=HEADERS).status_code == 200
    old = _entry(client, 1)
    clock[0] = old.deadline - 0.1
    original = DatabaseExportArtifactStore.read_many

    def sql_failure(store, refs, **kwargs):
        original(store, refs, **kwargs)
        clock[0] = old.deadline + 0.001
        with repository.engine.connect() as c:
            c.exec_driver_sql("SELECT 1 / 0")
        pytest.fail("actual SQL failure was not raised")

    monkeypatch.setattr(DatabaseExportArtifactStore, "read_many", sql_failure)
    response = client.get("/v1/service/exports/current?limit=2", headers=HEADERS)
    assert response.status_code == 503 and "etag" not in response.headers
    assert len(calls) == 1


def test_seed_expiry_cold_fallback_failure_is_not_retried(repository, tmp_path, monkeypatch):
    from kortravelweather.export_artifacts import ExportArtifactError

    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    clock = _clock(client, monkeypatch)
    assert client.get("/v1/service/exports/current?limit=1", headers=HEADERS).status_code == 200
    old = _entry(client, 1)
    clock[0] = old.deadline - 0.1
    original = DatabaseExportArtifactStore.read_many

    def expire(store, refs, **kwargs):
        result = original(store, refs, **kwargs)
        clock[0] = old.deadline + 0.001
        return result
    attempts = []

    def fresh_failure(self):
        attempts.append(1)
        raise ExportArtifactError("fixture fresh full validation failed")

    monkeypatch.setattr(DatabaseExportArtifactStore, "read_many", expire)
    monkeypatch.setattr(FrozenCurrentExports, "_freeze", fresh_failure)
    response = client.get("/v1/service/exports/current?limit=2", headers=HEADERS)
    assert response.status_code == 503 and "etag" not in response.headers
    assert attempts == [1]
