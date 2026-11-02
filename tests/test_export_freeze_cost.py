"""export snapshot freeze의 반복 비용(2026-11-02 odroid 실측) 회귀.

- descriptor namespace(정규 JSON sha256)를 같은 descriptor마다 한 번만 계산한다. 외부 예보 첫 페이지
  하나가 descriptor 약 780개에 namespace를 6,081번(3.5초) 계산했다.
- catalog anchor 투영이 행마다 metadata(json)를 한 번만 해석한다. json 경로 연산자는 매번 원문을
다시
  해석해 1,451곳 조회가 0.7~1.1초였다.
"""

import os
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from test_export_external import external_repository as external_repository

from kortravelweather import export_heads
from kortravelweather.export_external import RetrievalScopeDescriptor
from kortravelweather.export_inventory_selection import (
    catalog_source_columns,
    catalog_source_from,
)
from kortravelweather.export_mid_anchor import mid_region_expression
from kortravelweather.models import WeatherLocation


def _descriptor(location_id="loc-1", **overrides):
    values = dict(
        provider="open_meteo", dataset_key="open_meteo_forecast", entity_id=location_id,
        forecast_style="short", location_ids=(location_id,), latitude=36.56, longitude=128.73,
        normalizer_revision="open_meteo-v1",
    )
    values.update(overrides)
    return RetrievalScopeDescriptor(**values)


def test_namespace_is_digested_once_per_equal_descriptor(monkeypatch):
    calls = []
    original = export_heads._digest

    def counting(value):
        calls.append(1)
        return original(value)

    export_heads._cached_namespace.cache_clear()
    monkeypatch.setattr(export_heads, "_digest", counting)
    first = _descriptor()
    expected = original(first.model_dump(mode="json"))
    assert [first.namespace for _ in range(5)] == [expected] * 5
    # 필드가 같은 새 descriptor(다른 객체)도 다시 계산하지 않는다.
    assert _descriptor().namespace == expected
    assert len(calls) == 1


def test_namespace_follows_field_changes_including_model_copy():
    export_heads._cached_namespace.cache_clear()
    base = _descriptor()
    copied = base.model_copy(update={"location_ids": ("loc-1", "loc-2")})
    assert base.namespace != copied.namespace
    assert copied.namespace == export_heads._digest(copied.model_dump(mode="json"))
    assert _descriptor(latitude=36.57).namespace != base.namespace
    # 기억한 값은 모델 자체를 바꾸지 않는다(동등성·직렬화 그대로).
    assert base == _descriptor() and base.model_dump() == _descriptor().model_dump()


@pytest.fixture
def repository():
    from kortravelweather.repository import WeatherRepository

    repo = WeatherRepository(os.environ.get(
        "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
        "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
    ))
    repo.create_schema()
    yield repo
    repo.engine.dispose()


def test_catalog_projection_casts_metadata_once_per_row(repository):
    from kortravelweather.export_heads import ExportHeads

    catalog = ExportHeads(repository).metadata.tables["weather_locations"]
    sql = str(select(catalog.c.location_id, *catalog_source_columns(catalog)).select_from(
        catalog_source_from(catalog),
    ).compile(dialect=postgresql.dialect()))
    assert sql.count("CAST(weather_locations.metadata AS JSONB)") == 1
    assert sql.count("JOIN LATERAL") == 1
    # 투영 열이 json 원문 열을 직접 경로 연산하지 않는다(열마다 다시 해석하던 원인).
    assert "weather_locations.metadata ->" not in sql


