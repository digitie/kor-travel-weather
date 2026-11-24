"""Map 표준 프로필(current) 사전 구성 페이지(호출 검토 A3)의 읽기 경로·handle 수명·fallback."""

from datetime import UTC, datetime, timedelta

import pytest
from kortravelweather_dagster.kma_weather import KmaAllGridsFailedError
from sqlalchemy import event, select, text, update
from test_frozen_service_exports import HEADERS, TOKEN, _client, _collect
from test_frozen_service_exports import repository as repository

from kortravelweather import export_prebuilt
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


def test_prebuilt_walk_serves_the_same_pages_without_reading_the_selection(
    repository, tmp_path,
):
    _collect(repository, tmp_path)
    baseline = _walk(_app(repository, tmp_path, enabled=False))
    assert baseline[0].headers["x-export-prebuilt"] == "-"
    report = _build(repository, tmp_path)
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
    with repository.engine.begin() as connection:
        # 시험 schema(create_all)에는 migration의 불변 trigger·digest 제약이 없다.
        connection.execute(update(pages_table).where(
            pages_table.c.start_g == 0, pages_table.c.start_p == 0,
        ).values(payload=b"[]"))
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
