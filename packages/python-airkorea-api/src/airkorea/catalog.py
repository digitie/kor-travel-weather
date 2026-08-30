"""AirKorea 지원 API 카탈로그."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from airkorea.codes import (
    DataTerm,
    InformCode,
    Pollutant,
    SidoName,
    StatsDataGubun,
    StatsSearchCondition,
)


@dataclass(frozen=True)
class ParamSpec:
    """디버그 UI가 위젯 하나를 자동 생성하기 위해 쓰는 요청 파라미터 명세.

    ``kind``는 ``"text"``, ``"int"``, ``"float"``, ``"select"`` 중 하나이며,
    ``select``는 ``options``(대개 ``codes.py``의 Enum 값)를 selectbox 선택지로 씁니다.
    """

    name: str
    label: str
    kind: str = "text"
    required: bool = False
    default: Any = None
    help: str | None = None
    options: tuple[str, ...] = ()
    min_value: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Streamlit 위젯 렌더링에 바로 쓸 수 있는 dict를 반환합니다."""

        return {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "required": self.required,
            "default": self.default,
            "help": self.help,
            "options": list(self.options),
            "min_value": self.min_value,
        }


@dataclass(frozen=True)
class ApiCatalogEntry:
    """지원하는 AirKorea endpoint 하나에 대한 사람이 읽기 쉬운 카탈로그 항목."""

    dataset_name: str
    service_label: str
    service_name: str
    base_url: str
    endpoint: str
    method_names: tuple[str, ...]
    portal_url: str
    service_key_url: str
    service_key_param: str = "serviceKey"
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()

    @property
    def url(self) -> str:
        """Base URL과 endpoint를 합친 호출 URL을 반환합니다."""

        return f"{self.base_url.rstrip('/')}/{self.endpoint.lstrip('/')}"

    def to_dict(self) -> dict[str, Any]:
        """Streamlit dataframe/json 표시에 바로 쓸 수 있는 dict를 반환합니다."""

        return {
            "dataset_name": self.dataset_name,
            "service_label": self.service_label,
            "service_name": self.service_name,
            "endpoint": self.endpoint,
            "method_names": list(self.method_names),
            "service_key_param": self.service_key_param,
            "base_url": self.base_url,
            "url": self.url,
            "portal_url": self.portal_url,
            "service_key_url": self.service_key_url,
            "required_params": list(self.required_params),
            "optional_params": list(self.optional_params),
        }


