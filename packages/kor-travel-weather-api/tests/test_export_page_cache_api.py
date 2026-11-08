"""완성 페이지 재사용은 인증·수명·실파일 검사를 건너뛰지 않는다."""

from test_frozen_service_exports import HEADERS, _client, _collect
from test_frozen_service_exports import repository as repository

from kortravelweather.export_snapshots import FrozenCurrentExports


def test_completed_current_page_skips_freeze_but_keeps_auth_gzip_etag(
    repository, tmp_path, monkeypatch,
):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    url = "/v1/service/exports/current?limit=1"
    first = client.get(url, headers={**HEADERS, "accept-encoding": "identity"})
    assert first.status_code == 200 and first.headers["x-export-read"] == "generated"

    def no_freeze(*args, **kwargs):
        raise AssertionError("검증 완료된 페이지를 다시 구성했습니다.")

    monkeypatch.setattr(FrozenCurrentExports, "_freeze", no_freeze)
    # 인증 실패는 cache hit가 있어도 같고, 검증한 payload와 원천 clock을 그대로 쓴다.
    assert client.get(url).status_code == 401
    again = client.get(url, headers={**HEADERS, "accept-encoding": "gzip"})
    assert again.status_code == 200 and again.headers["x-export-read"] == "reused"
    before, after = first.json(), again.json()
    before["meta"].pop("generated_at")
    after["meta"].pop("generated_at")
    assert after == before
    conditional = client.get(url, headers={**HEADERS, "if-none-match": first.headers["etag"]})
    assert conditional.status_code == 304 and not conditional.content
    assert conditional.headers["x-export-read"] == "reused"
    assert again.headers["etag"] == first.headers["etag"]


def test_reused_page_rehashes_real_artifact(repository, tmp_path):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    url = "/v1/service/exports/current"
    first = client.get(url, headers=HEADERS)
    assert first.status_code == 200
    for path in tmp_path.glob("*.jsonl"):
        path.chmod(0o600)
        original = path.read_bytes()
        path.write_bytes(original.replace(b"18.4", b"28.4"))
    broken = client.get(url, headers={**HEADERS, "if-none-match": first.headers["etag"]})
    assert broken.status_code == 503 and "etag" not in broken.headers


def test_completed_page_does_not_hold_global_gc_lock(repository, tmp_path):
    from kortravelweather.export_artifacts import ExportArtifactStore

    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    url = "/v1/service/exports/current"
    first = client.get(url, headers=HEADERS)
    assert first.status_code == 200
    # 유효 snapshot의 파일은 GC root로 보존된다. 이미 pin된 파일을 읽기 위해 GC 전역
    # 잠금을 다시 기다리지 않으면서 실제 파일 hash/count 검산은 계속한다.
    with ExportArtifactStore(tmp_path).guard(holder="gc"):
        response = client.get(url, headers={**HEADERS, "if-none-match": first.headers["etag"]})
    assert response.status_code == 304 and response.headers["x-export-read"] == "reused"


def test_manifest_selection_is_outside_lock_but_new_pin_commit_is_inside(
    repository, tmp_path, monkeypatch,
):
    from contextlib import contextmanager

    from sqlalchemy import event

    from kortravelweather.export_artifacts import ExportArtifactStore

    _collect(repository, tmp_path)
    original = ExportArtifactStore.guard
    held = []
    commits = []

    @contextmanager
    def observe(store, **kwargs):
        with original(store, **kwargs):
            held.append(True)
            try:
                yield
            finally:
                held.pop()

    def before(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT") and "weather_locations" in statement:
            assert not held, "전체 승인/카탈로그 조회 동안 파일 guard를 잡았습니다."
        if statement.lstrip().upper().startswith("INSERT INTO WEATHER_EXPORT_SNAPSHOTS"):
            assert held

    def committed(connection):
        if held:
            commits.append(True)

    monkeypatch.setattr(ExportArtifactStore, "guard", observe)
    event.listen(repository.engine, "before_cursor_execute", before)
    event.listen(repository.engine, "commit", committed)
    try:
        response = _client(repository, tmp_path).get("/v1/service/exports/current", headers=HEADERS)
    finally:
        event.remove(repository.engine, "before_cursor_execute", before)
        event.remove(repository.engine, "commit", committed)
    assert response.status_code == 200 and commits


def test_cache_never_returns_old_response_past_selection_deadline(
    repository, tmp_path, monkeypatch,
):
    from kortravelweather import export_snapshots

    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)
    clock = [export_snapshots.monotonic()]
    monkeypatch.setattr(export_snapshots, "monotonic", lambda: clock[0])
    client.app.state.export_selection_cache.clock = lambda: clock[0]
    url = "/v1/service/exports/current"
    first = client.get(url, headers=HEADERS)
    assert first.status_code == 200
    clock[0] += 59
    assert client.get(url, headers=HEADERS).headers["x-export-read"] == "reused"
    clock[0] += 1

    def failed_refresh(*args, **kwargs):
        from kortravelweather.export_artifacts import ExportGuardTimeout
        raise ExportGuardTimeout("검증용 갱신 실패")

    monkeypatch.setattr(FrozenCurrentExports, "_freeze", failed_refresh)
    expired = client.get(url, headers={**HEADERS, "if-none-match": first.headers["etag"]})
    assert expired.status_code == 503 and "etag" not in expired.headers


def test_page_miss_does_not_inherit_another_pages_expiring_selection(
    repository, tmp_path, monkeypatch,
):
    _collect(repository, tmp_path)
    client = _client(repository, tmp_path)

    def old_selection(*args, **kwargs):
        raise AssertionError("다른 페이지의 만료 직전 승인 수명을 이어받았습니다.")

    monkeypatch.setattr(client.app.state.export_selection_cache, "read", old_selection)
    first = client.get("/v1/service/exports/current?limit=1", headers=HEADERS)
    assert first.status_code == 200 and first.headers["x-export-read"] == "generated"
    second = client.get("/v1/service/exports/current?limit=2", headers=HEADERS)
    assert second.status_code == 200 and second.headers["x-export-read"] == "generated"
