"""KMA 단기/초단기는 catalog JSON 없이도 전체 승인/건강도와 frozen 계약을 유지한다."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from kma import KmaClient
from kma.time_utils import parse_kma_datetime
from kortravelweather_dagster.kma_weather import (
    KMA_SHORT_FORECAST,
    KMA_ULTRA_SHORT_FORECAST,
    WeatherTarget,
    run_weather_sync,
)
from sqlalchemy import event, select
from test_frozen_service_exports import HEADERS, _client
from test_frozen_service_exports import repository as repository

from kortravelweather import export_forecasts, export_inventory_selection, export_snapshots
from kortravelweather.export_collector import publish_pending_exports
from kortravelweather.export_forecasts import FrozenForecastExports
from kortravelweather.models import WeatherLocation


def collect_style(repo, root, style, *, count=3):
    def respond(request):
        q = request.url.params
        base = parse_kma_datetime(q["base_date"], q["base_time"])
        rows = []
        categories = ({"TMP": "18.4", "POP": "0", "PTY": "0", "REH": "50",
                       "SKY": "1", "VEC": "120", "WSD": "1", "PCP": "강수없음",
                       "SNO": "적설없음", "WAV": "0", "UUU": "0", "VVV": "0"}
                      if style == "short" else
                      {"T1H": "18.4", "RN1": "0", "REH": "50", "PTY": "0",
                       "SKY": "1", "VEC": "120", "WSD": "1", "UUU": "0", "VVV": "0"})
        for hour in range(1, 81 if style == "short" else 7):
            at = (base + timedelta(hours=hour)).replace(minute=0)
            for category, value in categories.items():
                rows.append(dict(
                    baseDate=q["base_date"], baseTime=q["base_time"],
                    nx=int(q["nx"]), ny=int(q["ny"]), category=category, fcstValue=value,
                    fcstDate=at.strftime("%Y%m%d"), fcstTime=at.strftime("%H%M"),
                ))
        return httpx.Response(200, json={"response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
            "body": {"pageNo": 1, "numOfRows": 1000, "totalCount": len(rows),
                     "items": {"item": rows}},
        }})

    client = KmaClient("local-fixture", session=httpx.AsyncClient(
        transport=httpx.MockTransport(respond)), retries=0)
    targets = [WeatherTarget(WeatherLocation(
        location_id=f"forecast-{i:04}", name=f"예보 위치{i}", latitude=37.5, longitude=127,
        nx=60 + i // 48, ny=127,
    )) for i in range(count)]
    result = run_weather_sync(
        repository=repo, client=client, targets=targets,
        base_datasets=frozenset({KMA_SHORT_FORECAST if style == "short"
                                 else KMA_ULTRA_SHORT_FORECAST}),
        export_artifact_root=root,
    )
    assert result["status"] == "success"
    for _ in range((count + 47) // 48 + 1):
        progress = publish_pending_exports(repo, root, limit=64, budget_seconds=60)
        if not progress.has_more:
            break
    return targets


def wall_clock(monkeypatch):
    wall = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return wall[0].astimezone(tz) if tz else wall[0].replace(tzinfo=None)

    for module in (export_forecasts, export_inventory_selection, export_snapshots):
        monkeypatch.setattr(module, "datetime", Clock)
    return wall


def stable_body(response):
    body = response.json()
    body["meta"].pop("generated_at")
    return body


@pytest.mark.parametrize("style", ["short", "ultra_short"])
def test_kma_first_conditional_and_health_skip_only_unused_catalog_json(
    repository, tmp_path, monkeypatch, style,
):
    collect_style(repository, tmp_path, style)
    wall_clock(monkeypatch)
    client = _client(repository, tmp_path)
    queries, approvals = [], []
    original = export_inventory_selection.approved_source_selection

    def approval(*args, **kwargs):
        approvals.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(export_inventory_selection, "approved_source_selection", approval)

    def observe(_c, _cur, sql, _params, _context, _many):
        if "FROM weather_locations" in sql and "weather_locations.enabled" in sql:
            queries.append(sql)

    event.listen(repository.engine, "before_cursor_execute", observe)
    try:
        path = f"/v1/service/exports/forecasts?style={style}&limit=1"
        first = client.get(path, headers=HEADERS)
        assert first.status_code == 200, first.text
        before = len(approvals)
        repeated = client.get(path, headers={**HEADERS, "if-none-match": first.headers["etag"]})
        assert repeated.status_code == 304
        assert repeated.headers["etag"] == first.headers["etag"]
        assert len(approvals) > before  # 첫 조건부도 fresh 승인을 실제로 다시 읽는다.
        assert repeated.headers["x-export-artifact-read-calls"] == "0"
        # cursor health도 동일한 catalog 투영을 사용한다.
        following = client.get("/v1/service/exports/forecasts", params={
            "style": style, "limit": 1, "cursor": first.json()["meta"]["next_cursor"],
        }, headers=HEADERS)
        assert following.status_code == 200
    finally:
        event.remove(repository.engine, "before_cursor_execute", observe)
    assert len(queries) >= 3
    assert not any("catalog_source_metadata" in sql or "weather_locations.metadata" in sql
                   for sql in queries)
    assert all("weather_locations.latitude" in sql and "weather_locations.nx" in sql
               for sql in queries)


@pytest.mark.parametrize("style", ["short", "ultra_short"])
def test_full_projection_reference_preserves_body_etag_cursor_and_fresh_approval(
    repository, tmp_path, monkeypatch, style,
):
    collect_style(repository, tmp_path, style)
    wall_clock(monkeypatch)
    old, new = _client(repository, tmp_path), _client(repository, tmp_path)
    path = f"/v1/service/exports/forecasts?style={style}&limit=1"
    full_projection = export_forecasts.catalog_source_projection

    def reference(client, url, **kwargs):
        with monkeypatch.context() as patch:
            patch.setattr(FrozenForecastExports, "_catalog_source",
                          lambda _self, table: full_projection(table))
            return client.get(url, **kwargs)

    baseline = reference(old, path, headers=HEADERS)
    actual = new.get(path, headers=HEADERS)
    assert baseline.status_code == actual.status_code == 200
    assert stable_body(actual) == stable_body(baseline)
    assert actual.headers["etag"] == baseline.headers["etag"]
    assert actual.json()["meta"]["has_more"]
    params = {"style": style, "limit": 1,
              "cursor": actual.json()["meta"]["next_cursor"]}
    a = reference(old, "/v1/service/exports/forecasts", params=params, headers=HEADERS)
    b = new.get("/v1/service/exports/forecasts", params=params, headers=HEADERS)
    assert a.status_code == b.status_code == 200 and stable_body(a) == stable_body(b)
    assert a.headers["etag"] == b.headers["etag"]
    headers = {**HEADERS, "if-none-match": actual.headers["etag"]}
    assert reference(old, path, headers=headers).status_code == 304
    assert new.get(path, headers=headers).status_code == 304


@pytest.mark.parametrize("mutation", ["coordinate", "grid", "disabled", "head", "artifact",
                                      "failed_run", "freshness"])
def test_projection_preserves_changed_anchor_deletion_and_stale_fallback(
    repository, tmp_path, monkeypatch, mutation,
):
    from kortravelweather.export_heads import ExportHeads

    collect_style(repository, tmp_path, "ultra_short")
    clock = wall_clock(monkeypatch)
    old, new = _client(repository, tmp_path), _client(repository, tmp_path)
    path = "/v1/service/exports/forecasts?style=ultra_short&limit=1"
    projection = export_forecasts.catalog_source_projection

    def reference(client, **kwargs):
        with monkeypatch.context() as patch:
            patch.setattr(FrozenForecastExports, "_catalog_source",
                          lambda _self, table: projection(table))
            return client.get(path, **kwargs)

    first = reference(old, headers=HEADERS)
    assert first.status_code == 200
    assert new.get(path, headers=HEADERS).headers["etag"] == first.headers["etag"]
    heads = ExportHeads(repository)
    catalog = heads.metadata.tables["weather_locations"]
    with repository.engine.begin() as c:
        if mutation in {"coordinate", "grid", "disabled"}:
            values = {"coordinate": {"latitude": 37.6}, "grid": {"nx": 70},
                      "disabled": {"enabled": False}}[mutation]
            c.execute(catalog.update().where(catalog.c.location_id == "forecast-0000")
                      .values(**values))
        elif mutation == "head":
            c.execute(heads.heads.delete())
        elif mutation == "artifact":
            c.execute(heads.metadata.tables["weather_export_artifacts"].delete())
        elif mutation == "failed_run":
            c.execute(heads.metadata.tables["weather_sync_runs"].update().values(status="failed"))
        else:
            clock[0] += timedelta(hours=7)
    headers = {**HEADERS, "if-none-match": first.headers["etag"]}
    a, b = reference(old, headers=headers), new.get(path, headers=headers)
    assert a.status_code == b.status_code
    assert a.headers.get("etag") == b.headers.get("etag")
    if b.status_code == 200:
        assert stable_body(a) == stable_body(b)
        assert b.json()["meta"]["source_status"] == "stale"
    else:
        assert b.status_code == 503 and a.json()["code"] == b.json()["code"]


@pytest.mark.parametrize("overrides", [
    {"style": "external", "providers": ["python-khoa-api"],
     "expected_sources": [("python-khoa-api", "khoa_beach_index")]},
    {"providers": ["python-kma-api", "python-khoa-api"]},
    {"expected_sources": [("python-kma-api", "kma_short_forecast"),
                          ("python-khoa-api", "khoa_beach_index")]},
    {"optional_sources": [("python-khoa-api", "khoa_beach_index")]},
    {"expected_sources": []},
])
def test_non_kma_or_incomplete_selection_keeps_metadata_projection(repository, overrides):
    from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore
    from kortravelweather.repository import Base

    kwargs = dict(style="short", providers=["python-kma-api"], source_inventory=True,
                  expected_sources=[("python-kma-api", "kma_short_forecast")])
    kwargs.update(overrides)
    reader = FrozenForecastExports(repository, DatabaseExportArtifactStore(repository),
                                   signing_key="projection-fixture", **kwargs)
    table, columns = reader._catalog_source(Base.metadata.tables["weather_locations"])
    sql = str(select(*columns).select_from(table))
    assert "catalog_source_metadata" in sql
    assert "point_station_id" in sql and "mid_land_region" in sql


def test_same_input_large_catalog_conditional_timing(repository, tmp_path, monkeypatch):
    import hashlib
    import json
    from statistics import median
    from time import perf_counter

    from kortravelweather.repository import Base

    collect_style(repository, tmp_path, "ultra_short", count=1452)
    catalog = Base.metadata.tables["weather_locations"]
    metadata = {
        "measurement_point": {
            "provider": "python-krforest-api", "station_id": "catalog-fixture",
            "station_name": "산악 관측소", "sido_name": "강원특별자치도", "network": "mountain",
        },
        "mid_land_region_code": "11D10000", "mid_temperature_region_code": "11D10401",
        "mid_region_code": "11D10000",
    }
    with repository.engine.begin() as c:
        c.execute(catalog.update().values(metadata=metadata))
    wall_clock(monkeypatch)
    old, new = _client(repository, tmp_path), _client(repository, tmp_path)
    path = "/v1/service/exports/forecasts?style=ultra_short&limit=200"
    projection = export_forecasts.catalog_source_projection

    def request(client, baseline, **kwargs):
        if not baseline:
            return client.get(path, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(FrozenForecastExports, "_catalog_source",
                          lambda _self, table: projection(table))
            return client.get(path, **kwargs)

    first = request(old, True, headers=HEADERS)
    actual = request(new, False, headers=HEADERS)
    assert first.status_code == actual.status_code == 200
    assert stable_body(first) == stable_body(actual)
    assert first.headers["etag"] == actual.headers["etag"]
    frozen = first.json()["meta"]["snapshot_id"]
    with repository.engine.connect() as c:
        assert len(c.execute(select(catalog.c.location_id)).all()) == 1452
        assert len(c.execute(select(Base.metadata.tables["weather_export_approved_locations"]
                                   .c.location_id)).all()) == 1452
    times, catalog_times = {"baseline": [], "candidate": []}, {"baseline": [], "candidate": []}
    active = [None]

    def before(_c, _cursor, statement, _params, ctx, _many):
        if "FROM weather_locations" in statement and "weather_locations.enabled" in statement:
            ctx._catalog_probe_started = perf_counter()

    def after(_c, _cursor, _statement, _params, ctx, _many):
        started = getattr(ctx, "_catalog_probe_started", None)
        if started is not None and active[0] is not None:
            catalog_times[active[0]].append((perf_counter() - started) * 1000)

    event.listen(repository.engine, "before_cursor_execute", before)
    event.listen(repository.engine, "after_cursor_execute", after)
    try:
        for repeat in range(6):
            pairs = [("baseline", old, True), ("candidate", new, False)]
            for name, client, baseline in (pairs if repeat % 2 == 0 else reversed(pairs)):
                active[0] = name
                started = perf_counter()
                response = request(client, baseline, headers={
                    **HEADERS, "if-none-match": first.headers["etag"],
                })
                times[name].append((perf_counter() - started) * 1000)
                assert response.status_code == 304 and not response.content
                assert response.headers["etag"] == first.headers["etag"]
                assert response.headers["x-export-artifact-read-calls"] == "0"
    finally:
        event.remove(repository.engine, "before_cursor_execute", before)
        event.remove(repository.engine, "after_cursor_execute", after)
    proof = {
        "production": False, "catalogLocations": 1452, "approvedLocations": 1452,
        "metadataUtf8BytesPerLocation": len(json.dumps(metadata, ensure_ascii=False).encode()),
        "pairedAlternatingSamples": 6, "conditionalWallMs": times,
        "catalogSqlMs": catalog_times,
        "medianConditionalMs": {k: median(v) for k, v in times.items()},
        "medianCatalogSqlMs": {k: median(v) for k, v in catalog_times.items()},
        "bodyEtagCursorEqual": True, "conditionalStatuses": [304] * 12,
        "conditionalArtifactReads": 0, "snapshotId": frozen,
        "itemsSHA256": hashlib.sha256(json.dumps(first.json()["items"],
                                                sort_keys=True).encode()).hexdigest(),
    }
    print("CATALOG_PROJECTION_PAIRED " + json.dumps(proof))
