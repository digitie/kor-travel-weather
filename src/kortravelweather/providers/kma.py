"""`python-kma-api` row → :class:`WeatherValue` 변환.

KMA client 자체를 감싸지 않고 입력 Protocol만 정의한다. 이 방식은 원본
`kor-travel-map`의 provider 경계(ADR-006)를 그대로 따르며, Dagster가
`kma.KmaClient`를 직접 생성한다. camelCase raw payload도 payload에 함께
보존해 장애 분석과 재처리를 가능하게 한다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from kortravelweather.models import KST, ForecastStyle, TimelineBucket, WeatherValue

KMA_PROVIDER_NAME = "python-kma-api"

KMA_METRIC_UNITS: dict[str, str] = {
    "T1H": "deg_c",
    "TMP": "deg_c",
    "TMN": "deg_c",
    "TMX": "deg_c",
    "T3H": "deg_c",
    "REH": "%",
    "WSD": "m/s",
    "WSDM": "m/s",
    "VEC": "deg",
    "RN1": "mm",
    "PCP": "mm",
    "SNO": "cm",
    "PTY": "code",
    "SKY": "code",
    "POP": "%",
    "WAV": "m",
    "UUU": "m/s",
    "VVV": "m/s",
    "LGT": "code",
}
KMA_METRIC_NAMES: dict[str, str] = {
    "T1H": "현재 기온",
    "TMP": "기온",
    "TMN": "일 최저기온",
    "TMX": "일 최고기온",
    "T3H": "3시간 기온",
    "REH": "상대습도",
    "WSD": "풍속",
    "WSDM": "평균 풍속",
    "VEC": "풍향",
    "RN1": "1시간 강수량",
    "PCP": "강수량",
    "SNO": "적설",
    "PTY": "강수형태",
    "SKY": "하늘상태",
    "POP": "강수확률",
    "WAV": "파고",
    "UUU": "동서바람성분",
    "VVV": "남북바람성분",
    "LGT": "낙뢰",
}


class KmaForecastLike(Protocol):
    base_date: str
    base_time: str
    fcst_date: str
    fcst_time: str
    nx: int
    ny: int
    category: str
    fcst_value: str


class KmaNowcastLike(Protocol):
    base_date: str
    base_time: str
    nx: int
    ny: int
    category: str
    obsr_value: str


@dataclass(frozen=True, slots=True)
class KmaForecastRow:
    base_date: str
    base_time: str
    fcst_date: str
    fcst_time: str
    nx: int
    ny: int
    category: str
    fcst_value: str

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any] | Any) -> KmaForecastRow:
        source = getattr(raw, "raw", None)
        if isinstance(source, Mapping):
            raw = source

        def read(*names: str) -> Any:
            for name in names:
                if isinstance(raw, Mapping) and name in raw and raw[name] is not None:
                    return raw[name]
                if not isinstance(raw, Mapping) and hasattr(raw, name):
                    value = getattr(raw, name)
                    if value is not None:
                        return value
            raise ValueError(f"KMA forecast row 필수 필드 누락: {names[0]}")

        return cls(
            base_date=str(read("base_date", "baseDate")),
            base_time=str(read("base_time", "baseTime")),
            fcst_date=str(read("fcst_date", "fcstDate")),
            fcst_time=str(read("fcst_time", "fcstTime")),
            nx=int(read("nx")),
            ny=int(read("ny")),
            category=str(read("category")),
            fcst_value=str(read("fcst_value", "fcstValue")),
        )


@dataclass(frozen=True, slots=True)
class KmaNowcastRow:
    base_date: str
    base_time: str
    nx: int
    ny: int
    category: str
    obsr_value: str

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any] | Any) -> KmaNowcastRow:
        source = getattr(raw, "raw", None)
        if isinstance(source, Mapping):
            raw = source

        def read(*names: str) -> Any:
            for name in names:
                if isinstance(raw, Mapping) and name in raw and raw[name] is not None:
                    return raw[name]
                if not isinstance(raw, Mapping) and hasattr(raw, name):
                    value = getattr(raw, name)
                    if value is not None:
                        return value
            raise ValueError(f"KMA nowcast row 필수 필드 누락: {names[0]}")

        return cls(
            base_date=str(read("base_date", "baseDate")),
            base_time=str(read("base_time", "baseTime")),
            nx=int(read("nx")),
            ny=int(read("ny")),
            category=str(read("category")),
            obsr_value=str(read("obsr_value", "obsrValue")),
        )


def _parse_datetime(date_text: str, time_text: str) -> datetime:
    if len(date_text) != 8 or len(time_text) != 4:
        raise ValueError(f"KMA datetime 형식 오류: {date_text!r} {time_text!r}")
    return datetime.strptime(f"{date_text} {time_text}", "%Y%m%d %H%M").replace(tzinfo=KST)


def _value(raw: str, category: str) -> tuple[Decimal | None, str | None]:
    text = str(raw).strip()
    if not text:
        return None, None
    if category in {"RN1", "PCP", "SNO"}:
        if text in {"강수없음", "적설없음", "0", "0.0"}:
            return Decimal("0"), text
        if "미만" in text:
            # KMA의 "1mm 미만"은 지도/PinVi의 numeric 집계에서 0으로
            # 취급해 온 기존 계약이다. 원문 qualifier도 value_text에 남긴다.
            return Decimal("0"), text
    try:
        return Decimal(text), None
    except (InvalidOperation, ValueError):
        return None, text


def _derived_source_key(dataset_key: str, location_id: str, payload: Mapping[str, Any]) -> str:
    """row fixture에서도 재실행마다 같은 source key를 만들도록 한다."""
    canonical = json.dumps(
        [KMA_PROVIDER_NAME, dataset_key, location_id, payload],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return "sr_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:48]


def _forecast_value(
    row: KmaForecastLike | KmaForecastRow,
    *,
    location_id: str,
    dataset_key: str,
    style: ForecastStyle,
    bucket: TimelineBucket | None,
    source_record_key: str | None = None,
    known_at: datetime | None = None,
) -> WeatherValue:
    if row.category not in KMA_METRIC_UNITS:
        raise ValueError(f"지원하지 않는 KMA category: {row.category}")
    issued = _parse_datetime(row.base_date, row.base_time)
    valid = _parse_datetime(row.fcst_date, row.fcst_time)
    number, text = _value(row.fcst_value, row.category)
    payload = {
        "base_date": row.base_date,
        "base_time": row.base_time,
        "fcst_date": row.fcst_date,
        "fcst_time": row.fcst_time,
        "nx": row.nx,
        "ny": row.ny,
        "category": row.category,
        "fcst_value": row.fcst_value,
    }
    source_key = source_record_key or _derived_source_key(dataset_key, location_id, payload)
    return WeatherValue(
        location_id=location_id,
        provider=KMA_PROVIDER_NAME,
        dataset_key=dataset_key,
        weather_domain=dataset_key,
        forecast_style=style,
        timeline_bucket=bucket,
        metric_key=row.category,
        source_metric_key=row.category,
        metric_name=KMA_METRIC_NAMES.get(row.category),
        unit=KMA_METRIC_UNITS.get(row.category),
        issued_at=issued,
        valid_at=valid,
        target_at=valid,
        known_at=known_at or datetime.now(KST),
        value_number=number,
        value_text=text,
        payload=payload,
        source_record_key=source_key,
    )


def _nowcast_value(
    row: KmaNowcastLike | KmaNowcastRow,
    *,
    location_id: str,
    source_record_key: str | None = None,
    known_at: datetime | None = None,
) -> WeatherValue:
    if row.category not in KMA_METRIC_UNITS:
        raise ValueError(f"지원하지 않는 KMA category: {row.category}")
    observed = _parse_datetime(row.base_date, row.base_time)
    number, text = _value(row.obsr_value, row.category)
    payload = {
        "base_date": row.base_date,
        "base_time": row.base_time,
        "nx": row.nx,
        "ny": row.ny,
        "category": row.category,
        "obsr_value": row.obsr_value,
    }
    source_key = source_record_key or _derived_source_key(
        "kma_ultra_short_nowcast", location_id, payload
    )
    return WeatherValue(
        location_id=location_id,
        provider=KMA_PROVIDER_NAME,
        dataset_key="kma_ultra_short_nowcast",
        weather_domain="kma_ultra_short_nowcast",
        forecast_style=ForecastStyle.NOWCAST,
        timeline_bucket=TimelineBucket.ULTRA_SHORT,
        metric_key=row.category,
        source_metric_key=row.category,
        metric_name=KMA_METRIC_NAMES.get(row.category),
        unit=KMA_METRIC_UNITS.get(row.category),
        observed_at=observed,
        target_at=observed,
        known_at=known_at or datetime.now(KST),
        value_number=number,
        value_text=text,
        payload=payload,
        source_record_key=source_key,
    )


def _rows(items: Iterable[Any], *, kind: str) -> list[Any]:
    if kind == "nowcast":
        return [KmaNowcastRow.from_raw(item) for item in items]
    return [KmaForecastRow.from_raw(item) for item in items]


def ultra_short_nowcast_to_weather_values(
    items: Iterable[Any],
    *,
    location_id: str,
    source_record_key: str | None = None,
    known_at: datetime | None = None,
) -> list[WeatherValue]:
    return [
        _nowcast_value(
            row, location_id=location_id, source_record_key=source_record_key, known_at=known_at
        )
        for row in _rows(items, kind="nowcast")
    ]


def ultra_short_forecast_to_weather_values(
    items: Iterable[Any],
    *,
    location_id: str,
    source_record_key: str | None = None,
    known_at: datetime | None = None,
) -> list[WeatherValue]:
    return [
        _forecast_value(
            row,
            location_id=location_id,
            dataset_key="kma_ultra_short_forecast",
            style=ForecastStyle.ULTRA_SHORT,
            bucket=TimelineBucket.ULTRA_SHORT,
            source_record_key=source_record_key,
            known_at=known_at,
        )
        for row in _rows(items, kind="forecast")
    ]


def short_forecast_to_weather_values(
    items: Iterable[Any],
    *,
    location_id: str,
    source_record_key: str | None = None,
    known_at: datetime | None = None,
) -> list[WeatherValue]:
    return [
        _forecast_value(
            row,
            location_id=location_id,
            dataset_key="kma_short_forecast",
            style=ForecastStyle.SHORT,
            bucket=TimelineBucket.SHORT,
            source_record_key=source_record_key,
            known_at=known_at,
        )
        for row in _rows(items, kind="forecast")
    ]


_MID_LAND_PERIODS: tuple[tuple[int, str | None, int, int], ...] = (
    (3, "am", 0, 12),
    (3, "pm", 12, 24),
    (4, "am", 0, 12),
    (4, "pm", 12, 24),
    (5, "am", 0, 12),
    (5, "pm", 12, 24),
    (6, "am", 0, 12),
    (6, "pm", 12, 24),
    (7, "am", 0, 12),
    (7, "pm", 12, 24),
    (8, None, 0, 24),
    (9, None, 0, 24),
    (10, None, 0, 24),
)
_MID_TEMP_DAYS = (3, 4, 5, 6, 7, 8, 9, 10)


def _mapping_for(item: Any) -> Mapping[str, Any]:
    raw = getattr(item, "raw", None)
    if isinstance(raw, Mapping):
        return raw
    if isinstance(item, Mapping):
        return item
    return {}


def _field(item: Any, *names: str) -> Any:
    raw = _mapping_for(item)
    for name in names:
        if name in raw:
            return raw[name]
        if hasattr(item, name):
            return getattr(item, name)
    return None


def _mid_announce(tm_fc: str) -> datetime:
    if len(tm_fc) != 12:
        raise ValueError(f"중기예보 tm_fc 형식 오류: {tm_fc!r} (YYYYMMDDHHMM 필요)")
    return datetime.strptime(tm_fc, "%Y%m%d%H%M").replace(tzinfo=KST)


def _mid_window(
    issued: datetime, day: int, start_hour: int, end_hour: int
) -> tuple[datetime, datetime]:
    midnight = issued.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(days=day, hours=start_hour), midnight + timedelta(
        days=day, hours=end_hour
    )


def _mid_source_key(dataset_key: str, location_id: str, item: Any, explicit: str | None) -> str:
    if explicit:
        return explicit
    raw = _mapping_for(item)
    return _derived_source_key(dataset_key, location_id, raw or {"repr": repr(item)})


def mid_land_forecast_to_weather_values(
    items: Iterable[Any],
    *,
    location_id: str,
    source_record_key: str | None = None,
    known_at: datetime | None = None,
) -> list[WeatherValue]:
    """``getMidLandFcst`` flat ``wf3Am``/``rnSt3Am`` 필드를 day-period로 fan-out."""
    values: list[WeatherValue] = []
    for item in items:
        tm_fc = str(_field(item, "tm_fc", "tmFc") or "")
        issued = _mid_announce(tm_fc)
        source_key = _mid_source_key("kma_mid_forecast", location_id, item, source_record_key)
        for day, period, start, end in _MID_LAND_PERIODS:
            period_suffix = period or ""
            camel_suffix = f"{day}{period.capitalize()}" if period else str(day)
            snake_suffix = f"{day}_{period}" if period else str(day)
            weather = _field(item, f"wf_{snake_suffix}", f"wf{camel_suffix}")
            pop = _field(item, f"rn_st_{snake_suffix}", f"rnSt{camel_suffix}")
            valid_from, valid_until = _mid_window(issued, day, start, end)
            payload = {
                "reg_id": _field(item, "reg_id", "regId"),
                "tm_fc": tm_fc,
                "day_offset": day,
                "period": period,
                "wf": weather,
                "rn_st": pop,
            }
            if weather is not None and str(weather).strip():
                values.append(
                    WeatherValue(
                        location_id=location_id,
                        provider=KMA_PROVIDER_NAME,
                        dataset_key="kma_mid_forecast",
                        weather_domain="kma_mid_forecast",
                        forecast_style=ForecastStyle.MID,
                        timeline_bucket=TimelineBucket.MID,
                        metric_key="SKY",
                        metric_name=KMA_METRIC_NAMES["SKY"],
                        source_metric_key=f"wf{camel_suffix}",
                        issued_at=issued,
                        valid_at=valid_from,
                        target_at=valid_from,
                        valid_from=valid_from,
                        valid_until=valid_until,
                        value_text=str(weather).strip(),
                        payload=payload,
                        known_at=known_at or datetime.now(KST),
                        source_record_key=source_key,
                    )
                )
            if pop is not None and str(pop).strip() != "":
                number, text = _value(str(pop), "POP")
                values.append(
                    WeatherValue(
                        location_id=location_id,
                        provider=KMA_PROVIDER_NAME,
                        dataset_key="kma_mid_forecast",
                        weather_domain="kma_mid_forecast",
                        forecast_style=ForecastStyle.MID,
                        timeline_bucket=TimelineBucket.MID,
                        metric_key="POP",
                        metric_name=KMA_METRIC_NAMES["POP"],
                        unit="%",
                        source_metric_key=f"rnSt{camel_suffix}",
                        issued_at=issued,
                        valid_at=valid_from,
                        target_at=valid_from,
                        valid_from=valid_from,
                        valid_until=valid_until,
                        value_number=number,
                        value_text=text,
                        payload=payload,
                        known_at=known_at or datetime.now(KST),
                        source_record_key=source_key,
                    )
                )
    return values


def mid_temperature_to_weather_values(
    items: Iterable[Any],
    *,
    location_id: str,
    source_record_key: str | None = None,
    known_at: datetime | None = None,
) -> list[WeatherValue]:
    """``getMidTa``의 ``taMin3``/``taMax3``를 일별 TMN/TMX로 fan-out."""
    values: list[WeatherValue] = []
    for item in items:
        tm_fc = str(_field(item, "tm_fc", "tmFc") or "")
        issued = _mid_announce(tm_fc)
        source_key = _mid_source_key("kma_mid_forecast", location_id, item, source_record_key)
        for day in _MID_TEMP_DAYS:
            valid_from, valid_until = _mid_window(issued, day, 0, 24)
            for metric, camel, snake in (
                ("TMN", f"taMin{day}", f"ta_min_{day}"),
                ("TMX", f"taMax{day}", f"ta_max_{day}"),
            ):
                raw_value = _field(item, snake, camel)
                if raw_value is None or str(raw_value).strip() == "":
                    continue
                number, text = _value(str(raw_value), metric)
                values.append(
                    WeatherValue(
                        location_id=location_id,
                        provider=KMA_PROVIDER_NAME,
                        dataset_key="kma_mid_forecast",
                        weather_domain="kma_mid_forecast",
                        forecast_style=ForecastStyle.MID,
                        timeline_bucket=TimelineBucket.MID,
                        metric_key=metric,
                        metric_name=KMA_METRIC_NAMES[metric],
                        unit="deg_c",
                        source_metric_key=camel,
                        issued_at=issued,
                        valid_at=valid_from,
                        target_at=valid_from,
                        valid_from=valid_from,
                        valid_until=valid_until,
                        value_number=number,
                        value_text=text,
                        payload={
                            "reg_id": _field(item, "reg_id", "regId"),
                            "tm_fc": tm_fc,
                            "day_offset": day,
                            "source_metric": camel,
                            "raw_value": raw_value,
                        },
                        known_at=known_at or datetime.now(KST),
                        source_record_key=source_key,
                    )
                )
    return values


def mid_forecast_to_weather_values(
    items: Iterable[Any],
    *,
    location_id: str,
    source_record_key: str | None = None,
    known_at: datetime | None = None,
) -> list[WeatherValue]:
    """중기육상/기온 typed rows를 자동 식별해 정규화한다."""
    materialized = list(items)
    if not materialized:
        return []
    first = materialized[0]
    if any(_field(first, f"wf_{day}_am", f"wf{day}Am") is not None for day in range(3, 8)):
        return mid_land_forecast_to_weather_values(
            materialized,
            location_id=location_id,
            source_record_key=source_record_key,
            known_at=known_at,
        )
    return mid_temperature_to_weather_values(
        materialized,
        location_id=location_id,
        source_record_key=source_record_key,
        known_at=known_at,
    )
