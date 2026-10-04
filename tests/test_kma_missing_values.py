"""KMA missing-observation sentinels and out-of-range values.

From 2026-09-30 on, prod ``kma_ultra_short_nowcast_job`` failed on every hourly
run with ``REH 값은 0 이상이어야 합니다``. A read-only replay of the job's fetch
(2026-10-05 04:00 KST) found the response below for grid (35, 106). Its
station reported no observation, and KMA encodes that as numeric
sentinels. The KMA 단기예보 조회서비스 guide treats values of +900 or more
and -900 or less as Missing. ``WeatherValue`` rejected the REH/RN1/VEC/WSD
sentinels, so one grid aborted the whole run. The T1H/UUU/VVV sentinels
passed validation, so they would have been stored as -999 °C and -998.9 m/s.
"""

from __future__ import annotations

import logging
from collections import Counter
from decimal import Decimal

import pytest

from kortravelweather.providers.kma import (
    short_forecast_to_weather_values,
    ultra_short_forecast_to_weather_values,
    ultra_short_nowcast_to_weather_values,
)


def _nowcast(category: str, value: str) -> dict[str, object]:
    return {
        "baseDate": "20261005",
        "baseTime": "0400",
        "nx": 35,
        "ny": 106,
        "category": category,
        "obsrValue": value,
    }


# getUltraSrtNcst for grid (35, 106), base 20261005 0400, exactly as returned
# (PTY was the only category without a sentinel in that response).
GRID_35_106_MISSING_STATION = [
    _nowcast("PTY", "0"),
    _nowcast("REH", "-998"),
    _nowcast("RN1", "-998.9"),
    _nowcast("T1H", "-999"),
    _nowcast("UUU", "-998.9"),
    _nowcast("VEC", "-998"),
    _nowcast("VVV", "-998.9"),
    _nowcast("WSD", "-998.9"),
]


def _forecast(category: str, value: str) -> dict[str, object]:
    return {
        "baseDate": "20261005",
        "baseTime": "0500",
        "fcstDate": "20261005",
        "fcstTime": "0600",
        "nx": 35,
        "ny": 106,
        "category": category,
        "fcstValue": value,
    }


def test_missing_station_nowcast_skips_each_sentinel_metric_instead_of_failing() -> None:
    skipped: Counter[str] = Counter()

    values = ultra_short_nowcast_to_weather_values(
        GRID_35_106_MISSING_STATION, location_id="airkorea-station-28b66347b615", skipped=skipped
    )

    assert [(value.metric_key, value.value_number) for value in values] == [
        ("PTY", Decimal("0"))
    ]
    assert skipped == Counter(
        {
            f"kma_ultra_short_nowcast:missing:{category}": 1
            for category in ("REH", "RN1", "T1H", "UUU", "VEC", "VVV", "WSD")
        }
    )


def test_sentinels_that_pass_range_checks_are_not_stored_as_measurements() -> None:
    # T1H -999 and UUU -998.9 satisfy every WeatherValue range check, so they
    # are the dangerous half: no error, just a -999 °C reading.
    for row in (_nowcast("T1H", "-999"), _nowcast("UUU", "-998.9"), _nowcast("T1H", "900")):
        assert ultra_short_nowcast_to_weather_values([row], location_id="x") == []


@pytest.mark.parametrize(
    "converter, dataset_key",
    [
        (ultra_short_forecast_to_weather_values, "kma_ultra_short_forecast"),
        (short_forecast_to_weather_values, "kma_short_forecast"),
    ],
)
def test_forecasts_share_the_missing_sentinel_rule(converter, dataset_key) -> None:
    skipped: Counter[str] = Counter()
    rows = [
        _forecast("TMP", "-998.9"),
        _forecast("REH", "-999"),
        _forecast("WSD", "999.0"),
        _forecast("TMP", "12"),
    ]

    values = converter(rows, location_id="x", skipped=skipped)

    assert [(value.metric_key, value.value_number) for value in values] == [
        ("TMP", Decimal("12"))
    ]
    assert skipped == Counter(
        {
            f"{dataset_key}:missing:TMP": 1,
            f"{dataset_key}:missing:REH": 1,
            f"{dataset_key}:missing:WSD": 1,
        }
    )


def test_an_impossible_non_sentinel_value_skips_only_that_metric() -> None:
    skipped: Counter[str] = Counter()
    rows = [
        _nowcast("REH", "105"),  # real validation still rejects this
        _nowcast("WSD", "-0.5"),
        _nowcast("T1H", "-12.3"),  # legitimately negative: kept
        _nowcast("REH", "40"),
    ]

    values = ultra_short_nowcast_to_weather_values(rows, location_id="x", skipped=skipped)

    assert [(value.metric_key, value.value_number) for value in values] == [
        ("T1H", Decimal("-12.3")),
        ("REH", Decimal("40")),
    ]
    assert skipped == Counter(
        {"kma_ultra_short_nowcast:invalid:REH": 1, "kma_ultra_short_nowcast:invalid:WSD": 1}
    )


