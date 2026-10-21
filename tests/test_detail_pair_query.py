"""상세 조회가 projection의 정확한 fact revision만 읽는지 검증한다."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, select
from sqlalchemy.dialects import postgresql

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.repository import WeatherRepository, WeatherValueRow


@pytest.fixture
def repository(weather_test_database_url):
    repo = WeatherRepository(weather_test_database_url)
    repo.create_schema()
    for key in ("requested", "unrequested"):
        repo.upsert_location(WeatherLocation(location_id=key, name=key, latitude=37, longitude=127))
    yield repo
    repo.engine.dispose()


def seed(repository, *, alert=False, expired=False):
    now = datetime.now(UTC)
    known = now - timedelta(hours=1)
    repository.record_source(
        source_record_key="detail-source", provider="fixture", dataset_key="detail",
        source_entity_type="response", source_entity_id="requested", payload={}, fetched_at=known,
    )
    repository.upsert_values([WeatherValue(
        location_id="requested", provider="fixture", dataset_key="detail",
        weather_domain="weather_alert" if alert else "weather",
        forecast_style=ForecastStyle.OBSERVED,
        metric_key="ALERT" if alert else "TEMP", target_at=now - timedelta(days=4) if expired
        else known, known_at=known, collected_at=known,
        value_number=None if alert else Decimal("20"),
        value_text="호우주의보" if alert else None,
        valid_until=now - timedelta(days=1) if expired else now + timedelta(days=1),
        source_record_key="detail-source", payload={"raw": "kept"},
    )])
    with repository._session_factory.begin() as session:
        row = session.scalars(select(WeatherValueRow)).one()
        shadow = {col.name: getattr(row, col.name) for col in WeatherValueRow.__table__.columns}
        shadow.update(location_id="unrequested", known_at=now,
                      value_number=None if alert else Decimal("99"),
                      valid_until=now + timedelta(days=1))
        session.execute(insert(WeatherValueRow), shadow)
    return now, known


@pytest.mark.parametrize("method", ["latest_values", "timeline", "latest_values_many",
                                    "forecast_many", "timeline_many", "alert_values_many"])
def test_detail_preserves_exact_selected_pair(repository, method):
    now, known = seed(repository, alert=method == "alert_values_many")
    kwargs = {"from_at": known - timedelta(seconds=1)} if method == "forecast_many" else {}
    result = getattr(repository, method)(
        ["requested"] if method.endswith("many") else "requested", **kwargs,
    )
    rows = result["requested"] if isinstance(result, dict) else result
    assert len(rows) == 1
    assert rows[0].known_at == known
    assert rows[0].location_id == "requested"
    assert rows[0].payload == {"raw": "kept"}


def test_expired_alert_cannot_borrow_valid_until_from_other_revision(repository):
    now, _ = seed(repository, alert=True, expired=True)
    assert repository.alert_values_many(["requested"], now=now) == {"requested": []}


def test_fixed_preference_and_hydration_keep_cacheable_bind_boundaries():
    statements = []
    known = datetime(2026, 10, 21, tzinfo=UTC)

    class Session:
        def execute(self, stmt):
            statements.append(stmt)
            return SimpleNamespace(all=lambda: [SimpleNamespace(
                location_id="request'; --", value_id="id", known_at=known,
            )])

        def scalars(self, stmt):
            statements.append(stmt)
            return SimpleNamespace(all=lambda: [])

    def capture(ids):
        statements.clear()
        repo = WeatherRepository.__new__(WeatherRepository)
        repo._current_value_models_many(Session(), ids, limit_per_location=200,
                                        prefer_current=True, now=known)
        return [stmt.compile(dialect=postgresql.dialect(),
                             compile_kwargs={"render_postcompile": True}) for stmt in statements]

    first, hydration = capture(["request'; --"])
    assert "IN ('observed', 'nowcast') THEN 0 ELSE 1 END" in str(first)
    assert "request'; --" not in str(first)
    assert first.params["current_location_ids"] == ["request'; --"]
    assert "weather_values.known_at = current_fact_keys.known_at" in str(hydration)
    assert "weather_values.known_at >= " in str(hydration)
    assert "weather_values.known_at <= " in str(hydration)
    one = [stmt._generate_cache_key().key for stmt in statements]
    capture(["a", "b"])
    assert one == [stmt._generate_cache_key().key for stmt in statements]
