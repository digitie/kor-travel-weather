from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import BaseModel, ValidationError

from airkorea._convert import KST
from airkorea.models import AirKoreaModel, AirQualityMeasurement


def measurement_model() -> AirQualityMeasurement:
    return AirQualityMeasurement(
        station_name="종로구",
        data_time=datetime(2026, 4, 30, 14, tzinfo=KST),
        mang_name="도시대기",
        sido_name="서울",
        khai_value=74,
        khai_grade=2,
        khai_grade_label="보통",
        so2_value=0.003,
        co_value=0.4,
        o3_value=0.031,
        no2_value=0.025,
        pm10_value=35,
        pm10_value_24h=42,
        pm25_value=17,
        pm25_value_24h=19,
        so2_grade=1,
        co_grade=1,
        o3_grade=2,
        no2_grade=2,
        pm10_grade=2,
        pm10_grade_1h=1,
        pm25_grade=2,
        pm25_grade_1h=1,
        raw={"stationName": "종로구"},
    )


def test_response_models_are_pydantic_models() -> None:
    row = measurement_model()

    assert isinstance(row, AirKoreaModel)
    assert isinstance(row, BaseModel)
    assert row.model_dump()["station_name"] == "종로구"
    assert row.model_dump(mode="json")["data_time"] == "2026-04-30T14:00:00+09:00"
    assert row.model_dump()["raw"] == {"stationName": "종로구"}


def test_response_models_are_frozen_and_validated() -> None:
    row = measurement_model()

    with pytest.raises(ValidationError):
        row.station_name = "중구"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        AirQualityMeasurement.model_validate(
            {
                **row.model_dump(),
                "pm10_value": "not-a-number",
            }
        )