_SERVICE_INFO: dict[str, dict[str, str]] = {
    "ArpltnInforInqireSvc": {
        "dataset_name": "한국환경공단_에어코리아_대기오염정보",
        "service_label": "대기오염정보",
        "base_url": "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc",
        "portal_url": "https://www.data.go.kr/data/15073861/openapi.do",
        "service_key_param": "serviceKey",
    },
    "MsrstnInfoInqireSvc": {
        "dataset_name": "한국환경공단_에어코리아_측정소정보",
        "service_label": "측정소정보",
        "base_url": "https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc",
        "portal_url": "https://www.data.go.kr/data/15073877/openapi.do",
        "service_key_param": "serviceKey",
    },
    "ArpltnStatsSvc": {
        "dataset_name": "한국환경공단_에어코리아_대기오염통계 현황",
        "service_label": "대기오염통계 현황",
        "base_url": "https://apis.data.go.kr/B552584/ArpltnStatsSvc",
        "portal_url": "https://www.data.go.kr/data/15073855/openapi.do",
        "service_key_param": "serviceKey",
    },
    "OzYlwsndOccrrncInforInqireSvc": {
        "dataset_name": "한국환경공단_에어코리아_오존황사 발생정보",
        "service_label": "오존황사 발생정보",
        "base_url": "https://apis.data.go.kr/B552584/OzYlwsndOccrrncInforInqireSvc",
        "portal_url": "https://www.data.go.kr/data/15073872/openapi.do",
        "service_key_param": "serviceKey",
    },
    "UlfptcaAlarmInqireSvc": {
        "dataset_name": "한국환경공단_에어코리아_미세먼지 경보 발령 현황",
        "service_label": "미세먼지 경보 발령 현황",
        "base_url": "https://apis.data.go.kr/B552584/UlfptcaAlarmInqireSvc",
        "portal_url": "https://www.data.go.kr/data/15073885/openapi.do",
        "service_key_param": "serviceKey",
    },
    "UserSportSvc": {
        "dataset_name": "한국환경공단_에어코리아_사용자 지원",
        "service_label": "사용자 지원",
        "base_url": "https://apis.data.go.kr/B552584/UserSportSvc",
        "portal_url": "https://www.data.go.kr/data/15075624/openapi.do",
        "service_key_param": "ServiceKey",
    },
    "MinuDustFrcstDspthSvc": {
        "dataset_name": "한국환경공단_에어코리아_고농도 초미세먼지(50초과) 예보 정보",
        "service_label": "고농도 초미세먼지(50초과) 예보 정보",
        "base_url": "https://apis.data.go.kr/B552584/MinuDustFrcstDspthSvc",
        "portal_url": "https://www.data.go.kr/data/15112307/openapi.do",
        "service_key_param": "serviceKey",
    },
    "RltmKhaiInfoSvc": {
        "dataset_name": "한국환경공단_에어코리아_통합대기환경지수(CAI) 조회 서비스",
        "service_label": "통합대기환경지수(CAI) 조회 서비스",
        "base_url": "https://apis.data.go.kr/B552584/RltmKhaiInfoSvc",
        "portal_url": "https://www.data.go.kr/data/15140253/openapi.do",
        "service_key_param": "serviceKey",
    },
    "arpltsNtnBkgrDnstSyrdt": {
        "dataset_name": "한국환경공단_대기오염물질 국가배경농도 합성데이터 조회 서비스",
        "service_label": "국가배경농도 합성데이터 조회 서비스",
        "base_url": "https://apis.data.go.kr/B552584/arpltsNtnBkgrDnstSyrdt",
        "portal_url": "https://www.data.go.kr/data/15153593/openapi.do",
        "service_key_param": "serviceKey",
    },
    "atmstMsrstnRltmInfo": {
        "dataset_name": "한국환경공단_에어코리아 측정소별 실시간 측정정보 영문 조회 서비스",
        "service_label": "측정소별 실시간 측정정보 영문 조회 서비스",
        "base_url": "https://apis.data.go.kr/B552584/atmstMsrstnRltmInfo",
        "portal_url": "https://www.data.go.kr/data/15156659/openapi.do",
        "service_key_param": "serviceKey",
    },
    "atmstMsrstnInfoEngNm": {
        "dataset_name": "한국환경공단_에어코리아_측정소 영문 정보 조회 서비스",
        "service_label": "측정소 영문 정보 조회 서비스",
        "base_url": "https://apis.data.go.kr/B552584/atmstMsrstnInfoEngNm",
        "portal_url": "https://www.data.go.kr/data/15156708/openapi.do",
        "service_key_param": "serviceKey",
    },
}

def _text(
    name: str,
    *,
    required: bool = False,
    default: str = "",
    help_text: str | None = None,
) -> ParamSpec:
    return ParamSpec(
        name=name,
        label=name,
        kind="text",
        required=required,
        default=default,
        help=help_text,
    )


def _int(
    name: str,
    *,
    default: int,
    min_value: int = 1,
    help_text: str | None = None,
) -> ParamSpec:
    return ParamSpec(
        name=name,
        label=name,
        kind="int",
        required=False,
        default=default,
        help=help_text,
        min_value=min_value,
    )


def _float(name: str, *, required: bool, default: float) -> ParamSpec:
    return ParamSpec(name=name, label=name, kind="float", required=required, default=default)


def _select(
    name: str,
    options: Iterable[str],
    *,
    required: bool = False,
    default: str = "",
    help_text: str | None = None,
) -> ParamSpec:
    return ParamSpec(
        name=name,
        label=name,
        kind="select",
        required=required,
        default=default,
        options=tuple(options),
        help=help_text,
    )


def _enum_values(enum_cls: type[Enum], *, exclude: Iterable[Enum] = ()) -> tuple[str, ...]:
    excluded = set(exclude)
    return tuple(str(member.value) for member in enum_cls if member not in excluded)


def _optional_options(values: Iterable[str]) -> tuple[str, ...]:
    return ("", *values)


