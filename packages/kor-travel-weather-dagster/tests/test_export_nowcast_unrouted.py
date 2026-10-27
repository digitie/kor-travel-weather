"""export capture로 가지 않는 실황 격자의 준비 attempt를 preparing으로 남기지 않는다."""

from __future__ import annotations

import os

import httpx
import pytest
from kma import KmaClient
from kortravelweather_dagster.kma_weather import (
    KMA_ULTRA_SHORT_NOWCAST,
    WeatherTarget,
    run_weather_sync,
)
from sqlalchemy import func, select, text

from kortravelweather.export_collector import publish_pending_exports
from kortravelweather.export_heads import ExportHeads
from kortravelweather.models import WeatherLocation
from kortravelweather.repository import WeatherRepository

METRICS = [("T1H", "18.4"), ("RN1", "0"), ("UUU", "0"), ("VVV", "0"),
           ("REH", "50"), ("PTY", "0"), ("VEC", "120"), ("WSD", "1")]


@pytest.fixture
def repository():
    repository = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    repository.create_schema()

    def clean():
        with repository.engine.begin() as connection:
            connection.execute(text(
                "TRUNCATE weather_export_scope_plans, weather_export_heads, "
                "weather_export_generations, weather_export_generation_chunks, "
                "weather_export_pending_refs, weather_export_attempts, weather_locations, "
                "weather_sync_runs RESTART IDENTITY CASCADE"
            ))

    clean()
    yield repository
    clean()
    repository.engine.dispose()


def _client(*, missing_nx):
    """missing_nx 격자는 모든 범주를 KMA Missing 센티널(-999)로 응답한다."""

    def respond(request):
        q = request.url.params
        rows = [dict(
            baseDate=q["base_date"], baseTime=q["base_time"], nx=int(q["nx"]), ny=int(q["ny"]),
            category=category, obsrValue="-999" if int(q["nx"]) == missing_nx else value,
        ) for category, value in METRICS]
        return httpx.Response(200, json={"response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
            "body": {"pageNo": 1, "numOfRows": 1000, "totalCount": len(rows),
                     "items": {"item": rows}},
        }})

    return KmaClient(
        "private-key", session=httpx.AsyncClient(transport=httpx.MockTransport(respond)), retries=0
    )


def test_all_missing_nowcast_grid_closes_its_prepared_attempt(repository, tmp_path):
    targets = [
        WeatherTarget(WeatherLocation(
            location_id=key, name=key, latitude=37.5, longitude=127, nx=nx, ny=127,
        ))
        for key, nx in [("one", 60), ("two", 61)]
    ]
    result = run_weather_sync(
        repository=repository, client=_client(missing_nx=61), targets=targets,
        base_datasets=frozenset({KMA_ULTRA_SHORT_NOWCAST}), export_artifact_root=tmp_path,
    )
    assert result["status"] == "success" and result["values_loaded"] == len(METRICS)
    heads = ExportHeads(repository)
    attempts = heads.lifecycle.attempts
    with repository.engine.connect() as connection:
        rows = {
            row.descriptor["entity_id"]: row
            for row in connection.execute(select(
                attempts.c.attempt_id, attempts.c.status, attempts.c.error_code,
                attempts.c.plan_closed_at, heads.plans.c.descriptor,
            ).join(heads.plans, heads.plans.c.attempt_id == attempts.c.attempt_id))
        }
        empty = rows["grid:61:127"]
        pins = connection.scalar(select(func.count()).select_from(heads.pins).where(
            heads.pins.c.attempt_id == empty.attempt_id,
        ))
    assert empty.status == "failed" and empty.error_code.startswith("NO_NORMALIZED_POINTS")
    assert pins == 0
    healthy = rows["grid:60:127"]
    assert healthy.status == "preparing" and healthy.plan_closed_at is not None
    # 닫힌 attempt는 publisher 후보로 남지 않는다. 정상 격자만 승격된다.
    assert publish_pending_exports(repository, tmp_path).results == {"PUBLISHED": 1}
