"""공개 읽기 마감·lock 상한과 공유 읽기 묶음(호출 검토 B9) 회귀 시험.

마감 설정은 값이 바뀌는 ``SET LOCAL`` 문자열 대신 bind 상수 SQL로 transaction당 한 번 걸고,
공개 읽기에는 ``lock_timeout``이 걸려 lock 대기열 뒤에서 마감을 다 쓰지 않고 503이 된다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient
from kortravelweather_api import read_limits
from kortravelweather_api.app import create_app
from kortravelweather_api.read_limits import public_read_deadline
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.settings import WeatherSettings

FUTURE = datetime(2027, 1, 1, tzinfo=UTC)
PAST = datetime(2026, 11, 20, 3, tzinfo=UTC)


def _seed(repository) -> None:
    """측정점 1곳·KMA 1곳에 관측·예보·특보를 둔다."""
    repository.create_location(WeatherLocation(
        location_id="air-a", name="측정소 A", latitude=37.5, longitude=127.0,
        metadata={"measurement_point": {"provider": "python-airkorea-api",
                                        "station_name": "중구", "address": "서울 중구"}},
    ))
    repository.create_location(WeatherLocation(
        location_id="kma-a", name="KMA A", latitude=37.51, longitude=127.01, nx=60, ny=127,
    ))
    values = []
    for location_id in ("air-a", "kma-a"):
        for suffix, dataset in (
            ("obs", "d"), ("fc", "kma_short_forecast"), ("alert", "kma_weather_alerts"),
        ):
            repository.record_source(
                source_record_key=f"{location_id}-{suffix}", provider="p", dataset_key=dataset,
                source_entity_type="weather_response", source_entity_id=location_id,
                payload={"rows": []},
            )
        for hour in range(4):
            for metric, number in (("T1H", Decimal("12.5")), ("PM10", Decimal("0.0001"))):
                values.append(WeatherValue(
                    location_id=location_id, provider="p", dataset_key="d",
                    weather_domain="weather", forecast_style=ForecastStyle.OBSERVED,
                    metric_key=metric, target_at=PAST - timedelta(hours=hour),
                    value_number=number, source_record_key=f"{location_id}-obs",
                ))
        for hour in range(1, 6):
            for metric in ("TMP", "SKY"):
                values.append(WeatherValue(
                    location_id=location_id, provider="p", dataset_key="kma_short_forecast",
                    weather_domain="weather", forecast_style=ForecastStyle.SHORT,
                    metric_key=metric, target_at=FUTURE + timedelta(hours=hour),
                    value_number=Decimal(hour) / 3,
                    value_text='맑음 "따옴표" \\ \n' if metric == "SKY" else None,
                    source_record_key=f"{location_id}-fc",
                ))
        values.append(WeatherValue(
            location_id=location_id, provider="p", dataset_key="kma_weather_alerts",
            weather_domain="weather_alert", forecast_style=ForecastStyle.OBSERVED,
            metric_key="ALERT", target_at=PAST, valid_until=FUTURE, value_text="호우경보 발표",
            source_record_key=f"{location_id}-alert",
        ))
    repository.upsert_values(values)


def _client(repository, **settings) -> TestClient:
    settings = WeatherSettings(environment="development", **settings)
    return TestClient(create_app(settings, repository))


def test_public_limits_are_transaction_local_bind_settings(repository):
    create_app(WeatherSettings(environment="development"), repository)
    # 값은 bind로만 넘긴다. 문장 문자열은 늘 같아 prepare 후보 LRU를 밀어내지 않는다.
    assert "%s" in read_limits._ARM_SQL and "ms" not in read_limits._ARM_SQL
    with public_read_deadline(15), repository.read_scope():
        with repository._session_factory() as session:  # noqa: SLF001
            lock, statement = session.execute(text(
                "SELECT current_setting('lock_timeout'), current_setting('statement_timeout')"
            )).one()
        with repository._session_factory() as session:  # noqa: SLF001
            # 같은 transaction의 다음 읽기도 같은 상한 아래에 있다.
            assert session.scalar(text("SELECT current_setting('lock_timeout')")) == "2s"
    assert lock == "2s"
    assert statement == "15s" or 14_000 <= int(statement.removesuffix("ms")) < 15_000
    with repository.engine.connect() as connection:
        assert connection.scalar(text("SHOW lock_timeout")) == "0"
        assert connection.scalar(text("SHOW statement_timeout")) == "0"


def test_rearm_only_when_the_armed_timeout_drifts_past_the_slack():
    assert read_limits._needs_arm(None, 15)
    assert not read_limits._needs_arm(15, 14.96)
    assert read_limits._needs_arm(15, 14.9)
    # 짧은 마감은 값의 10%까지만 넘길 수 있다.
    assert not read_limits._needs_arm(.06, .055)
    assert read_limits._needs_arm(.06, .02)
    # 마감이 길어진 경우(다른 문맥)도 다시 건다.
    assert read_limits._needs_arm(5, 15)


def _timeout_ms(value: str) -> int:
    if value.endswith("ms"):
        return int(value.removesuffix("ms"))
    return int(value.removesuffix("s")) * 1000


def test_savepoint_rollback_rearms_the_remaining_deadline_on_the_next_statement(repository):
    """savepoint 안에서 다시 건 값은 rollback으로 되돌아간다. 그 뒤 첫 문장이 다시 건다."""
    create_app(WeatherSettings(environment="development"), repository)
    with public_read_deadline(15), repository.engine.connect() as connection, connection.begin():
        connection.execute(text("SELECT 1"))  # 약 15초로 건다.
        savepoint = connection.begin_nested()
        sleep(0.3)  # 여유 50ms를 넘겨 savepoint 안 문장이 다시 걸게 한다.
        connection.execute(text("SELECT 1"))
        savepoint.rollback()
        actual = connection.scalar(text("SELECT current_setting('statement_timeout')"))
        lock = connection.scalar(text("SELECT current_setting('lock_timeout')"))
    # 되돌아간 15초가 아니라 남은 마감(15 - 0.3초 이하)이 걸려 있다.
    assert _timeout_ms(actual) <= 14_750
    assert lock == "2s"


def test_savepoint_rollback_recovers_from_an_error_under_the_public_deadline(repository):
    """실패한 savepoint의 ``ROLLBACK TO SAVEPOINT`` 앞에서 마감을 걸지 않아 복구가 된다."""
    create_app(WeatherSettings(environment="development"), repository)
    with public_read_deadline(15), repository.engine.connect() as connection, connection.begin():
        connection.execute(text("SELECT 1"))
        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError):
            connection.execute(text("SELECT 1 / 0"))
        savepoint.rollback()
        assert connection.scalar(text("SELECT 2")) == 2
        actual = connection.scalar(text("SELECT current_setting('statement_timeout')"))
    assert 0 < _timeout_ms(actual) <= 15_000


def test_public_read_behind_a_lock_ends_with_503_lock_timeout(repository):
    _seed(repository)
    client = _client(repository)
    with repository.engine.connect() as holder:
        holder.execute(text("LOCK TABLE weather_locations IN ACCESS EXCLUSIVE MODE"))
        started = monotonic()
        response = client.get("/v1/weather/locations")
        elapsed = monotonic() - started
        holder.rollback()
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "LOCK_TIMEOUT"
    assert response.headers["retry-after"] == "1"
    # 마감 15초가 아니라 lock_timeout 2초에서 끝난다.
    assert 1.5 <= elapsed < 8
    assert repository.engine.pool.checkedout() == 0
    assert client.get("/v1/weather/locations").status_code == 200


def test_read_scope_shares_one_connection_and_keeps_writes_separate(repository):
    _seed(repository)
    with repository.read_scope():
        with repository._session_factory() as reader:  # noqa: SLF001
            shared = reader.connection()
            assert reader.scalar(text("SELECT count(*) FROM weather_locations")) == 2
        # 다음 읽기 함수도 같은 연결·transaction을 쓴다.
        assert [row.location_id for row in repository.get_locations_by_ids(["kma-a"])] == ["kma-a"]
        assert repository.public_location_regions([repository.get_location("kma-a")]) == {}
        assert repository.engine.pool.checkedout() == 1
        with repository._session_factory.begin() as writer:  # noqa: SLF001
            assert writer.connection() is not shared
            writer.execute(text(
                "UPDATE weather_locations SET name = 'B' WHERE location_id = 'kma-a'"
            ))
        # 쓰기 commit은 읽기 묶음을 끝내지 않는다.
        assert shared.in_transaction()
        with repository._session_factory() as reader:  # noqa: SLF001
            assert reader.connection() is shared
    assert repository.engine.pool.checkedout() == 0
    assert repository.get_location("kma-a").name == "B"