def test_catalog_projection_values_match_the_json_path_projection(repository):
    from kortravelweather.export_heads import ExportHeads

    prefix = "proj-" + uuid.uuid4().hex[:10]
    metadatas = {
        "full": {"measurement_point": {
            "provider": "python-airkorea-api", "station_id": "823", "station_name": "명륜동",
            "sido_name": "경북", "network": "도시대기",
        }, "mid_land_region_code": "11H10000", "mid_temperature_region_code": "11H10501"},
        "fallback": {"mid_region_code": "11B00000"},
        "partial": {"measurement_point": {"provider": "python-krex-api", "station_id": 64}},
        "long": {"measurement_point": {"station_name": "가" * 400, "provider": "p" * 300}},
        "empty": {},
    }
    for key, metadata in metadatas.items():
        repository.create_location(WeatherLocation(
            location_id=f"{prefix}-{key}", name=key, latitude=36.5, longitude=128.7,
            metadata=metadata,
        ))
    catalog = ExportHeads(repository).metadata.tables["weather_locations"]
    point = catalog.c.metadata["measurement_point"]
    reference = [
        *[func.left(mid_region_expression(catalog, part), 33).label(f"mid_{part}_region")
          for part in ("land", "temperature")],
        *[func.left(point[key].as_string(), maximum + 1).label("point_" + key)
          for key, maximum in [("provider", 120), ("station_id", 120), ("station_name", 200),
                               ("sido_name", 32), ("network", 120)]],
    ]
    where = catalog.c.location_id.like(prefix + "-%")
    with repository.engine.connect() as connection:
        new = {row["location_id"]: dict(row) for row in connection.execute(
            select(catalog.c.location_id, *catalog_source_columns(catalog))
            .select_from(catalog_source_from(catalog)).where(where)
        ).mappings()}
        old = {row["location_id"]: dict(row) for row in connection.execute(
            select(catalog.c.location_id, *reference).where(where)
        ).mappings()}
    assert len(new) == len(metadatas)
    assert new == old
    assert new[f"{prefix}-fallback"]["mid_land_region"] == "11B00000"
    assert len(new[f"{prefix}-long"]["point_station_name"]) == 201


def test_lateral_join_keeps_catalog_rows_without_metadata_keys(repository):
    # LATERAL은 행마다 정확히 한 행을 낸다 — ON true join이 catalog 행을 늘리거나 줄이지 않는다.
    from kortravelweather.export_heads import ExportHeads

    catalog = ExportHeads(repository).metadata.tables["weather_locations"]
    with repository.engine.connect() as connection:
        plain = connection.scalar(select(func.count()).select_from(catalog))
        joined = connection.scalar(select(func.count()).select_from(catalog_source_from(catalog)))
    assert joined == plain


def _selection_scans(repo, run):
    """run 동안 snapshot 표에 selection json 조건을 건 SQL 문장들."""
    from sqlalchemy import event

    seen = []

    def capture(_conn, _cursor, statement, parameters, _context, _many):
        if "weather_export_snapshots" in statement and "selection" in repr(parameters):
            seen.append(statement)

    event.listen(repo.engine, "before_cursor_execute", capture)
    try:
        result = run()
    finally:
        event.remove(repo.engine, "before_cursor_execute", capture)
    return result, seen


def test_first_page_without_a_matching_digest_does_not_scan_manifest_selection(
    external_repository, tmp_path,
):
    # 예전 재사용 조회는 digest가 없어도 selection json 조건으로 snapshot 표를 훑었다(행마다 큰
    # manifest 해석, 2026-11-02 odroid 65행 약 3.7초 → 5초 상한 503). digest가 없으면 훑지 않는다.
    from test_export_mid_reader_postgres import _collect, _reader

    repo = external_repository
    collector, _ = _collect(repo, tmp_path)
    handle = _reader(repo, collector)
    (first, _), scans = _selection_scans(repo, lambda: handle.page(limit=1, cursor=None))
    assert first["meta"]["snapshot_id"]
    assert scans == []
    # 같은 digest가 있으면 그보다 새 행만 selection으로 확인하고 재사용한다.
    (again, _), scans = _selection_scans(repo, lambda: handle.page(limit=1, cursor=None))
    assert again["meta"]["snapshot_id"] == first["meta"]["snapshot_id"]
    assert len(scans) == 1 and "issue_order >" in scans[0]


def test_a_newer_handle_of_the_same_selection_still_blocks_reuse(external_repository, tmp_path):
    # 재사용 조건은 그대로다: 같은 digest라도 그 selection의 가장 최근 발급이 아니면 새로 발급한다.
    from test_export_mid_reader_postgres import _collect, _reader

    repo = external_repository
    collector, _ = _collect(repo, tmp_path)
    handle = _reader(repo, collector)
    first, _ = handle.page(limit=1, cursor=None)
    snapshots = handle.snapshots
    with repo.engine.begin() as connection:
        row = dict(connection.execute(select(snapshots).where(
            snapshots.c.snapshot_id == first["meta"]["snapshot_id"],
        )).mappings().one())
        row.pop("issue_order")
        newer_id = str(uuid.uuid4())
        row.update(snapshot_id=newer_id, manifest_digest="0" * 64)
        connection.execute(snapshots.insert().values(**row))
    third, _ = handle.page(limit=1, cursor=None)
    assert third["meta"]["snapshot_id"] not in {first["meta"]["snapshot_id"], newer_id}