_COMMON_PAGE_PARAMS = (
    _int("page_no", default=1, help_text="AirKorea pageNo"),
    _int("num_of_rows", default=5, help_text="AirKorea numOfRows"),
)
_SIDO_OPTIONS = _enum_values(SidoName)
_DATA_TERM_OPTIONS = _enum_values(DataTerm)
_STATS_DATA_GUBUN_OPTIONS = _enum_values(StatsDataGubun)
_STATS_SEARCH_CONDITION_OPTIONS = _enum_values(StatsSearchCondition)
_STATS_POLLUTANT_OPTIONS = _enum_values(Pollutant, exclude=(Pollutant.KHAI,))
_ALARM_POLLUTANT_OPTIONS = (Pollutant.PM10.value, Pollutant.PM25.value)

# public method 이름별 요청 파라미터 명세. Streamlit 디버그 UI가 이 테이블에서
# 위젯을 자동 생성하므로, 함수 이름을 분기하는 하드코딩된 폼 코드가 필요 없습니다.
_METHOD_PARAM_SPECS: dict[str, tuple[ParamSpec, ...]] = {
    "station_measurements": (
        _text("station_name", required=True, default="종로구"),
        _select("data_term", _DATA_TERM_OPTIONS, default=DataTerm.DAILY.value),
        *_COMMON_PAGE_PARAMS,
        _text("ver", default="1.3"),
    ),
    "latest_station_measurement": (
        _text("station_name", required=True, default="종로구"),
        _select("data_term", _DATA_TERM_OPTIONS, default=DataTerm.DAILY.value),
        _text("ver", default="1.3"),
    ),
    "sido_measurements": (
        _select("sido_name", _SIDO_OPTIONS, required=True, default=SidoName.SEOUL.value),
        *_COMMON_PAGE_PARAMS,
        _text("ver", default="1.3"),
    ),
    "unhealthy_stations": _COMMON_PAGE_PARAMS,
    "forecast_notices": (
        _text("search_date", help_text="YYYY-MM-DD"),
        _select("inform_code", _optional_options(_enum_values(InformCode))),
        *_COMMON_PAGE_PARAMS,
    ),
    "weekly_forecasts": (
        _text("search_date", help_text="YYYY-MM-DD"),
        *_COMMON_PAGE_PARAMS,
    ),
    "stations": (
        _text("addr", default="서울"),
        _text("station_name"),
        *_COMMON_PAGE_PARAMS,
    ),
    "nearby_stations": (
        _float("lat", required=True, default=37.5665),
        _float("lon", required=True, default=126.9780),
        _text("ver"),
    ),
    "tm_coordinates": (
        _text("umd_name", required=True, default="혜화동"),
        *_COMMON_PAGE_PARAMS,
    ),
    "measurement_near": (
        _float("lat", required=True, default=37.5665),
        _float("lon", required=True, default=126.9780),
        _select("data_term", _DATA_TERM_OPTIONS, default=DataTerm.DAILY.value),
        _text("ver", default="1.3"),
    ),
    "sido_average_stats": (
        _select(
            "item_code",
            _STATS_POLLUTANT_OPTIONS,
            required=True,
            default=Pollutant.PM25.value,
        ),
        _select("data_gubun", _STATS_DATA_GUBUN_OPTIONS, default=StatsDataGubun.HOUR.value),
        _select(
            "search_condition",
            _STATS_SEARCH_CONDITION_OPTIONS,
            default=StatsSearchCondition.WEEK.value,
        ),
        *_COMMON_PAGE_PARAMS,
    ),
    "city_average_stats": (
        _select("sido_name", _SIDO_OPTIONS, required=True, default=SidoName.SEOUL.value),
        _select("item_code", _optional_options(_STATS_POLLUTANT_OPTIONS)),
        _select("data_gubun", _optional_options(_STATS_DATA_GUBUN_OPTIONS)),
        _select(
            "search_condition",
            _STATS_SEARCH_CONDITION_OPTIONS,
            default=StatsSearchCondition.HOUR.value,
        ),
        *_COMMON_PAGE_PARAMS,
    ),
    "station_daily_stats": (
        _text("inquiry_begin_date", required=True, default="2026-04-01", help_text="YYYY-MM-DD"),
        _text("inquiry_end_date", required=True, default="2026-04-30", help_text="YYYY-MM-DD"),
        _text("station_name"),
        *_COMMON_PAGE_PARAMS,
    ),
    "station_monthly_stats": (
        _text("inquiry_begin_date", required=True, default="2026-04-01", help_text="YYYY-MM-DD"),
        _text("inquiry_end_date", required=True, default="2026-04-30", help_text="YYYY-MM-DD"),
        _text("station_name"),
        *_COMMON_PAGE_PARAMS,
    ),
    "ozone_advisories": (
        _text("year", default="2026"),
        *_COMMON_PAGE_PARAMS,
    ),
    "yellow_dust_advisories": (
        _text("year", default="2026"),
        *_COMMON_PAGE_PARAMS,
    ),
    "dust_alarms": (
        _text("year", required=True, default="2026"),
        _select("item_code", _optional_options(_ALARM_POLLUTANT_OPTIONS)),
        *_COMMON_PAGE_PARAMS,
    ),
    "traffic_stats": (
        _text("search_date", required=True, default="2026-04-30", help_text="YYYY-MM-DD"),
        *_COMMON_PAGE_PARAMS,
    ),
    "high_pm25_forecasts": (
        _text("search_date", required=True, default="2026-04-30", help_text="YYYY-MM-DD"),
        *_COMMON_PAGE_PARAMS,
    ),
    "cai_measurements": (
        _text("station_name"),
        *_COMMON_PAGE_PARAMS,
    ),
    "background_concentrations": (
        _text("measurement_date", required=True, default="2022-01-03", help_text="YYYY-MM-DD"),
        _text("station_name", required=True, default="s0002"),
        *_COMMON_PAGE_PARAMS,
    ),
    "english_measurements": (
        _text("station_name"),
        *_COMMON_PAGE_PARAMS,
    ),
    "english_stations": (
        _text("station_name"),
        _text("road_address"),
        *_COMMON_PAGE_PARAMS,
    ),
}


