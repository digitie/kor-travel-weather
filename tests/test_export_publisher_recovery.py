"""확정 terminal과 아직 불확실한 export 후보의 복구 경계를 검증한다."""

import os

import pytest

from kortravelweather.repository import WeatherRepository


@pytest.fixture(scope="module")
def publisher_repository():
    repository = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    repository.create_schema()
    yield repository
    repository.engine.dispose()


@pytest.mark.parametrize("terminal", ["failed", "partial", "cancelled"])
def test_promotion_keeps_confirmed_rejection_distinct_from_pending(
    publisher_repository, tmp_path, terminal
):
    from test_export_heads import _candidate, _descriptor

    heads, identity, capture, *_ = _candidate(
        publisher_repository, tmp_path, _descriptor(publisher_repository), finish=terminal
    )
    assert heads.promote(identity, capture.artifacts) == "REJECTED"