def test_skips_are_logged_even_without_a_counter(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Alembic's fileConfig (run by migration tests earlier in the session)
    # disables loggers that already exist; this test is about our call only.
    monkeypatch.setattr(logging.getLogger("kortravelweather.providers.kma"), "disabled", False)
    with caplog.at_level("WARNING", logger="kortravelweather.providers.kma"):
        values = ultra_short_nowcast_to_weather_values(
            [_nowcast("REH", "-998"), _nowcast("REH", "105")], location_id="x"
        )
    assert values == []
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "missing" in messages and "invalid" in messages and "(35,106)" in messages


def test_structural_row_errors_still_fail_loudly() -> None:
    # Only value-level problems are skipped; an unknown category or a broken
    # timestamp is a contract change and must still stop the run.
    with pytest.raises(ValueError, match="지원하지 않는 KMA category"):
        ultra_short_nowcast_to_weather_values([_nowcast("XYZ", "1")], location_id="x")
    with pytest.raises(ValueError, match="datetime"):
        ultra_short_nowcast_to_weather_values(
            [{**_nowcast("REH", "40"), "baseTime": "4"}], location_id="x"
        )


# -- review follow-ups ---------------------------------------------------------


@pytest.mark.parametrize("raw", ["NaN", "nan", "Infinity", "-Infinity", "sNaN"])
def test_non_finite_values_are_counted_invalid_not_raised(raw: str) -> None:
    skipped: Counter[str] = Counter()
    values = ultra_short_nowcast_to_weather_values(
        [_nowcast("T1H", raw), _nowcast("REH", "40")], location_id="x", skipped=skipped
    )
    assert [value.metric_key for value in values] == ["REH"]
    assert skipped == Counter({"kma_ultra_short_nowcast:invalid:T1H": 1})


def test_only_value_range_errors_are_skipped_everything_else_still_raises() -> None:
    from datetime import datetime

    from pydantic import ValidationError

    # Neither a number nor a text: a broken row, not an out-of-range value.
    with pytest.raises(ValidationError, match="value_number 또는 value_text"):
        ultra_short_nowcast_to_weather_values([_nowcast("REH", "")], location_id="x")
    with pytest.raises(ValidationError, match="timezone-aware"):
        ultra_short_nowcast_to_weather_values(
            [_nowcast("REH", "40")], location_id="x", known_at=datetime(2026, 10, 5, 4)
        )
    with pytest.raises(ValidationError, match="255"):
        ultra_short_nowcast_to_weather_values(
            [_nowcast("REH", "40")], location_id="x", source_record_key="k" * 256
        )


def test_skip_details_are_logged_for_the_first_skips_of_a_run_only(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kortravelweather.providers.kma import KMA_SKIP_LOG_DETAILS

    monkeypatch.setattr(logging.getLogger("kortravelweather.providers.kma"), "disabled", False)
    skipped: Counter[str] = Counter()
    rows = [_nowcast("REH", "-998") for _ in range(KMA_SKIP_LOG_DETAILS + 30)]
    with caplog.at_level("WARNING", logger="kortravelweather.providers.kma"):
        ultra_short_nowcast_to_weather_values(rows, location_id="x", skipped=skipped)
    assert skipped["kma_ultra_short_nowcast:missing:REH"] == KMA_SKIP_LOG_DETAILS + 30
    assert len(caplog.records) == KMA_SKIP_LOG_DETAILS


@pytest.mark.parametrize(
    "converter, category",
    [
        (short_forecast_to_weather_values, "PCP"),
        (short_forecast_to_weather_values, "SNO"),
        (ultra_short_forecast_to_weather_values, "RN1"),
    ],
)
@pytest.mark.parametrize(
    "raw, number, text",
    [
        ("1.0mm", None, "1.0mm"),
        ("강수없음", Decimal("0"), "강수없음"),
        ("30.0~50.0mm", None, "30.0~50.0mm"),
    ],
)
def test_precipitation_labels_are_kept(converter, category, raw, number, text) -> None:
    if category == "SNO" and raw == "강수없음":
        raw, text = "적설없음", "적설없음"
    skipped: Counter[str] = Counter()
    values = converter([_forecast(category, raw)], location_id="x", skipped=skipped)
    assert [(value.value_number, value.value_text) for value in values] == [(number, text)]
    assert not skipped


@pytest.mark.parametrize(
    "raw, kept",
    [("899.9", True), ("-899.9", True), ("-900", False), ("900", False), ("-900.0", False)],
)
def test_the_missing_boundary_is_exactly_900(raw: str, kept: bool) -> None:
    skipped: Counter[str] = Counter()
    values = ultra_short_nowcast_to_weather_values(
        [_nowcast("T1H", raw)], location_id="x", skipped=skipped
    )
    assert bool(values) is kept
    assert bool(skipped) is not kept
