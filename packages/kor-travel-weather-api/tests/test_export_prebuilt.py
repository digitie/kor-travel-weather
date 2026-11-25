"""Map 표준 프로필(current) 사전 구성 페이지(호출 검토 A3)의 읽기 경로·handle 수명·fallback."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from kortravelweather_dagster.kma_weather import KmaAllGridsFailedError
from sqlalchemy import delete, event, func, insert, select, text, update
from test_frozen_service_exports import HEADERS, TOKEN, _client, _collect
from test_frozen_service_exports import repository as repository

from kortravelweather import export_prebuilt
from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore
from kortravelweather.export_prebuilt import build_current_profile, export_prebuilt_tables
from kortravelweather.export_snapshots import SNAPSHOT_TTL, export_snapshot_tables
from kortravelweather.repository import Base
from kortravelweather.settings import WeatherSettings

URL = "/v1/service/exports/current"
LIMIT = 5
#: 승인 선택·상태 판정이 읽는 큰 표. builder 판정을 이어 쓰는 첫 페이지는 읽지 않는다.
SELECTION_TABLES = ("weather_export_scope_owners", "weather_export_attempts",
                    "weather_export_generations", "weather_export_scope_plans",
                    "weather_sync_runs")


@pytest.fixture(autouse=True)
def _postgresql_only(repository):
    if repository.engine.dialect.name != "postgresql":
        pytest.skip("사전 구성은 PostgreSQL 삭제 epoch가 있어야 한다.")


def _settings(tmp_path, *, enabled=True):
    return WeatherSettings(
        environment="development", admin_token=TOKEN, enabled_providers=["python-kma-api"],
        service_exports_enabled=True, service_export_artifact_root=tmp_path,
        service_export_prebuild_enabled=enabled,
    )


def _build(repository, tmp_path, **kwargs):
    return build_current_profile(repository, _settings(tmp_path, **kwargs), limit=LIMIT)


def _app(repository, tmp_path, *, enabled=True):
    client = _client(repository, tmp_path)
    client.app.state.settings = _settings(tmp_path, enabled=enabled)
    return client


def _walk(client, *, headers=None):
    pages = [client.get(URL, params={"limit": LIMIT}, headers={**HEADERS, **(headers or {})})]
    assert pages[0].status_code == 200, pages[0].text
    while pages[-1].json()["meta"]["next_cursor"]:
        page = client.get(URL, params={
            "limit": LIMIT, "cursor": pages[-1].json()["meta"]["next_cursor"],
        }, headers=HEADERS)
        assert page.status_code == 200, page.text
        pages.append(page)
    return pages


def _items(pages):
    return [item for page in pages for item in page.json()["items"]]


def _builds(repository):
    builds, _ = export_prebuilt_tables(Base.metadata)
    with repository.engine.connect() as connection:
        return connection.execute(select(builds).order_by(
            builds.c.status_checked_at,
        )).mappings().all()


def _metric(name, label):
    from kortravelweather.metrics import metrics_payload

    for line in metrics_payload().decode().splitlines():
        if line.startswith(f"{name}{{{label}}} "):
            return float(line.split()[-1])
    return 0.0


def _prebuilt_metrics():
    return (
        _metric("ktw_export_prebuilt_decisions_total", 'outcome="hit"'),
        _metric("ktw_export_prebuilt_pages_total", 'page="first"'),
        _metric("ktw_export_prebuilt_pages_total", 'page="cursor"'),
    )


def test_prebuilt_walk_serves_the_same_pages_without_reading_the_selection(
    repository, tmp_path,
):
    _collect(repository, tmp_path)
    baseline = _walk(_app(repository, tmp_path, enabled=False))
    assert baseline[0].headers["x-export-prebuilt"] == "-"
    report = _build(repository, tmp_path)
    before = _prebuilt_metrics()
    assert report["outcome"] == "built" and report["pages"] == 5 and report["items"] == 24
    client = _app(repository, tmp_path)
    statements = []

    def capture(_c, _cursor, sql, *_rest):
        statements.append(sql)

    event.listen(repository.engine, "before_cursor_execute", capture)
    try:
        first = client.get(URL, params={"limit": LIMIT}, headers=HEADERS)
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture)
    assert first.status_code == 200, first.text
    assert first.headers["x-export-prebuilt"] == "hit"
    assert first.headers["x-export-status-proof"] == "prebuilt"
    # 첫 페이지는 입력 proof(inventory·catalog·주소·epoch)만 보고 승인 선택은 다시 읽지 않는다.
    assert not [sql for sql in statements if any(table in sql for table in SELECTION_TABLES)]
    pages = [first, *_walk(client)[1:]]
    assert _items(pages) == _items(baseline)
    assert [page.headers["etag"] for page in pages] == [
        page.headers["etag"] for page in baseline
    ]
    assert {page.json()["meta"]["source_status"] for page in pages} == {"ok"}
    assert sum(int(page.headers["x-export-prebuilt-pages"]) for page in pages) == 5
    # 같은 내용이면 304도 같다(내용 ETag).
    conditional = client.get(URL, params={"limit": LIMIT},
                             headers={**HEADERS, "if-none-match": baseline[0].headers["etag"]})
    assert conditional.status_code == 304
    # 적중률은 header 밖(지표)에서도 본다. 완료 페이지 재사용(판정 없음)은 판정으로 세지 않는다.
    after = _prebuilt_metrics()
    assert [b - a for a, b in zip(before, after, strict=True)] == [1, 1, 4]


def test_builder_is_a_noop_until_an_input_changes(repository, tmp_path):
    # 승격은 더 새 발표 시각이어야 한다(같은 시각·다른 값은 CONFLICT).
    _collect(repository, tmp_path, hours=2)
    assert _build(repository, tmp_path)["outcome"] == "built"
    assert _build(repository, tmp_path)["outcome"] == "unchanged"
    assert len(_builds(repository)) == 1
    _collect(repository, tmp_path, hours=1, value="21.5")
    client = _app(repository, tmp_path)
    changed = client.get(URL, params={"limit": LIMIT}, headers=HEADERS)
    # 승격이 inventory를 바꿨다. 이전 build를 내지 않고 기존 freeze로 새 값을 낸다.
    assert changed.headers["x-export-prebuilt"] == "changed"
    walked = _items(_walk(client))
    assert {item["value"] for item in walked if item["metric_key"] == "T1H"} == {21.5}
    assert _build(repository, tmp_path)["outcome"] == "built"

    def first_outcome():
        # 새 앱(빈 완료 페이지 캐시)의 첫 페이지 판정.
        return _app(repository, tmp_path).get(
            URL, params={"limit": LIMIT}, headers=HEADERS,
        ).headers["x-export-prebuilt"]

    assert first_outcome() == "hit"
    # catalog 이름만 바뀌어도 manifest(위치 항목)가 달라진다.
    catalog = Base.metadata.tables["weather_locations"]
    with repository.engine.begin() as connection:
        connection.execute(update(catalog).where(catalog.c.location_id == "a").values(
            name="새 이름",
        ))
    assert first_outcome() == "changed"
    assert _build(repository, tmp_path)["outcome"] == "built"
    _collect(repository, tmp_path, hours=0, value="22.5")
    assert _build(repository, tmp_path)["outcome"] == "built"
    # profile마다 최근 build 둘만 남긴다(앞 build로 순회 중인 cursor용).
    assert len(_builds(repository)) == export_prebuilt.KEEP_BUILDS


def test_old_builder_status_is_recomputed_and_a_new_failure_falls_back(
    repository, tmp_path,
):
    builds, _ = export_prebuilt_tables(Base.metadata)
    _collect(repository, tmp_path)
    assert _build(repository, tmp_path)["outcome"] == "built"
    with repository.engine.begin() as connection:
        connection.execute(update(builds).values(
            status_checked_at=datetime.now(UTC) - timedelta(minutes=2),
        ))
    client = _app(repository, tmp_path)
    first = client.get(URL, params={"limit": LIMIT}, headers=HEADERS)
    # builder 판정은 60초 승인 수명을 넘었다. 본문은 쓰되 상태는 새로 판정한다.
    assert first.headers["x-export-prebuilt"] == "hit"
    assert first.headers["x-export-status-proof"] == "computed"
    assert first.json()["meta"]["source_status"] == "ok"
    with pytest.raises(KmaAllGridsFailedError):
        _collect(repository, tmp_path, failed=True)
    fresh = _app(repository, tmp_path)
    failed = fresh.get(URL, params={"limit": LIMIT}, headers=HEADERS)
    # 실패가 승인 선택을 미확인으로 바꿨다. 사전 구성 manifest(확인됨)를 버리고 freeze한다.
    assert failed.headers["x-export-prebuilt"] == "selection"
    assert failed.json()["meta"]["source_status"] == "stale"
    # 실패는 proof(inventory·catalog·epoch)를 바꾸지 않는다. builder는 요청과 같은 승인 선택
    # 비교로 다시 만든다(다음 승격까지 사전 구성이 쓸모없어지지 않게).
    rebuilt = _build(repository, tmp_path)
    assert rebuilt["outcome"] == "built", rebuilt
    with repository.engine.begin() as connection:
        # 새 판정을 강제해 manifest와 지금 승인 선택(미확인)이 같은지를 요청이 다시 보게 한다.
        connection.execute(update(builds).where(
            builds.c.build_id == rebuilt["build_id"],
        ).values(status_checked_at=datetime.now(UTC) - timedelta(seconds=90)))
    again = _app(repository, tmp_path).get(URL, params={"limit": LIMIT}, headers=HEADERS)
    assert again.headers["x-export-prebuilt"] == "hit"
    assert again.json()["meta"]["source_status"] == "stale"
    assert _build(repository, tmp_path)["outcome"] == "unchanged"


def test_expired_handle_is_not_extended_and_a_new_handle_is_issued(repository, tmp_path):
    snapshots, _ = export_snapshot_tables(Base.metadata)
    _collect(repository, tmp_path)
    report = _build(repository, tmp_path)
    expired_at = datetime.now(UTC) - timedelta(minutes=1)
    with repository.engine.begin() as connection:
        connection.execute(update(snapshots).where(
            snapshots.c.snapshot_id == report["snapshot_id"],
        ).values(expires_at=expired_at))
    client = _app(repository, tmp_path)
    first = client.get(URL, params={"limit": LIMIT}, headers=HEADERS)
    assert first.headers["x-export-prebuilt"] == "hit"
    issued = first.json()["meta"]["snapshot_id"]
    assert issued != report["snapshot_id"]
    with repository.engine.connect() as connection:
        old = connection.scalar(select(snapshots.c.expires_at).where(
            snapshots.c.snapshot_id == report["snapshot_id"],
        ))
        new = connection.scalar(select(snapshots.c.expires_at).where(
            snapshots.c.snapshot_id == issued,
        ))
    assert old == expired_at  # 만료 handle은 되살리지 않는다.
    assert new - datetime.now(UTC) >= SNAPSHOT_TTL - timedelta(seconds=5)
    # 새 handle도 같은 manifest라 cursor 페이지는 계속 사전 구성 본문을 쓴다.
    pages = _walk(client)
    assert {page.json()["meta"]["snapshot_id"] for page in pages} == {issued}
    # 첫 페이지는 방금 만든 완료 페이지 재사용(x-export-read=reused)이라 판정이 없다.
    assert pages[0].headers["x-export-read"] == "reused"
    assert first.headers["x-export-prebuilt-pages"] == "1"
    assert sum(int(page.headers["x-export-prebuilt-pages"]) for page in pages[1:]) == 4


def _snapshot_count(repository):
    snapshots, refs = export_snapshot_tables(Base.metadata)
    with repository.engine.connect() as connection:
        return (connection.scalar(select(func.count()).select_from(snapshots)),
                connection.scalar(select(func.count()).select_from(refs)))


def test_a_handle_issued_for_an_expired_build_handle_is_reused(repository, tmp_path):
    """build handle이 만료된 뒤 첫 요청만 새 handle을 발급하고 다음 첫 페이지들은 그것을 쓴다."""
    snapshots, _ = export_snapshot_tables(Base.metadata)
    _collect(repository, tmp_path)
    report = _build(repository, tmp_path)
    with repository.engine.begin() as connection:
        connection.execute(update(snapshots).where(
            snapshots.c.snapshot_id == report["snapshot_id"],
        ).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    before = _snapshot_count(repository)
    issued = []
    for _ in range(3):
        # 새 앱마다(빈 완료 페이지 캐시) 첫 페이지 판정을 다시 한다.
        first = _app(repository, tmp_path).get(URL, params={"limit": LIMIT}, headers=HEADERS)
        assert first.headers["x-export-prebuilt"] == "hit"
        issued.append(first.json()["meta"]["snapshot_id"])
    assert len(set(issued)) == 1 and issued[0] != report["snapshot_id"]
    after = _snapshot_count(repository)
    _, refs = export_snapshot_tables(Base.metadata)
    with repository.engine.connect() as connection:
        per_handle = connection.scalar(select(func.count()).select_from(refs).where(
            refs.c.snapshot_id == issued[0],
        ))
    assert per_handle > 0
    # 세 요청을 합쳐 handle 하나와 그 scope ref만 더 생겼다.
    assert after == (before[0] + 1, before[1] + per_handle)
    # builder는 handle 수명 때문에 다시 만들지 않는다(요청이 이어 쓰는 handle이 있다).
    assert _build(repository, tmp_path)["outcome"] == "unchanged"


def test_a_passed_time_boundary_recomputes_the_status_and_keeps_the_build(repository, tmp_path):
    """운영(2026-11-25 odroid): 3천여 행의 가장 이른 health 경계가 판정 5초 뒤라 ``valid_until``이
    ``built_at``보다 앞섰고, 요청은 모든 build를 ``expired``로 버렸다. 경계가 바꾸는 것은 만료
    위치·미확인 여부뿐이라, 경계 뒤에는 builder 판정만 잇지 않고 새 판정으로 manifest를 검산한다.
    """
    builds, _ = export_prebuilt_tables(Base.metadata)
    _collect(repository, tmp_path)
    baseline = _walk(_app(repository, tmp_path, enabled=False))
    report = _build(repository, tmp_path)
    assert report["outcome"] == "built"
    with repository.engine.begin() as connection:
        # builder 판정은 방금(60초 안) 했지만 시간 경계는 build 저장 전에 지났다.
        connection.execute(update(builds).values(
            valid_until=datetime.now(UTC) - timedelta(seconds=1),
        ))
    pages = _walk(_app(repository, tmp_path))
    assert pages[0].headers["x-export-prebuilt"] == "hit"
    # 경계 뒤 builder 판정은 잇지 않는다(시각만으로 달라질 수 있다).
    assert pages[0].headers["x-export-status-proof"] == "computed"
    assert _items(pages) == _items(baseline)
    assert [page.headers["etag"] for page in pages] == [page.headers["etag"] for page in baseline]
    assert sum(int(page.headers["x-export-prebuilt-pages"]) for page in pages) == report["pages"]
    # 승인 선택이 그대로면 시간 경계만으로 다시 만들지 않는다.
    assert _build(repository, tmp_path)["outcome"] == "unchanged"


def test_a_time_boundary_that_changes_the_selection_falls_back(
    repository, tmp_path, monkeypatch,
):
    """경계가 실제로 승인 선택을 바꾸면(신선도 상한을 넘겨 미확인) build를 버리고 freeze한다."""
    from kortravelweather import export_inventory_selection

    builds, _ = export_prebuilt_tables(Base.metadata)
    _collect(repository, tmp_path)
    assert _build(repository, tmp_path)["outcome"] == "built"
    with repository.engine.begin() as connection:
        # Map은 builder 2분 뒤에 읽는다. builder 판정(60초)은 잇지 않는다.
        connection.execute(update(builds).values(
            status_checked_at=datetime.now(UTC) - timedelta(minutes=2),
        ))

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            # 관측(초단기실황) 신선도 상한 3시간을 넘긴 시각. proof(행 내용)는 그대로다.
            return datetime.now(tz) + timedelta(hours=3)

    monkeypatch.setattr(export_inventory_selection, "datetime", Later)
    pages = _walk(_app(repository, tmp_path))
    assert pages[0].headers["x-export-prebuilt"] == "selection"
    assert {page.json()["meta"]["source_status"] for page in pages} == {"stale"}
    # builder도 같은 승인 선택 비교로 다시 만든다.
    assert _build(repository, tmp_path)["outcome"] == "built"


def test_a_delete_epoch_change_falls_back(repository, tmp_path):
    _collect(repository, tmp_path)
    baseline = _items(_walk(_app(repository, tmp_path, enabled=False)))
    _build(repository, tmp_path)
    with repository.engine.begin() as connection:
        # head 삭제가 올리는 번호다. 삭제 자체 없이 번호만 올려도 proof가 달라야 한다.
        connection.execute(text(
            "UPDATE weather_page_liveness_epochs SET epoch = epoch + 1 "
            "WHERE target_name = 'weather_export_heads'"
        ))
    pages = _walk(_app(repository, tmp_path))
    assert pages[0].headers["x-export-prebuilt"] == "changed"
    assert _items(pages) == baseline


def test_missing_artifacts_fall_back_to_the_regular_path(
    repository, tmp_path, monkeypatch,
):
    snapshots, _ = export_snapshot_tables(Base.metadata)
    _collect(repository, tmp_path)
    baseline = _items(_walk(_app(repository, tmp_path, enabled=False)))
    report = _build(repository, tmp_path)
    with repository.engine.begin() as connection:
        connection.execute(update(snapshots).where(
            snapshots.c.snapshot_id == report["snapshot_id"],
        ).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    original = DatabaseExportArtifactStore.all_present
    calls = []

    def first_missing(self, refs):
        # 사전 구성 검산(첫 호출)만 파일이 없다고 본다. 기존 경로는 실제 검산을 한다.
        calls.append(1)
        return False if len(calls) == 1 else original(self, refs)

    monkeypatch.setattr(DatabaseExportArtifactStore, "all_present", first_missing)
    before = _snapshot_count(repository)[0]
    pages = _walk(_app(repository, tmp_path))
    assert pages[0].headers["x-export-prebuilt"] == "artifacts"
    assert _items(pages) == baseline
    # 새 handle은 기존 freeze가 발급한 하나뿐이다.
    assert _snapshot_count(repository)[0] == before + 1


def test_a_failed_handle_copy_falls_back_instead_of_503(repository, tmp_path, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from kortravelweather.export_snapshots import FrozenCurrentExports

    snapshots, _ = export_snapshot_tables(Base.metadata)
    _collect(repository, tmp_path)
    baseline = _items(_walk(_app(repository, tmp_path, enabled=False)))
    report = _build(repository, tmp_path)
    with repository.engine.begin() as connection:
        connection.execute(update(snapshots).where(
            snapshots.c.snapshot_id == report["snapshot_id"],
        ).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))

    def locked(self, session, frozen):
        raise OperationalError("INSERT", {}, Exception("lock timeout"))

    monkeypatch.setattr(FrozenCurrentExports, "_copy_handle", locked)
    pages = _walk(_app(repository, tmp_path))
    # 발급 실패는 사전 구성만 버린다. 기존 freeze가 새 handle로 같은 항목을 낸다.
    assert pages[0].headers["x-export-prebuilt"] == "error"
    assert _items(pages) == baseline


def test_a_failed_proof_read_falls_back_instead_of_failing(repository, tmp_path, monkeypatch):
    from kortravelweather import export_page_liveness

    _collect(repository, tmp_path)
    _build(repository, tmp_path)
    original = export_page_liveness.capture_liveness
    calls = []

    def first_fails(connection, metadata):
        calls.append(1)
        if len(calls) == 1:
            raise ExportArtifactError("완료 페이지 생존 검산을 확인할 수 없습니다.")
        return original(connection, metadata)

    monkeypatch.setattr(export_page_liveness, "capture_liveness", first_fails)
    first = _app(repository, tmp_path).get(URL, params={"limit": LIMIT}, headers=HEADERS)
    assert first.status_code == 200, first.text
    assert first.headers["x-export-prebuilt"] == "error"


def test_cursor_pages_after_the_build_is_removed_use_the_regular_assembly(
    repository, tmp_path,
):
    builds, _ = export_prebuilt_tables(Base.metadata)
    _collect(repository, tmp_path)
    baseline = _items(_walk(_app(repository, tmp_path, enabled=False)))
    _build(repository, tmp_path)
    client = _app(repository, tmp_path)
    first = client.get(URL, params={"limit": LIMIT}, headers=HEADERS)
    assert first.headers["x-export-prebuilt"] == "hit"
    assert first.headers["x-export-prebuilt-pages"] == "1"
    with repository.engine.begin() as connection:
        connection.execute(delete(builds))
    pages = [first]
    while pages[-1].json()["meta"]["next_cursor"]:
        page = client.get(URL, params={
            "limit": LIMIT, "cursor": pages[-1].json()["meta"]["next_cursor"],
        }, headers=HEADERS)
        assert page.status_code == 200, page.text
        pages.append(page)
    assert _items(pages) == baseline
    assert {page.headers["x-export-prebuilt-pages"] for page in pages[1:]} == {"0"}


def test_handle_at_its_total_lifetime_ceiling_gets_a_new_handle(repository, tmp_path):
    snapshots, _ = export_snapshot_tables(Base.metadata)
    _collect(repository, tmp_path)
    report = _build(repository, tmp_path)
    now = datetime.now(UTC)
    with repository.engine.begin() as connection:
        # 총 수명 6시간 상한이 2분 남았다. 갱신해도 15분을 줄 수 없다.
        connection.execute(update(snapshots).where(
            snapshots.c.snapshot_id == report["snapshot_id"],
        ).values(created_at=now - timedelta(hours=6) + timedelta(minutes=2),
                 expires_at=now + timedelta(minutes=1)))
    first = _app(repository, tmp_path).get(URL, params={"limit": LIMIT}, headers=HEADERS)
    assert first.headers["x-export-prebuilt"] == "hit"
    assert first.json()["meta"]["snapshot_id"] != report["snapshot_id"]


def test_damaged_page_body_is_rebuilt_from_artifacts(repository, tmp_path):
    _, pages_table = export_prebuilt_tables(Base.metadata)
    _collect(repository, tmp_path)
    baseline = _items(_walk(_app(repository, tmp_path, enabled=False)))
    _build(repository, tmp_path)
    first_page = (pages_table.c.start_g == 0) & (pages_table.c.start_p == 0)
    with repository.engine.begin() as connection:
        # 같은 시험 DB를 앞선 시험이 migration으로 만들었으면 불변 trigger(BEFORE UPDATE)와
        # byte·digest 제약이 있다. 행을 지우고 다시 넣되 byte·digest는 맞추고 항목 수만 어긋나게
        # 해 두 schema(create_all·migration) 모두에서 읽기 검산의 shape 단계가 잡게 한다.
        rows = [dict(row) for row in connection.execute(
            select(pages_table).where(first_page)
        ).mappings()]
        assert rows and all(row["item_count"] > 0 for row in rows)
        connection.execute(delete(pages_table).where(first_page))
        connection.execute(insert(pages_table), [
            {**row, "payload": b"[]", "byte_count": 2,
             "payload_digest": hashlib.sha256(b"[]").hexdigest()}
            for row in rows
        ])
    pages = _walk(_app(repository, tmp_path))
    assert _items(pages) == baseline
    assert pages[0].headers["x-export-prebuilt"] == "hit"
    assert pages[0].headers["x-export-prebuilt-pages"] == "0"


def test_switch_off_keeps_the_regular_path_and_the_builder_idle(repository, tmp_path):
    _collect(repository, tmp_path)
    assert _build(repository, tmp_path, enabled=False)["outcome"] == "disabled"
    assert _builds(repository) == []
    _build(repository, tmp_path)
    pages = _walk(_app(repository, tmp_path, enabled=False))
    assert pages[0].headers["x-export-prebuilt"] == "-"
    assert {page.headers["x-export-prebuilt-pages"] for page in pages} == {"0"}


def test_builder_does_not_prebuild_a_previous_success_fallback(repository, tmp_path):
    _collect(repository, tmp_path)
    with repository.engine.begin() as connection:
        # 승인 inventory가 없으면 freeze는 이전 승인 fallback(stale)을 쓴다. 그것은 만들지 않는다.
        connection.execute(text("DELETE FROM weather_export_approved_locations"))
    assert _build(repository, tmp_path)["outcome"] == "not_ready"
    assert _builds(repository) == []
