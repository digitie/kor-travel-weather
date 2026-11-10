"""Marker가 fact를 다시 읽을 때 partition key를 보존하는지 확인한다."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, select

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue, kst_now
from kortravelweather.repository import WeatherRepository, WeatherValueRow


def _marker_statements(location_ids, alert_keys=()):
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

        def execute(self, stmt):
            statements.append(stmt)
            if list(stmt.selected_columns.keys())[-2:] == ["provider", "payload"]:
                return SimpleNamespace(all=lambda: list(alert_keys))
            return SimpleNamespace(all=lambda: [])

        def scalars(self, stmt):
            statements.append(stmt)
            return Result()

    repo = WeatherRepository.__new__(WeatherRepository)
    repo._session_factory = lambda: Session()
    result = repo.marker_values_many(location_ids, now=datetime(2026, 10, 16, tzinfo=UTC))
    return statements, result


def test_marker_alert_fact_rejoin_preserves_composite_partition_key():
    key = SimpleNamespace(
        location_id="marker-fixture", value_id="v1", known_at=datetime(2026, 10, 15, tzinfo=UTC),
        provider="fixture", payload={},
    )
    statements, _ = _marker_statements(["marker-fixture"], alert_keys=[key])
    # 위치 조회, 관측 후보, 대체 후보, 특보 순위 key, 남은 key의 fact 조회.
    assert len(statements) == 5
    hydration = str(statements[4])
    assert "provider_alert_keys" in hydration
    # 정확한 복합 키(value_id, known_at)로만 fact를 다시 읽는다(partition key 보존).
    assert "weather_values.value_id = provider_alert_keys.value_id" in hydration
    assert "weather_values.known_at = provider_alert_keys.known_at" in hydration


def test_marker_empty_batch_never_opens_a_session():
    statements, result = _marker_statements([])
    assert statements == [] and result == {}


@pytest.fixture
def repository(weather_test_database_url):
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    for key in ("requested", "unrequested"):
        repo.upsert_location(WeatherLocation(location_id=key, name=key, latitude=37, longitude=127))
    yield repo
    repo.engine.dispose()


@pytest.mark.parametrize("kind", ["observed", "fallback", "alert"])
@pytest.mark.parametrize("providers", [None, ["fixture"]])
def test_marker_never_rejoins_same_uuid_from_another_partition(repository, kind, providers):
    """같은 UUID도 partition key가 다르면 선택하지 않은 fact다."""
    now = kst_now().astimezone(UTC)
    known = now - timedelta(days=1)
    is_alert = kind == "alert"
    repository.record_source(
        source_record_key="marker-pair-source",
        provider="fixture",
        dataset_key="marker-pair",
        source_entity_type="weather_response",
        source_entity_id="requested",
        payload={"fixture": kind},
        fetched_at=known,
    )
    repository.upsert_values(
        [
            WeatherValue(
                location_id="requested",
                provider="fixture",
                dataset_key="marker-pair",
                weather_domain="weather_alert" if is_alert else "weather",
                forecast_style=ForecastStyle.SHORT
                if kind == "fallback"
                else ForecastStyle.OBSERVED,
                metric_key="ALERT" if is_alert else "TEMP",
                target_at=known,
                known_at=known,
                value_number=None if is_alert else Decimal("20"),
                value_text="호우주의보 발표" if is_alert else None,
                valid_until=now + timedelta(days=1) if is_alert else None,
                payload={"stn_id": "fixture", "seq": "1"} if is_alert else {},
                source_record_key="marker-pair-source",
            )
        ]
    )
    with repository._session_factory.begin() as session:
        row = session.scalars(select(WeatherValueRow)).one()
        shadow = {col.name: getattr(row, col.name) for col in WeatherValueRow.__table__.columns}
        # PK (value_id, known_at)가 허용하는 다른 fact. 요청하지 않은 위치의
        # UUID가 같다는 이유로 marker에 섞이거나 요청별 한도가 늘면 안 된다.
        shadow.update(location_id="unrequested", known_at=now - timedelta(minutes=1))
        session.execute(insert(WeatherValueRow), shadow)
    result = repository.marker_values_many(["requested"], now=now, providers=providers)
    assert set(result) == {"requested"}
    assert len(result["requested"]) == 1
    assert result["requested"][0].known_at == known