def _param_names(specs: tuple[ParamSpec, ...], *, required: bool) -> tuple[str, ...]:
    return tuple(spec.name for spec in specs if spec.required is required)


def _required_optional_for_methods(
    method_names: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """method 이름들이 공유하는 endpoint 하나에 대한 필수/선택 파라미터 이름 합집합을 반환합니다."""

    required: set[str] = set()
    optional: set[str] = set()
    for method_name in method_names:
        specs = _METHOD_PARAM_SPECS.get(method_name, ())
        required.update(_param_names(specs, required=True))
        optional.update(_param_names(specs, required=False))
    optional -= required
    return tuple(sorted(required)), tuple(sorted(optional))


def api_catalog_params_for_method(method_name: str) -> tuple[ParamSpec, ...]:
    """public method 이름에 대한 요청 파라미터 명세를 반환합니다."""

    return _METHOD_PARAM_SPECS.get(method_name, ())


_ENDPOINT_METHODS: dict[tuple[str, str], tuple[str, ...]] = {
    ("ArpltnInforInqireSvc", "getMsrstnAcctoRltmMesureDnsty"): (
        "station_measurements",
        "latest_station_measurement",
        "measurement_near",
    ),
    ("ArpltnInforInqireSvc", "getUnityAirEnvrnIdexSnstiveAboveMsrstnList"): (
        "unhealthy_stations",
    ),
    ("ArpltnInforInqireSvc", "getCtprvnRltmMesureDnsty"): ("sido_measurements",),
    ("ArpltnInforInqireSvc", "getMinuDustFrcstDspth"): ("forecast_notices",),
    ("ArpltnInforInqireSvc", "getMinuDustWeekFrcstDspth"): ("weekly_forecasts",),
    ("MsrstnInfoInqireSvc", "getMsrstnList"): ("stations",),
    ("MsrstnInfoInqireSvc", "getNearbyMsrstnList"): ("nearby_stations", "measurement_near"),
    ("MsrstnInfoInqireSvc", "getTMStdrCrdnt"): ("tm_coordinates",),
    ("ArpltnStatsSvc", "getCtprvnMesureLIst"): ("sido_average_stats",),
    ("ArpltnStatsSvc", "getCtprvnMesureSidoLIst"): ("city_average_stats",),
    ("ArpltnStatsSvc", "getMsrstnAcctoRDyrg"): ("station_daily_stats",),
    ("ArpltnStatsSvc", "getMsrstnAcctoRMmrg"): ("station_monthly_stats",),
    ("OzYlwsndOccrrncInforInqireSvc", "getOzAdvsryOccrrncInfo"): ("ozone_advisories",),
    ("OzYlwsndOccrrncInforInqireSvc", "getYlwsndAdvsryOccrrncInfo"): (
        "yellow_dust_advisories",
    ),
    ("UlfptcaAlarmInqireSvc", "getUlfptcaAlarmInfo"): ("dust_alarms",),
    ("UserSportSvc", "getSvckeyDalyStats"): ("traffic_stats",),
    ("MinuDustFrcstDspthSvc", "getMinuDustFrcstDspth50Over"): (
        "high_pm25_forecasts",
    ),
    ("RltmKhaiInfoSvc", "getMsrstnKhaiRltmDnsty"): ("cai_measurements",),
    ("arpltsNtnBkgrDnstSyrdt", "getList"): ("background_concentrations",),
    ("atmstMsrstnRltmInfo", "getList"): ("english_measurements",),
    ("atmstMsrstnInfoEngNm", "getList"): ("english_stations",),
}

def _build_catalog_entry(
    service_name: str,
    endpoint: str,
    method_names: tuple[str, ...],
) -> ApiCatalogEntry:
    required_params, optional_params = _required_optional_for_methods(method_names)
    info = _SERVICE_INFO[service_name]
    return ApiCatalogEntry(
        dataset_name=info["dataset_name"],
        service_label=info["service_label"],
        service_name=service_name,
        base_url=info["base_url"],
        endpoint=endpoint,
        method_names=method_names,
        portal_url=info["portal_url"],
        service_key_url=info["portal_url"],
        service_key_param=info["service_key_param"],
        required_params=required_params,
        optional_params=optional_params,
    )


_CATALOG = tuple(
    _build_catalog_entry(service_name, endpoint, method_names)
    for (service_name, endpoint), method_names in _ENDPOINT_METHODS.items()
)

_CATALOG_BY_ENDPOINT = {
    (entry.service_name, entry.endpoint): entry for entry in _CATALOG
}


def api_catalog() -> tuple[ApiCatalogEntry, ...]:
    """지원 endpoint 전체 카탈로그를 반환합니다."""

    return _CATALOG


def api_catalog_dicts() -> list[dict[str, Any]]:
    """Streamlit dataframe 등에 바로 넘길 수 있는 dict 카탈로그를 반환합니다."""

    return [entry.to_dict() for entry in _CATALOG]


def get_api_catalog_entry(service_name: str, endpoint: str) -> ApiCatalogEntry:
    """서비스명과 endpoint에 맞는 카탈로그 항목을 반환합니다."""

    canonical_service_name = canonical_service_name_for_catalog(service_name)
    try:
        return _CATALOG_BY_ENDPOINT[(canonical_service_name, endpoint)]
    except KeyError as exc:
        known = ", ".join(
            entry.endpoint for entry in _CATALOG if entry.service_name == canonical_service_name
        )
        raise ValueError(
            f"{canonical_service_name} does not support endpoint {endpoint!r}; known: {known}"
        ) from exc


def api_catalog_for_method(method_name: str) -> tuple[ApiCatalogEntry, ...]:
    """public method 이름과 연결된 카탈로그 항목을 반환합니다."""

    return tuple(entry for entry in _CATALOG if method_name in entry.method_names)


def api_catalog_for_debug_input(
    function_name: str,
    input_data: Mapping[str, Any] | None = None,
) -> tuple[ApiCatalogEntry, ...]:
    """디버그 실행 입력으로 Streamlit Trace 탭에 보여줄 카탈로그 항목을 찾습니다."""

    if function_name == "call":
        data = input_data or {}
        service_name = data.get("service_name")
        endpoint = data.get("endpoint")
        if isinstance(service_name, str) and isinstance(endpoint, str):
            return (get_api_catalog_entry(service_name, endpoint),)
        return ()
    return api_catalog_for_method(function_name)


def canonical_service_name_for_catalog(service_name: str) -> str:
    """대소문자를 정규화한 AirKorea 서비스명을 반환합니다."""

    if service_name in _SERVICE_INFO:
        return service_name
    lookup = {name.lower(): name for name in _SERVICE_INFO}
    try:
        return lookup[service_name.lower()]
    except KeyError as exc:
        known = ", ".join(_SERVICE_INFO)
        raise ValueError(f"unknown AirKorea service {service_name!r}; known: {known}") from exc


__all__ = [
    "ApiCatalogEntry",
    "ParamSpec",
    "api_catalog",
    "api_catalog_dicts",
    "api_catalog_for_debug_input",
    "api_catalog_for_method",
    "api_catalog_params_for_method",
    "canonical_service_name_for_catalog",
    "get_api_catalog_entry",
]
