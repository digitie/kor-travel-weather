"""지도 응답은 요청별 SQL 컴파일과 같은 특보 묶음의 반복 판정을 피한다."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import Request
from kortravelweather_api.routers import weather
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherRepository


def _statements(ids):
    statements = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def scalars(self, stmt):
            statements.append(stmt)
            return SimpleNamespace(all=lambda: [])

    repo = WeatherRepository.__new__(WeatherRepository)
    repo._session_factory = Session
    repo.marker_values_many(ids, now=datetime(2026, 10, 16, tzinfo=UTC))
    return statements


@pytest.mark.parametrize("index", [0, 1, 2], ids=["observed", "fallback", "alerts"])
def test_marker_query_cache_is_reused_across_location_batches(index):
    first = _statements(["one"])[index]
    second = _statements(["two", "three"])[index]
    first_key = first._generate_cache_key()
    second_key = second._generate_cache_key()
    assert first_key is not None and second_key is not None
    assert first_key.key == second_key.key
    # 위치 내용/개수가 달라도 SQL 구조만 재사용하며 실제 입력은 별도 bind다.
    dialect = PGDialect_psycopg()
    assert first.compile(dialect=dialect).params != second.compile(dialect=dialect).params


def test_marker_shared_projection_reduces_alerts_once(monkeypatch):
    location = WeatherLocation(location_id="one", name="one", latitude=37, longitude=127)
    row = WeatherValue(
        location_id="one", provider="fixture", dataset_key="weather_alert",
        weather_domain="weather_alert", forecast_style="observed", metric_key="ALERT",
        value_text="호우주의보 발표",
    )
    calls = []
    original = weather.active_alert_values

    def record(rows):
        calls.append(rows)
        return original(rows)

    monkeypatch.setattr(weather, "active_alert_values", record)
    repo = SimpleNamespace(
        get_locations_by_ids=lambda *args, **kwargs: [location],
        marker_values_many=lambda *args, **kwargs: {"one": [row]},
    )
    request = Request({"type": "http", "headers": []})
    result = asyncio.run(weather.marker_summaries(request, repo, ["one"]))
    assert len(calls) == 1
    assert result["data"][0]["alerts"][0]["value_text"] == "호우주의보 발표"
    assert result["data"][0]["latest"] == []
