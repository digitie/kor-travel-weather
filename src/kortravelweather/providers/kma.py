"""`python-kma-api` row → :class:`WeatherValue` 변환.

KMA client 자체를 감싸지 않고 입력 Protocol만 정의한다. 이 방식은 원본
`kor-travel-map`의 provider 경계(ADR-006)를 그대로 따르며, Dagster가
`kma.KmaClient`를 직접 생성한다. camelCase raw payload도 payload에 함께
보존해 장애 분석과 재처리를 가능하게 한다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from kortravelweather.models import ForecastStyle, KST, TimelineBucket, WeatherValue

KMA_PROVIDER_NAME = "python-kma-api"

KMA_METRIC_UNITS: dict[str, str] = {
    "T1H": "deg_c", "TMP": "deg_c", "TMN": "deg_c", "TMX": "deg_c", "T3H": "deg_c",
    "REH": "%", "WSD": "m/s", "WSDM": "m/s", "VEC": "deg", "RN1": "mm", "PCP": "mm",
    "SNO": "cm", "PTY": "code", "SKY": "code", "POP": "%", "WAV": "m", "UUU": "m/s",
    "VVV": "m/s", "LGT": "code",
}
KMA_METRIC_NAMES: dict[str, str] = {
    "T1H": "현재 기온", "TMP": "기온", "TMN": "일 최저기온", "TMX": "일 최고기온",
    "T3H": "3시간 기온", "REH": "상대습도", "WSD": "풍속", "WSDM": "평균 풍속",
    "VEC": "풍향", "RN1": "1시간 강수량", "PCP": "강수량", "SNO": "적설",
    "PTY": "강수형태", "SKY": "하늘상태", "POP": "강수확률", "WAV": "파고",
    "UUU": "동서바람성분", "VVV": "남북바람성분", "LGT": "낙뢰",
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
        get = raw.get if isinstance(raw, Mapping) else lambda key: getattr(raw, key)
        # typed model의 raw payload가 있으면 KMA 원문 key를 우선한다.
        source = getattr(raw, "raw", None)
        if isinstance(source, Mapping):
            get = source.get
            return cls(
                base_date=str(get("baseDate")), base_time=str(get("baseTime")),
                fcst_date=str(get("fcstDate")), fcst_time=str(get("fcstTime")),
                nx=int(get("nx")), ny=int(get("ny")), category=str(get("category")),
                fcst_value=str(get("fcstValue")),
            )
        return cls(
            base_date=str(get("base_date")), base_time=str(get("base_time")),
            fcst_date=str(get("fcst_date")), fcst_time=str(get("fcst_time")),
            nx=int(get("nx")), ny=int(get("ny")), category=str(get("category")),
            fcst_value=str(get("fcst_value")),
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
        get = raw.get if isinstance(raw, Mapping) else lambda key: getattr(raw, key)
        if isinstance(raw, Mapping) and "baseDate" in raw:
            return cls(
                base_date=str(get("baseDate")), base_time=str(get("baseTime")),
                nx=int(get("nx")), ny=int(get("ny")), category=str(get("category")),
                obsr_value=str(get("obsrValue")),
            )
        return cls(
            base_date=str(get("base_date")), base_time=str(get("base_time")),
            nx=int(get("nx")), ny=int(get("ny")), category=str(get("category")),
            obsr_value=str(get("obsr_value")),
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
            return Decimal("0"), text
    try:
        return Decimal(text), None
    except (InvalidOperation, ValueError):
        return None, text


def _forecast_value(
    row: KmaForecastLike | KmaForecastRow,
    *,
    location_id: str,
    dataset_key: str,
    style: ForecastStyle,
    bucket: TimelineBucket | None,
) -> WeatherValue:
    issued = _parse_datetime(row.base_date, row.base_time)
    valid = _parse_datetime(row.fcst_date, row.fcst_time)
    number, text = _value(row.fcst_value, row.category)
    return WeatherValue(
        location_id=location_id, provider=KMA_PROVIDER_NAME, dataset_key=dataset_key,
        weather_domain=dataset_key, forecast_style=style, timeline_bucket=bucket,
        metric_key=row.category, source_metric_key=row.category,
        metric_name=KMA_METRIC_NAMES.get(row.category), unit=KMA_METRIC_UNITS.get(row.category),
        issued_at=issued, valid_at=valid, value_number=number, value_text=text,
        payload={
            "base_date": row.base_date, "base_time": row.base_time,
            "fcst_date": row.fcst_date, "fcst_time": row.fcst_time,
            "nx": row.nx, "ny": row.ny, "category": row.category, "fcst_value": row.fcst_value,
        },
    )


def _nowcast_value(
    row: KmaNowcastLike | KmaNowcastRow, *, location_id: str
) -> WeatherValue:
    observed = _parse_datetime(row.base_date, row.base_time)
    number, text = _value(row.obsr_value, row.category)
    return WeatherValue(
        location_id=location_id, provider=KMA_PROVIDER_NAME,
        dataset_key="kma_ultra_short_nowcast", weather_domain="kma_ultra_short_nowcast",
        forecast_style=ForecastStyle.NOWCAST, timeline_bucket=TimelineBucket.ULTRA_SHORT,
        metric_key=row.category, source_metric_key=row.category,
        metric_name=KMA_METRIC_NAMES.get(row.category), unit=KMA_METRIC_UNITS.get(row.category),
        observed_at=observed, value_number=number, value_text=text,
        payload={
            "base_date": row.base_date, "base_time": row.base_time,
            "nx": row.nx, "ny": row.ny, "category": row.category, "obsr_value": row.obsr_value,
        },
    )


def _rows(items: Iterable[Any], *, kind: str) -> list[Any]:
    if kind == "nowcast":
        return [KmaNowcastRow.from_raw(item) for item in items]
    return [KmaForecastRow.from_raw(item) for item in items]


def ultra_short_nowcast_to_weather_values(
    items: Iterable[Any], *, location_id: str
) -> list[WeatherValue]:
    return [_nowcast_value(row, location_id=location_id) for row in _rows(items, kind="nowcast")]


def ultra_short_forecast_to_weather_values(
    items: Iterable[Any], *, location_id: str
) -> list[WeatherValue]:
    return [
        _forecast_value(
            row, location_id=location_id, dataset_key="kma_ultra_short_forecast",
            style=ForecastStyle.ULTRA_SHORT, bucket=TimelineBucket.ULTRA_SHORT,
        )
        for row in _rows(items, kind="forecast")
    ]


def short_forecast_to_weather_values(
    items: Iterable[Any], *, location_id: str
) -> list[WeatherValue]:
    return [
        _forecast_value(
            row, location_id=location_id, dataset_key="kma_short_forecast",
            style=ForecastStyle.SHORT, bucket=TimelineBucket.SHORT,
        )
        for row in _rows(items, kind="forecast")
    ]


def mid_forecast_to_weather_values(
    items: Iterable[Any], *, location_id: str
) -> list[WeatherValue]:
    """중기 typed item의 공통 ``fcst_value``/``forecast_value`` shape을 보존한다."""
    values: list[WeatherValue] = []
    for item in items:
        raw = getattr(item, "raw", None)
        source: Mapping[str, Any] = raw if isinstance(raw, Mapping) else item
        metric = str(source.get("category") or source.get("metric_key") or "forecast")
        value = str(source.get("fcstValue") or source.get("value") or source.get("forecast_value") or "")
        base_date = str(source.get("baseDate") or source.get("tmFc") or source.get("base_date"))
        base_time = str(source.get("baseTime") or source.get("base_time") or "0000").zfill(4)
        valid_date = str(source.get("fcstDate") or source.get("date") or base_date)
        valid_time = str(source.get("fcstTime") or source.get("time") or "0000").zfill(4)
        values.append(
            _forecast_value(
                KmaForecastRow(base_date=base_date[:8], base_time=base_time[-4:],
                               fcst_date=valid_date[:8], fcst_time=valid_time[-4:], nx=0, ny=0,
                               category=metric, fcst_value=value),
                location_id=location_id, dataset_key="kma_mid_forecast",
                style=ForecastStyle.MID, bucket=TimelineBucket.MID,
            )
        )
    return values
