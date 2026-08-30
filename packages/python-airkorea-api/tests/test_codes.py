from __future__ import annotations

import pytest

from airkorea.codes import (
    AirQualityGrade,
    DataTerm,
    InformCode,
    Pollutant,
    SidoName,
    StatsDataGubun,
    StatsSearchCondition,
    grade_label,
    normalize_data_term,
    normalize_inform_code,
    normalize_pollutant_code,
    normalize_stats_data_gubun,
    normalize_stats_search_condition,
    validate_sido_name,
)


def test_grade_label() -> None:
    assert grade_label("1") == "좋음"
    assert grade_label(2) == "보통"
    assert grade_label(AirQualityGrade.BAD) == "나쁨"
    assert grade_label("3") == "나쁨"
    assert grade_label("4") == "매우나쁨"
    assert grade_label("5") is None
    assert grade_label("-") is None
    assert AirQualityGrade.from_code("2") is AirQualityGrade.MODERATE
    assert AirQualityGrade.VERY_BAD.label == "매우나쁨"


def test_normalize_data_term() -> None:
    assert normalize_data_term("daily") == "DAILY"
    assert normalize_data_term(DataTerm.DAILY) == "DAILY"
    assert normalize_data_term("MONTH") == "MONTH"
    assert normalize_data_term("3month") == "3MONTH"

    with pytest.raises(ValueError):
        normalize_data_term("YEAR")


def test_normalize_inform_code() -> None:
    assert normalize_inform_code(None) is None
    assert normalize_inform_code("pm10") == "PM10"
    assert normalize_inform_code(InformCode.PM10) == "PM10"
    assert normalize_inform_code(Pollutant.PM25) == "PM25"
    assert normalize_inform_code("PM25") == "PM25"
    assert normalize_inform_code("o3") == "O3"

    with pytest.raises(ValueError):
        normalize_inform_code("NO2")


def test_validate_sido_name() -> None:
    assert validate_sido_name("서울") == "서울"
    assert validate_sido_name(SidoName.SEOUL) == "서울"
    assert validate_sido_name(" 경기 ") == "경기"

    with pytest.raises(ValueError):
        validate_sido_name("서울특별시")


def test_normalize_pollutant_code() -> None:
    assert normalize_pollutant_code("pm2.5") == "PM25"
    assert normalize_pollutant_code(Pollutant.SO2) == "SO2"
    assert normalize_pollutant_code("pm10", allowed=(Pollutant.PM10, Pollutant.PM25)) == "PM10"

    with pytest.raises(ValueError):
        normalize_pollutant_code("SO2", allowed=(Pollutant.PM10, Pollutant.PM25))

    with pytest.raises(ValueError):
        normalize_pollutant_code("TSP")


def test_normalize_statistics_enums() -> None:
    assert normalize_stats_data_gubun(StatsDataGubun.HOUR) == "HOUR"
    assert normalize_stats_data_gubun("daily") == "DAILY"
    assert normalize_stats_search_condition(StatsSearchCondition.WEEK) == "WEEK"
    assert normalize_stats_search_condition("month") == "MONTH"

    with pytest.raises(ValueError):
        normalize_stats_data_gubun("YEAR")

    with pytest.raises(ValueError):
        normalize_stats_search_condition("YEAR")
