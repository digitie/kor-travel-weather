"""요청 입력은 bind하고 부분 인덱스의 고정 조건만 SQL에 남긴다."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

from kortravelweather.repository import WeatherRepository

NOW = datetime(2026, 10, 16, 3, 50, 34, tzinfo=UTC)
METRIC_SQL = "metric_key IN ('TEMP', 'T1H', 'TMP', 'WEATHER_CODE', 'SKY', 'PTY')"


def _statements(location_id="marker-fixture"):
    statements = []

    class Result:
        def all(self):
            return []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def scalars(self, stmt):
            statements.append(stmt)
            return Result()

    repo = WeatherRepository.__new__(WeatherRepository)
    repo._session_factory = lambda: Session()
    repo.marker_values_many([location_id], now=NOW)
    return statements


def _compile(stmt):
    return stmt.compile(
        dialect=PGDialect_psycopg(), compile_kwargs={"render_postcompile": True}
    )


@pytest.mark.parametrize("index", [0, 1], ids=["observed", "fallback"])
def test_marker_metric_partial_index_guard_survives_generic_bind(index):
    compiled = _compile(_statements()[index])
    assert METRIC_SQL in str(compiled)
    assert "__[POSTCOMPILE" not in str(compiled)


def test_marker_observed_partial_index_guard_survives_generic_bind():
    compiled = _compile(_statements()[0])
    assert "forecast_style IN ('observed', 'nowcast')" in str(compiled)


def test_marker_alert_partial_index_guard_survives_generic_bind():
    compiled = _compile(_statements()[2])
    assert "weather_domain = 'weather_alert'" in str(compiled)
    assert "metric_key = 'ALERT'" in str(compiled)


@pytest.mark.parametrize("index", [0, 1, 2], ids=["observed", "fallback", "alerts"])
def test_marker_request_location_remains_bound(index):
    location = "request-quote-'-- %(__literal__)s"
    compiled = _compile(_statements(location)[index])
    assert location not in str(compiled)
    assert location in compiled.params.values()
    # 후보/경보의 한도도 내부 predicate 고정과 별개의 기존 bind 계약이다.
    assert any(isinstance(value, int) for value in compiled.params.values())


def test_marker_alert_time_and_json_fields_remain_bound():
    compiled = _compile(_statements()[2])
    assert NOW in compiled.params.values()
    assert "'stn_id'" not in str(compiled)
    assert "stn_id" in compiled.params.values()
