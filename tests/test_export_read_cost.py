"""큰 JSON 조회/직렬화 비용을 줄여도 frozen 출력과 cursor 의미를 유지한다."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

# catalog와 snapshot을 매 시험 정리하는 실제 PostgreSQL fixture다.
from test_frozen_service_exports import repository as repository

from kortravelweather.export_artifacts import ExportArtifactStore
from kortravelweather.export_forecasts import FrozenForecastExports
from kortravelweather.export_locations import FrozenLocationExports
from kortravelweather.models import WeatherLocation


def test_reference_lookup_uses_small_projection_and_latest_issued_handle(repository, tmp_path):
    repository.create_location(WeatherLocation(
        location_id="reference", name="참조", latitude=37.5, longitude=127,
    ))
    reader = FrozenLocationExports(repository, ExportArtifactStore(tmp_path), signing_key="fixture")
    first = reader._freeze_locations()
    now = datetime.now(UTC)
    # 다른 종류와 더 최근에 발급된 같은 종류를 섞는다. 만료 여부로 latest를 바꾸지 않는다.
    rows = []
    for kind in ("locations", "forecasts", None):
        row = dict(first)
        row.update(snapshot_id=str(uuid.uuid4()), manifest={
            "selection": {"kind": kind}, "locations": [], "padding": "가" * 10000,
        }, expires_at=now - timedelta(seconds=1))
        rows.append(row)
    with repository.engine.begin() as connection:
        connection.execute(reader.snapshots.insert(), rows)
    statements = []

    def capture(_c, _cursor, sql, _params, _context, _many):
        statements.append(sql)

    event.listen(repository.engine, "before_cursor_execute", capture)
    try:
        with repository.engine.connect() as connection:
            connection.exec_driver_sql("SET LOCAL plan_cache_mode=force_generic_plan")
            latest = reader._previous(connection)
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture)
    assert latest["snapshot_id"] == rows[0]["snapshot_id"]
    assert latest["selection_kind"] == "locations"
    query = next(s for s in statements if "SELECT weather_export_snapshots" in s)
    assert "WHERE weather_export_snapshots.selection_kind =" in query
    assert " -> " not in query and " ->> " not in query
    with repository.engine.connect() as connection:
        kinds = dict(connection.execute(select(
            reader.snapshots.c.snapshot_id, reader.snapshots.c.selection_kind,
        )).all())
    assert [kinds[row["snapshot_id"]] for row in rows] == ["locations", "forecasts", None]


def test_concurrent_reference_issuance_preserves_each_frozen_cursor(repository, tmp_path):
    for key in ("a", "b", "c"):
        repository.create_location(WeatherLocation(
            location_id=key, name=key, latitude=37.5, longitude=127,
        ))

    def page(cursor=None):
        reader = FrozenLocationExports(
            repository, ExportArtifactStore(tmp_path), signing_key="fixture",
        )
        return reader.page("locations", limit=1, cursor=cursor)

    with ThreadPoolExecutor(max_workers=2) as pool:
        pairs = list(pool.map(lambda _: page(), range(2)))
    for first, tag in pairs:
        assert first["items"][0]["location_id"] == "a" and tag.startswith('W/"')
        following, _ = page(first["meta"]["next_cursor"])
        assert following["items"][0]["location_id"] == "b"
        assert following["meta"]["snapshot_id"] == first["meta"]["snapshot_id"]
    # 동시 신규 handle 발급은 허용하며 이후에는 실제 가장 최근 발급만 재사용한다.
    reader = FrozenLocationExports(repository, ExportArtifactStore(tmp_path), signing_key="fixture")
    with repository.engine.connect() as connection:
        latest = reader._previous(connection)
    again, _ = page()
    assert again["meta"]["snapshot_id"] == latest["snapshot_id"]


def test_missing_catalog_reissues_expired_reference_without_changing_saved_manifest(
    repository, tmp_path,
):
    repository.create_location(WeatherLocation(
        location_id="a", name="남아야 하는 위치", latitude=37.5, longitude=127,
    ))
    reader = FrozenLocationExports(repository, ExportArtifactStore(tmp_path), signing_key="fixture")
    first, _ = reader.page("locations", limit=1, cursor=None)
    with repository.engine.begin() as connection:
        previous = reader._previous(connection)
        connection.execute(reader.snapshots.update().values(
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        ))
        connection.execute(reader.heads.metadata.tables["weather_locations"].delete())
    fallback, _ = reader.page("locations", limit=1, cursor=None)
    assert fallback["items"] == first["items"]
    assert fallback["meta"]["source_status"] == "stale"
    assert fallback["meta"]["snapshot_id"] != first["meta"]["snapshot_id"]
    with repository.engine.connect() as connection:
        latest = reader._previous(connection)
    assert latest["manifest"] == previous["manifest"]
    assert latest["manifest_digest"] == previous["manifest_digest"]
    assert latest["selection_kind"] == "locations"


def test_forecast_single_pass_keeps_sorted_sparse_metrics_and_last_duplicate():
    now = datetime(2026, 11, 3, tzinfo=UTC)
    past, t1, t2 = [(now + timedelta(hours=h)).isoformat() for h in (-1, 1, 2)]
    points = [
        {"metric_key": metric, "target_at": at, "known_at": now.isoformat(),
         "value_number": number, "value_text": text, "provider": "python-kma-api",
         "forecast_style": "short", "location_id": "a"}
        for metric, at, number, text in (
            ("B", t2, "2", None), ("A", t1, None, "흐림"), ("B", t2, "3", None),
            ("A", past, None, "맑음"), ("C", t1, None, None),
        )
    ]
    reader = object.__new__(FrozenForecastExports)
    reader.style, reader.since = "short", None
    reader.artifacts = SimpleNamespace(read=lambda *args, **kwargs: iter(points))
    manifest = {"scopes": [{"published_at": now.isoformat(), "location_ids": ["a"],
                            "refs": [{"location_ids": ["a"], "artifact_digest": "d",
                                      "point_count": len(points), "byte_count": 1}]}]}
    state = {"g": 0, "p": 0}
    items, more = reader._page_items(manifest, state, 1, now)
    assert not more and state == {"g": 1, "p": 0}
    assert items[0]["target_at"] == [t1, t2]
    assert list(items[0]["metrics"]) == ["A", "B", "C"]
    assert items[0]["metrics"] == {"A": ["흐림", None], "B": [None, 3.0], "C": [None, None]}


@pytest.mark.parametrize("conditional", [False, True])
@pytest.mark.parametrize("value", ["영하", None, -0.0, 1e-7, 1e20])
def test_render_preserves_payload_headers_and_skips_serialization_for_304(
    monkeypatch, conditional, value,
):
    import gzip

    from kortravelweather_api.routers.service_exports import (
        ExportCurrentValue,
        ExportPage,
        _render_page,
    )

    page = {"items": [{
        "location_id": "위치", "provider": "python-kma-api", "metric_key": "TEMP",
        "value": value, "unit": "°C", "target_at": "2026-11-03T00:00:00+00:00",
        "known_at": "2026-11-03T00:00:00+00:00", "forecast_style": "observed",
    }], "meta": {"generated_at": "2026-11-03T00:00:00+00:00",
                  "source_status": "stale", "source_collected_at": None, "item_count": 1}}
    model = ExportPage[ExportCurrentValue]
    expected = json.dumps(model.model_validate(page).model_dump(mode="json"),
                          ensure_ascii=False, separators=(",", ":")).encode()
    if conditional:
        def forbidden(*args, **kwargs):
            pytest.fail("304에서 사용하지 않을 전체 payload를 직렬화했습니다.")
        monkeypatch.setattr(model, "model_dump_json", forbidden)
    payload, headers = _render_page(lambda: (page, 'W/"same"'), model,
                                    '"same"' if conditional else "", "gzip", gzip_minimum=0)
    assert headers["x-source-status"] == "stale" and headers["etag"] == 'W/"same"'
    assert headers["vary"] == "Accept-Encoding"
    if conditional:
        assert payload is None and "content-encoding" not in headers
        # ETag가 같아도 잘못된 DTO를 정상 304로 숨기지 않는다.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _render_page(lambda: ({"bad": True}, 'W/"same"'), model,
                         'W/"same"', "", gzip_minimum=0)
    else:
        assert headers["content-encoding"] == "gzip"
        # 작은 실수의 지수 표기(1e-07/1e-7)만 달라질 수 있으며 JSON 값은 같다.
        assert json.loads(gzip.decompress(payload)) == json.loads(expected)
