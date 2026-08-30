"""AirKorea 원본 값의 타입 변환 헬퍼."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

KST = timezone(timedelta(hours=9))

_EMPTY_TEXTS = {"", "-", "null", "none", "nan"}


def strip_or_none(value: Any) -> str | None:
    """원본 API 값을 정리하고 빈 placeholder를 ``None``으로 정규화합니다."""

    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _EMPTY_TEXTS:
        return None
    return text


def to_float_or_none(value: Any) -> float | None:
    text = strip_or_none(value)
    if text is None:
        return None
    return float(text.replace(",", ""))


def to_int_or_none(value: Any) -> int | None:
    text = strip_or_none(value)
    if text is None:
        return None
    try:
        return int(Decimal(text.replace(",", "")))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid integer value: {value!r}") from exc


def to_date_or_none(value: Any) -> date | None:
    text = strip_or_none(value)
    if text is None:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"invalid date value: {value!r}")


def parse_data_time(value: Any) -> datetime | None:
    """AirKorea의 흔한 KST 날짜/시간 문자열을 시간대 포함 datetime으로 파싱합니다."""

    text = strip_or_none(value)
    if text is None:
        return None
    normalized = text.replace("시 발표", ":00").replace("시", ":00")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H%M", "%Y%m%d%H%M"):
        candidate = normalized
        extra_day = False
        if fmt == "%Y-%m-%d %H:%M" and candidate.endswith(" 24:00"):
            candidate = candidate[:-6] + " 00:00"
            extra_day = True
        elif fmt == "%Y-%m-%d %H%M" and candidate.endswith(" 2400"):
            candidate = candidate[:-5] + " 0000"
            extra_day = True
        elif fmt == "%Y%m%d%H%M" and len(candidate) == 12 and candidate.endswith("2400"):
            candidate = candidate[:-4] + "0000"
            extra_day = True
        try:
            parsed = datetime.strptime(candidate, fmt)
        except ValueError:
            continue
        if extra_day:
            parsed += timedelta(days=1)
        return parsed.replace(tzinfo=KST)
    if len(normalized) == 10:
        try:
            return datetime.strptime(normalized, "%Y-%m-%d").replace(tzinfo=KST)
        except ValueError:
            pass
    raise ValueError(f"invalid AirKorea data time: {value!r}")
