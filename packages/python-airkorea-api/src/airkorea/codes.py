"""AirKorea 코드 라벨, enum, 파라미터 검증 헬퍼."""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum, IntEnum
from typing import Any


class _AirKoreaStrEnum(str, Enum):
    """기존 문자열 기반 코드와 자연스럽게 섞이는 문자열 enum."""

    def __str__(self) -> str:
        return str(self.value)


class AirQualityGrade(IntEnum):
    """AirKorea 대기질 등급 코드."""

    GOOD = 1
    MODERATE = 2
    BAD = 3
    VERY_BAD = 4

    @property
    def label(self) -> str:
        return GRADE_LABELS[int(self)]

    @classmethod
    def from_code(cls, value: Any) -> AirQualityGrade | None:
        if isinstance(value, int):
            try:
                return cls(value)
            except ValueError:
                return None
        try:
            return cls(int(str(value).strip()))
        except (TypeError, ValueError):
            return None


class DataTerm(_AirKoreaStrEnum):
    """측정소 측정정보 API가 받는 측정 이력 기간."""

    DAILY = "DAILY"
    MONTH = "MONTH"
    THREE_MONTH = "3MONTH"


class InformCode(_AirKoreaStrEnum):
    """예보 통보문 오염물질 코드."""

    PM10 = "PM10"
    PM25 = "PM25"
    O3 = "O3"


class Pollutant(_AirKoreaStrEnum):
    """AirKorea 오염물질/item 코드."""

    SO2 = "SO2"
    CO = "CO"
    O3 = "O3"
    NO2 = "NO2"
    PM10 = "PM10"
    PM25 = "PM25"
    KHAI = "KHAI"


class SidoName(_AirKoreaStrEnum):
    """AirKorea API가 받는 시도명."""

    SEOUL = "서울"
    BUSAN = "부산"
    DAEGU = "대구"
    INCHEON = "인천"
    GWANGJU = "광주"
    DAEJEON = "대전"
    ULSAN = "울산"
    GYEONGGI = "경기"
    GANGWON = "강원"
    CHUNGBUK = "충북"
    CHUNGNAM = "충남"
    JEONBUK = "전북"
    JEONNAM = "전남"
    GYEONGBUK = "경북"
    GYEONGNAM = "경남"
    JEJU = "제주"
    SEJONG = "세종"


class StatsDataGubun(_AirKoreaStrEnum):
    """통계 집계 단위."""

    HOUR = "HOUR"
    DAILY = "DAILY"
    MONTH = "MONTH"


class StatsSearchCondition(_AirKoreaStrEnum):
    """통계 조회 기간/조건."""

    HOUR = "HOUR"
    DAILY = "DAILY"
    WEEK = "WEEK"
    MONTH = "MONTH"


GRADE_LABELS = {
    1: "좋음",
    2: "보통",
    3: "나쁨",
    4: "매우나쁨",
}

INFORM_CODES = {item.value for item in InformCode}
POLLUTANT_CODES = {item.value for item in Pollutant}
DATA_TERMS = {item.value for item in DataTerm}
SIDO_NAMES = {item.value for item in SidoName}
STATS_DATA_GUBUNS = {item.value for item in StatsDataGubun}
STATS_SEARCH_CONDITIONS = {item.value for item in StatsSearchCondition}

POLLUTANT_UNITS = {
    "so2": "ppm",
    "co": "ppm",
    "o3": "ppm",
    "no2": "ppm",
    "pm10": "ug/m3",
    "pm25": "ug/m3",
    "khai": "index",
}

_POLLUTANT_ALIASES = {
    "PM2.5": "PM25",
    "PM2_5": "PM25",
    "PM-2.5": "PM25",
}


def grade_label(value: Any) -> str | None:
    """AirKorea 등급 코드 1-4에 대한 한글 라벨을 반환합니다."""

    grade = AirQualityGrade.from_code(value)
    return grade.label if grade is not None else None


def normalize_data_term(value: str | DataTerm) -> str:
    term = _enum_or_text(value).upper()
    if term not in DATA_TERMS:
        raise ValueError(f"data_term must be one of {sorted(DATA_TERMS)}")
    return term


def normalize_inform_code(value: str | InformCode | Pollutant | None) -> str | None:
    if value is None:
        return None
    code = normalize_pollutant_code(value)
    if code not in INFORM_CODES:
        raise ValueError(f"inform_code must be one of {sorted(INFORM_CODES)}")
    return code


def normalize_pollutant_code(
    value: str | Pollutant | InformCode | None,
    *,
    allowed: Iterable[str | Pollutant | InformCode] | None = None,
) -> str | None:
    if value is None:
        return None

    code = _normalize_pollutant_text(_enum_or_text(value))
    if code not in POLLUTANT_CODES:
        raise ValueError(f"pollutant code must be one of {sorted(POLLUTANT_CODES)}")

    if allowed is not None:
        allowed_codes = {
            _normalize_pollutant_text(_enum_or_text(item))
            for item in allowed
        }
        if code not in allowed_codes:
            raise ValueError(f"pollutant code must be one of {sorted(allowed_codes)}")
    return code


def normalize_stats_data_gubun(value: str | StatsDataGubun) -> str:
    code = _enum_or_text(value).upper()
    if code not in STATS_DATA_GUBUNS:
        raise ValueError(f"data_gubun must be one of {sorted(STATS_DATA_GUBUNS)}")
    return code


def normalize_stats_search_condition(value: str | StatsSearchCondition) -> str:
    code = _enum_or_text(value).upper()
    if code not in STATS_SEARCH_CONDITIONS:
        raise ValueError(
            f"search_condition must be one of {sorted(STATS_SEARCH_CONDITIONS)}"
        )
    return code


def validate_sido_name(value: str | SidoName) -> str:
    name = _enum_or_text(value)
    if name not in SIDO_NAMES:
        raise ValueError(f"sido_name must be one of {sorted(SIDO_NAMES)}")
    return name


def _normalize_pollutant_text(value: str) -> str:
    code = value.strip().upper()
    return _POLLUTANT_ALIASES.get(code, code)


def _enum_or_text(value: str | Enum) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value).strip()
