"""fact COPY 중 원래 오류를 driver cleanup 오류로 바꾸지 않는다."""

from types import SimpleNamespace

import psycopg
import pytest
from sqlalchemy import insert
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

from kortravelweather.repository import WeatherValueRow, _copy_weather_facts


class CleanupFailure(Exception):
    pass


class ControlledCopy(psycopg.Copy):
    def __repr__(self):
        return "<controlled-fact-copy>"


def _cursor_and_context(original):
    # 실제 Copy context/finish/write_row를 쓴다. 전송 writer만 실패시켜 PG 없이
    # 종료 오류를 결정적으로 재현하며, SDK나 제품 메서드는 monkeypatch하지 않는다.
    copy = object.__new__(ControlledCopy)
    copy._direction = psycopg.pq.ExecStatus.COPY_IN
    copy._finished = False

    def write_row(_row):
        if original is not None:
            raise original
        return b""

    def finish(_error):
        raise CleanupFailure("synthetic COPY cleanup failure")

    copy.formatter = SimpleNamespace(write_row=write_row, end=lambda: b"")
    copy.writer = SimpleNamespace(finish=finish)
    calls = []
    copy._write = calls.append

    def start(statement):
        calls.append(statement)
        return copy

    cursor = SimpleNamespace(
        copy=start, connection=SimpleNamespace(pgconn=SimpleNamespace(pipeline_status=0)),
    )
    context = SimpleNamespace(
        execution_options={"weather_fact_copy": True},
        dialect=PGDialect_psycopg(),
        compiled=SimpleNamespace(
            isinsert=True, statement=insert(WeatherValueRow.__table__), returning=(),
        ),
    )
    parameters = [dict.fromkeys(WeatherValueRow.__table__.columns.keys())]
    return cursor, context, parameters, calls


@pytest.mark.parametrize("original", [ValueError("synthetic row error"), KeyboardInterrupt()])
def test_fact_copy_preserves_original_error_when_driver_cleanup_fails(original):
    cursor, context, parameters, calls = _cursor_and_context(original)
    with pytest.raises(type(original)) as captured:
        _copy_weather_facts(cursor, "unused", parameters, context)
    assert captured.value is original
    assert len(calls) == 1
    assert calls[0].startswith("COPY weather_values ")


def test_fact_copy_does_not_hide_cleanup_error_without_original_failure():
    cursor, context, parameters, calls = _cursor_and_context(None)
    with pytest.raises(CleanupFailure):
        _copy_weather_facts(cursor, "unused", parameters, context)
    assert len(calls) == 1
