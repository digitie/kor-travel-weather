from __future__ import annotations

from datetime import date

import pytest

from airkorea._convert import (
    KST,
    parse_data_time,
    strip_or_none,
    to_date_or_none,
    to_float_or_none,
    to_int_or_none,
)


@pytest.mark.parametrize("value", [None, "", " ", "-", "null", "None", "NaN"])
def test_strip_or_none_handles_placeholders(value: object) -> None:
    assert strip_or_none(value) is None


def test_strip_or_none_preserves_real_text() -> None:
    assert strip_or_none(" 종로구 ") == "종로구"
    assert strip_or_none(123) == "123"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("35", 35.0),
        ("1,234.5", 1234.5),
        (35, 35.0),
        ("-", None),
    ],
)
def test_to_float_or_none(value: object, expected: float | None) -> None:
    assert to_float_or_none(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("35", 35),
        ("35.0", 35),
        ("1,234", 1234),
        ("-", None),
    ],
)
def test_to_int_or_none(value: object, expected: int | None) -> None:
    assert to_int_or_none(value) == expected


@pytest.mark.parametrize("value", ["bad", object()])
def test_numeric_parse_rejects_bad_values(value: object) -> None:
    with pytest.raises(ValueError):
        to_float_or_none(value)

    with pytest.raises(ValueError):
        to_int_or_none(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-04-30", date(2026, 4, 30)),
        ("20260430", date(2026, 4, 30)),
        ("-", None),
    ],
)
def test_to_date_or_none(value: object, expected: date | None) -> None:
    assert to_date_or_none(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-04-30 14:00", "2026-04-30T14:00:00+09:00"),
        ("2026-04-30 14시", "2026-04-30T14:00:00+09:00"),
        ("2026-04-30 14시 발표", "2026-04-30T14:00:00+09:00"),
        ("202604301400", "2026-04-30T14:00:00+09:00"),
    ],
)
def test_parse_data_time(value: str, expected: str) -> None:
    parsed = parse_data_time(value)

    assert parsed is not None
    assert parsed.tzinfo == KST
    assert parsed.isoformat() == expected


def test_parse_data_time_rejects_bad_value() -> None:
    with pytest.raises(ValueError):
        parse_data_time("2026/04/30 14:00")
