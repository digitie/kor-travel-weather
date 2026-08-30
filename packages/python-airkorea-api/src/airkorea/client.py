"""AirKorea 공개 API 클라이언트."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator, Mapping
from datetime import date, datetime
from typing import Any

from airkorea._convert import (
    parse_data_time,
    strip_or_none,
    to_date_or_none,
    to_float_or_none,
    to_int_or_none,
)
from airkorea._http import AsyncHttpClient, AsyncSessionLike, HttpClient, SessionLike
from airkorea.codes import (
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
from airkorea.coords import LatLon, TmPoint, resolve_airkorea_tm
from airkorea.exceptions import AirKoreaParseError
from airkorea.metadata import is_credential_param, load_service_key, make_call_context
from airkorea.models import (
    AdvisoryOccurrence,
    AirKoreaPage,
    AirQualityMeasurement,
    AirQualityStat,
    BackgroundConcentration,
    CaiMeasurement,
    DustAlarm,
    EnglishAirQualityMeasurement,
    EnglishStation,
    ForecastNotice,
    HighPm25Forecast,
    NearbyStation,
    RawRecord,
    Station,
    TmCoordinate,
    TrafficStat,
    WeeklyForecastNotice,
)
from airkorea.pagination import aiter_paginated_pages, iter_paginated_pages

DEFAULT_POLLUTION_BASE_URL = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc"
DEFAULT_STATION_BASE_URL = "https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc"
DEFAULT_STATS_BASE_URL = "https://apis.data.go.kr/B552584/ArpltnStatsSvc"
DEFAULT_OCCURRENCE_BASE_URL = "https://apis.data.go.kr/B552584/OzYlwsndOccrrncInforInqireSvc"
DEFAULT_ALARM_BASE_URL = "https://apis.data.go.kr/B552584/UlfptcaAlarmInqireSvc"
DEFAULT_USER_SUPPORT_BASE_URL = "https://apis.data.go.kr/B552584/UserSportSvc"
DEFAULT_HIGH_PM25_FORECAST_BASE_URL = "https://apis.data.go.kr/B552584/MinuDustFrcstDspthSvc"
DEFAULT_CAI_BASE_URL = "https://apis.data.go.kr/B552584/RltmKhaiInfoSvc"
DEFAULT_BACKGROUND_BASE_URL = "https://apis.data.go.kr/B552584/arpltsNtnBkgrDnstSyrdt"
DEFAULT_ENGLISH_MEASUREMENT_BASE_URL = "https://apis.data.go.kr/B552584/atmstMsrstnRltmInfo"
DEFAULT_ENGLISH_STATION_BASE_URL = "https://apis.data.go.kr/B552584/atmstMsrstnInfoEngNm"

SUPPORTED_ENDPOINTS = {
    "ArpltnInforInqireSvc": (
        "getMsrstnAcctoRltmMesureDnsty",
        "getUnityAirEnvrnIdexSnstiveAboveMsrstnList",
        "getCtprvnRltmMesureDnsty",
        "getMinuDustFrcstDspth",
        "getMinuDustWeekFrcstDspth",
    ),
    "MsrstnInfoInqireSvc": (
        "getMsrstnList",
        "getNearbyMsrstnList",
        "getTMStdrCrdnt",
    ),
    "ArpltnStatsSvc": (
        "getCtprvnMesureLIst",
        "getCtprvnMesureSidoLIst",
        "getMsrstnAcctoRDyrg",
        "getMsrstnAcctoRMmrg",
    ),
    "OzYlwsndOccrrncInforInqireSvc": (
        "getOzAdvsryOccrrncInfo",
        "getYlwsndAdvsryOccrrncInfo",
    ),
    "UlfptcaAlarmInqireSvc": ("getUlfptcaAlarmInfo",),
    "UserSportSvc": ("getSvckeyDalyStats",),
    "MinuDustFrcstDspthSvc": ("getMinuDustFrcstDspth50Over",),
    "RltmKhaiInfoSvc": ("getMsrstnKhaiRltmDnsty",),
    "arpltsNtnBkgrDnstSyrdt": ("getList",),
    "atmstMsrstnRltmInfo": ("getList",),
    "atmstMsrstnInfoEngNm": ("getList",),
}

_REGION_KEYS = (
    "seoul",
    "busan",
    "daegu",
    "incheon",
    "gwangju",
    "daejeon",
    "ulsan",
    "gyeonggi",
    "gangwon",
    "chungbuk",
    "chungnam",
    "jeonbuk",
    "jeonnam",
    "gyeongbuk",
    "gyeongnam",
    "jeju",
    "sejong",
)


class AirKoreaClient:
    """한국환경공단 AirKorea 공개 API 클라이언트."""

    def __init__(
        self,
        *,
        service_key: str | None = None,
        timeout: float = 10.0,
        retries: int = 3,
        retry_backoff: float = 0.3,
        session: SessionLike | None = None,
        pollution_base_url: str = DEFAULT_POLLUTION_BASE_URL,
        station_base_url: str = DEFAULT_STATION_BASE_URL,
        stats_base_url: str = DEFAULT_STATS_BASE_URL,
        occurrence_base_url: str = DEFAULT_OCCURRENCE_BASE_URL,
        alarm_base_url: str = DEFAULT_ALARM_BASE_URL,
        user_support_base_url: str = DEFAULT_USER_SUPPORT_BASE_URL,
        high_pm25_forecast_base_url: str = DEFAULT_HIGH_PM25_FORECAST_BASE_URL,
        cai_base_url: str = DEFAULT_CAI_BASE_URL,
        background_base_url: str = DEFAULT_BACKGROUND_BASE_URL,
        english_measurement_base_url: str = DEFAULT_ENGLISH_MEASUREMENT_BASE_URL,
        english_station_base_url: str = DEFAULT_ENGLISH_STATION_BASE_URL,
    ) -> None:
        resolved_service_key = _resolve_service_key(service_key)
        self._http = HttpClient(
            resolved_service_key,
            timeout=timeout,
            retries=retries,
            retry_backoff=retry_backoff,
            session=session,
        )
        self.pollution_base_url = pollution_base_url
        self.station_base_url = station_base_url
        self.stats_base_url = stats_base_url
        self.occurrence_base_url = occurrence_base_url
        self.alarm_base_url = alarm_base_url
        self.user_support_base_url = user_support_base_url
        self.high_pm25_forecast_base_url = high_pm25_forecast_base_url
        self.cai_base_url = cai_base_url
        self.background_base_url = background_base_url
        self.english_measurement_base_url = english_measurement_base_url
        self.english_station_base_url = english_station_base_url
        self.closed = False

    def __enter__(self) -> AirKoreaClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """내부 httpx 동기 클라이언트를 닫습니다."""

        self._http.close()
        self.closed = True

    @classmethod
    def from_env(
        cls,
        name: str = "DATA_GO_KR_SERVICE_KEY",
        *,
        dotenv_path: str | os.PathLike[str] | None = ".env",
        **kwargs: Any,
    ) -> AirKoreaClient:
        service_key = load_service_key(name, dotenv_path=dotenv_path)
        if service_key is None:
            raise ValueError(f"{name} is not set")
        return cls(service_key=service_key, **kwargs)

    @classmethod
    def aio(cls, *, service_key: str | None = None, **kwargs: Any) -> AsyncAirKoreaClient:
        """동일한 public surface를 가진 비동기 클라이언트를 생성합니다."""

        return AsyncAirKoreaClient(service_key=service_key, **kwargs)

    @classmethod
    def aio_from_env(
        cls,
        name: str = "DATA_GO_KR_SERVICE_KEY",
        *,
        dotenv_path: str | os.PathLike[str] | None = ".env",
        **kwargs: Any,
    ) -> AsyncAirKoreaClient:
        """환경변수나 ``.env``에서 서비스키를 읽어 비동기 클라이언트를 생성합니다."""

        return AsyncAirKoreaClient.from_env(name=name, dotenv_path=dotenv_path, **kwargs)

    def station_measurements(
        self,
        station_name: str,
        *,
        data_term: str | DataTerm = DataTerm.DAILY,
        page_no: int = 1,
        num_of_rows: int = 100,
        ver: str = "1.3",
    ) -> list[AirQualityMeasurement]:
        """측정소 하나의 실시간 대기질 측정값을 조회합니다."""

        resolved_station_name = _required_text(station_name, "station_name")
        body = self._pollution(
            "getMsrstnAcctoRltmMesureDnsty",
            {
                "stationName": resolved_station_name,
                "dataTerm": normalize_data_term(data_term),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
                "ver": ver,
            },
        )
        return [_measurement(row, station_name=resolved_station_name) for row in _items(body)]

    def latest_station_measurement(
        self,
        station_name: str,
        *,
        data_term: str | DataTerm = DataTerm.DAILY,
        ver: str = "1.3",
    ) -> AirQualityMeasurement | None:
        """측정소의 첫 측정값을 반환하고, 결과가 없으면 ``None``을 반환합니다."""

        rows = self.station_measurements(
            station_name,
            data_term=data_term,
            num_of_rows=1,
            ver=ver,
        )
        return rows[0] if rows else None

    def sido_measurements(
        self,
        sido_name: str | SidoName,
        *,
        page_no: int = 1,
        num_of_rows: int = 100,
        ver: str = "1.3",
    ) -> list[AirQualityMeasurement]:
        """시도 하나에 속한 모든 측정소의 실시간 측정값을 조회합니다."""

        body = self._pollution(
            "getCtprvnRltmMesureDnsty",
            {
                "sidoName": validate_sido_name(sido_name),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
                "ver": ver,
            },
        )
        return [_measurement(row) for row in _items(body)]

    def unhealthy_stations(
        self,
        *,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[AirQualityMeasurement]:
        """통합대기환경지수 등급이 나쁨 이상인 측정소를 조회합니다."""

        body = self._pollution(
            "getUnityAirEnvrnIdexSnstiveAboveMsrstnList",
            {
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_measurement(row) for row in _items(body)]

    def forecast_notices(
        self,
        *,
        search_date: str | date | None = None,
        inform_code: str | InformCode | Pollutant | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[ForecastNotice]:
        """미세먼지 또는 오존 예보 통보문을 조회합니다."""

        body = self._pollution(
            "getMinuDustFrcstDspth",
            {
                "searchDate": _date_param(search_date),
                "InformCode": normalize_inform_code(inform_code),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_forecast_notice(row) for row in _items(body)]

    def weekly_forecasts(
        self,
        *,
        search_date: str | date | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[WeeklyForecastNotice]:
        """주간 PM2.5 예보 통보문을 조회합니다."""

        body = self._pollution(
            "getMinuDustWeekFrcstDspth",
            {
                "searchDate": _date_param(search_date),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_weekly_forecast(row) for row in _items(body)]

    def stations(
        self,
        *,
        addr: str | None = None,
        station_name: str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[Station]:
        """대기질 측정소 메타데이터를 조회합니다."""

        body = self._station(
            "getMsrstnList",
            {
                "addr": addr,
                "stationName": station_name,
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_station_metadata(row) for row in _items(body)]

    def nearby_stations(
        self,
        *,
        coordinate: LatLon | tuple[float, float] | Mapping[str, Any] | None = None,
        tm: TmPoint | tuple[float, float] | Mapping[str, Any] | None = None,
        tm_x: float | None = None,
        tm_y: float | None = None,
        lat: float | None = None,
        lon: float | None = None,
        ver: str | None = None,
    ) -> list[NearbyStation]:
        """TM 좌표 또는 WGS84 위경도 주변의 측정소를 조회합니다."""

        query = resolve_airkorea_tm(
            coordinate=coordinate,
            tm=tm,
            tm_x=tm_x,
            tm_y=tm_y,
            lat=lat,
            lon=lon,
        )
        body = self._station(
            "getNearbyMsrstnList",
            {
                "tmX": query.tm_x,
                "tmY": query.tm_y,
                "ver": ver,
            },
        )
        return [_nearby_station(row) for row in _items(body)]

    def tm_coordinates(
        self,
        umd_name: str,
        *,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[TmCoordinate]:
        """읍/면/동 이름으로 AirKorea TM 기준 좌표를 조회합니다."""

        body = self._station(
            "getTMStdrCrdnt",
            {
                "umdName": _required_text(umd_name, "umd_name"),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_tm_coordinate(row) for row in _items(body)]

    def measurement_near(
        self,
        *,
        coordinate: LatLon | tuple[float, float] | Mapping[str, Any] | None = None,
        lat: float | None = None,
        lon: float | None = None,
        data_term: str | DataTerm = DataTerm.DAILY,
        ver: str = "1.3",
    ) -> AirQualityMeasurement | None:
        """WGS84 좌표에서 가장 가까운 측정소의 최신 측정값을 반환합니다."""

        stations = self.nearby_stations(coordinate=coordinate, lat=lat, lon=lon)
        if not stations:
            return None
        nearest = min(
            stations,
            key=lambda station: (
                station.distance_km if station.distance_km is not None else float("inf")
            ),
        )
        return self.latest_station_measurement(
            nearest.station_name,
            data_term=data_term,
            ver=ver,
        )

    def sido_average_stats(
        self,
        *,
        item_code: str | Pollutant,
        data_gubun: str | StatsDataGubun = StatsDataGubun.HOUR,
        search_condition: str | StatsSearchCondition = StatsSearchCondition.WEEK,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[AirQualityStat]:
        """시도별 실시간 평균 통계를 조회합니다."""

        body = self._stats(
            "getCtprvnMesureLIst",
            {
                "itemCode": normalize_pollutant_code(item_code),
                "dataGubun": normalize_stats_data_gubun(data_gubun),
                "searchCondition": normalize_stats_search_condition(search_condition),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_air_quality_stat(row) for row in _items(body)]

    def city_average_stats(
        self,
        sido_name: str,
        *,
        item_code: str | Pollutant | None = None,
        data_gubun: str | StatsDataGubun | None = None,
        search_condition: str | StatsSearchCondition = StatsSearchCondition.HOUR,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[AirQualityStat]:
        """시군구별 실시간 평균 통계를 조회합니다."""

        body = self._stats(
            "getCtprvnMesureSidoLIst",
            {
                "sidoName": _required_text(sido_name, "sido_name"),
                "itemCode": normalize_pollutant_code(item_code),
                "dataGubun": _optional_stats_data_gubun(data_gubun),
                "searchCondition": normalize_stats_search_condition(search_condition),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_air_quality_stat(row) for row in _items(body)]

    def station_daily_stats(
        self,
        inquiry_begin_date: str | date,
        inquiry_end_date: str | date,
        *,
        station_name: str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[AirQualityStat]:
        """측정소별 실시간 일평균 통계를 조회합니다."""

        body = self._stats(
            "getMsrstnAcctoRDyrg",
            {
                "inqBginDt": _date8_param(inquiry_begin_date, "inquiry_begin_date"),
                "inqEndDt": _date8_param(inquiry_end_date, "inquiry_end_date"),
                "msrstnName": station_name,
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_air_quality_stat(row) for row in _items(body)]

    def station_monthly_stats(
        self,
        inquiry_begin_date: str | date,
        inquiry_end_date: str | date,
        *,
        station_name: str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[AirQualityStat]:
        """측정소별 실시간 월평균 통계를 조회합니다."""

        body = self._stats(
            "getMsrstnAcctoRMmrg",
            {
                "inqBginDt": _date8_param(inquiry_begin_date, "inquiry_begin_date"),
                "inqEndDt": _date8_param(inquiry_end_date, "inquiry_end_date"),
                "msrstnName": station_name,
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_air_quality_stat(row) for row in _items(body)]

    def ozone_advisories(
        self,
        *,
        year: int | str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[AdvisoryOccurrence]:
        """오존 주의보 발생 정보를 조회합니다."""

        body = self._occurrence(
            "getOzAdvsryOccrrncInfo",
            {
                "year": _year_param(year),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_advisory_occurrence(row, kind="ozone") for row in _items(body)]

    def yellow_dust_advisories(
        self,
        *,
        year: int | str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[AdvisoryOccurrence]:
        """황사 주의보 발생 정보를 조회합니다."""

        body = self._occurrence(
            "getYlwsndAdvsryOccrrncInfo",
            {
                "year": _year_param(year),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_advisory_occurrence(row, kind="yellow_dust") for row in _items(body)]

    def dust_alarms(
        self,
        year: int | str,
        *,
        item_code: str | Pollutant | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[DustAlarm]:
        """PM10/PM2.5 주의보와 경보 현황을 조회합니다."""

        body = self._alarm(
            "getUlfptcaAlarmInfo",
            {
                "year": _year_param(year),
                "itemCode": normalize_pollutant_code(
                    item_code,
                    allowed=(Pollutant.PM10, Pollutant.PM25),
                ),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_dust_alarm(row) for row in _items(body)]

    def traffic_stats(
        self,
        search_date: str | date,
        *,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[TrafficStat]:
        """현재 서비스키의 일별 API 트래픽 통계를 조회합니다."""

        body = self._user_support(
            "getSvckeyDalyStats",
            {
                "searchDate": _date_param(search_date),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_traffic_stat(row) for row in _items(body)]

    def high_pm25_forecasts(
        self,
        search_date: str | date,
        *,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[HighPm25Forecast]:
        """고농도 PM2.5(50 ug/m3 초과) 예보를 조회합니다."""

        body = self._high_pm25_forecast(
            "getMinuDustFrcstDspth50Over",
            {
                "searchDate": _date_param(search_date),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_high_pm25_forecast(row) for row in _items(body)]

    def cai_measurements(
        self,
        *,
        station_name: str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[CaiMeasurement]:
        """실시간 통합대기환경지수(CAI) 행을 조회합니다."""

        body = self._cai(
            "getMsrstnKhaiRltmDnsty",
            {
                "stationName": station_name,
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_cai_measurement(row) for row in _items(body)]

    def background_concentrations(
        self,
        measurement_date: str | date,
        station_name: str,
        *,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[BackgroundConcentration]:
        """국가배경농도 합성데이터 행을 조회합니다."""

        body = self._background(
            "getList",
            {
                "msrmtYmd": _date8_param(measurement_date, "measurement_date"),
                "msrstnNm": _required_text(station_name, "station_name"),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_background_concentration(row) for row in _items(body)]

    def english_measurements(
        self,
        *,
        station_name: str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[EnglishAirQualityMeasurement]:
        """영문 실시간 측정정보 행을 조회합니다."""

        body = self._english_measurement(
            "getList",
            {
                "msrstnNm": station_name,
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_english_measurement(row) for row in _items(body)]

    def english_stations(
        self,
        *,
        station_name: str | None = None,
        road_address: str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[EnglishStation]:
        """영문 측정소 메타데이터 행을 조회합니다."""

        body = self._english_station(
            "getList",
            {
                "msrstnNm": station_name,
                "roadNmAddr": road_address,
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_english_station(row) for row in _items(body)]

    def call(
        self,
        service_name: str,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        page_no: int | None = 1,
        num_of_rows: int | None = 100,
    ) -> AirKoreaPage[RawRecord]:
        """지원 endpoint를 호출하고 페이지 메타데이터와 원본 item을 반환합니다."""

        canonical_service_name, canonical_endpoint = _validate_supported_endpoint(
            service_name,
            endpoint,
        )
        request_params = _page_params(
            _without_reserved_call_params(params),
            page_no=page_no,
            num_of_rows=num_of_rows,
        )
        body = self._http.get_body(
            self._base_url_for_service(canonical_service_name),
            canonical_endpoint,
            request_params,
            service_key_param=_service_key_param(canonical_service_name),
        )
        return _raw_page(
            body,
            service_name=canonical_service_name,
            endpoint=canonical_endpoint,
            request_params={"returnType": "json", **request_params},
        )

    def iter_pages(
        self,
        service_name: str,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        page_no: int = 1,
        num_of_rows: int = 100,
        max_pages: int | None = None,
        max_items: int | None = None,
    ) -> Iterator[AirKoreaPage[RawRecord]]:
        """지원 endpoint의 원본 페이지를 순회합니다."""

        canonical_service_name, canonical_endpoint = _validate_supported_endpoint(
            service_name,
            endpoint,
        )
        base_params = _without_page_params(params)

        def fetch_page(next_page_no: int, page_size: int) -> AirKoreaPage[RawRecord]:
            return self.call(
                canonical_service_name,
                canonical_endpoint,
                base_params,
                page_no=next_page_no,
                num_of_rows=page_size,
            )

        return iter_paginated_pages(
            fetch_page,
            page_no=page_no,
            num_of_rows=num_of_rows,
            max_pages=max_pages,
            max_items=max_items,
        )

    def _pollution(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._http.get_body(self.pollution_base_url, endpoint, params)

    def _station(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._http.get_body(self.station_base_url, endpoint, params)

    def _stats(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._http.get_body(self.stats_base_url, endpoint, params)

    def _occurrence(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._http.get_body(self.occurrence_base_url, endpoint, params)

    def _alarm(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._http.get_body(self.alarm_base_url, endpoint, params)

    def _user_support(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._http.get_body(
            self.user_support_base_url,
            endpoint,
            params,
            service_key_param="ServiceKey",
        )

    def _high_pm25_forecast(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._http.get_body(self.high_pm25_forecast_base_url, endpoint, params)

    def _cai(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._http.get_body(self.cai_base_url, endpoint, params)

    def _background(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._http.get_body(self.background_base_url, endpoint, params)

    def _english_measurement(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._http.get_body(self.english_measurement_base_url, endpoint, params)

    def _english_station(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._http.get_body(self.english_station_base_url, endpoint, params)

    def _base_url_for_service(self, service_name: str) -> str:
        return {
            "ArpltnInforInqireSvc": self.pollution_base_url,
            "MsrstnInfoInqireSvc": self.station_base_url,
            "ArpltnStatsSvc": self.stats_base_url,
            "OzYlwsndOccrrncInforInqireSvc": self.occurrence_base_url,
            "UlfptcaAlarmInqireSvc": self.alarm_base_url,
            "UserSportSvc": self.user_support_base_url,
            "MinuDustFrcstDspthSvc": self.high_pm25_forecast_base_url,
            "RltmKhaiInfoSvc": self.cai_base_url,
            "arpltsNtnBkgrDnstSyrdt": self.background_base_url,
            "atmstMsrstnRltmInfo": self.english_measurement_base_url,
            "atmstMsrstnInfoEngNm": self.english_station_base_url,
        }[service_name]


class AsyncAirKoreaClient:
    """한국환경공단 AirKorea 공개 API 비동기 클라이언트."""

    def __init__(
        self,
        *,
        service_key: str | None = None,
        timeout: float = 10.0,
        retries: int = 3,
        retry_backoff: float = 0.3,
        session: AsyncSessionLike | None = None,
        pollution_base_url: str = DEFAULT_POLLUTION_BASE_URL,
        station_base_url: str = DEFAULT_STATION_BASE_URL,
        stats_base_url: str = DEFAULT_STATS_BASE_URL,
        occurrence_base_url: str = DEFAULT_OCCURRENCE_BASE_URL,
        alarm_base_url: str = DEFAULT_ALARM_BASE_URL,
        user_support_base_url: str = DEFAULT_USER_SUPPORT_BASE_URL,
        high_pm25_forecast_base_url: str = DEFAULT_HIGH_PM25_FORECAST_BASE_URL,
        cai_base_url: str = DEFAULT_CAI_BASE_URL,
        background_base_url: str = DEFAULT_BACKGROUND_BASE_URL,
        english_measurement_base_url: str = DEFAULT_ENGLISH_MEASUREMENT_BASE_URL,
        english_station_base_url: str = DEFAULT_ENGLISH_STATION_BASE_URL,
    ) -> None:
        resolved_service_key = _resolve_service_key(service_key)
        self._http = AsyncHttpClient(
            resolved_service_key,
            timeout=timeout,
            retries=retries,
            retry_backoff=retry_backoff,
            session=session,
        )
        self.pollution_base_url = pollution_base_url
        self.station_base_url = station_base_url
        self.stats_base_url = stats_base_url
        self.occurrence_base_url = occurrence_base_url
        self.alarm_base_url = alarm_base_url
        self.user_support_base_url = user_support_base_url
        self.high_pm25_forecast_base_url = high_pm25_forecast_base_url
        self.cai_base_url = cai_base_url
        self.background_base_url = background_base_url
        self.english_measurement_base_url = english_measurement_base_url
        self.english_station_base_url = english_station_base_url
        self.closed = False

    async def __aenter__(self) -> AsyncAirKoreaClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """내부 httpx 비동기 클라이언트를 닫습니다."""

        await self._http.aclose()
        self.closed = True

    @classmethod
    def from_env(
        cls,
        name: str = "DATA_GO_KR_SERVICE_KEY",
        *,
        dotenv_path: str | os.PathLike[str] | None = ".env",
        **kwargs: Any,
    ) -> AsyncAirKoreaClient:
        service_key = load_service_key(name, dotenv_path=dotenv_path)
        if service_key is None:
            raise ValueError(f"{name} is not set")
        return cls(service_key=service_key, **kwargs)

    async def station_measurements(
        self,
        station_name: str,
        *,
        data_term: str | DataTerm = DataTerm.DAILY,
        page_no: int = 1,
        num_of_rows: int = 100,
        ver: str = "1.3",
    ) -> list[AirQualityMeasurement]:
        """측정소 하나의 실시간 대기질 측정값을 조회합니다."""

        resolved_station_name = _required_text(station_name, "station_name")
        body = await self._pollution(
            "getMsrstnAcctoRltmMesureDnsty",
            {
                "stationName": resolved_station_name,
                "dataTerm": normalize_data_term(data_term),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
                "ver": ver,
            },
        )
        return [_measurement(row, station_name=resolved_station_name) for row in _items(body)]

    async def latest_station_measurement(
        self,
        station_name: str,
        *,
        data_term: str | DataTerm = DataTerm.DAILY,
        ver: str = "1.3",
    ) -> AirQualityMeasurement | None:
        """측정소의 첫 측정값을 반환하고, 결과가 없으면 ``None``을 반환합니다."""

        rows = await self.station_measurements(
            station_name,
            data_term=data_term,
            num_of_rows=1,
            ver=ver,
        )
        return rows[0] if rows else None

    async def sido_measurements(
        self,
        sido_name: str | SidoName,
        *,
        page_no: int = 1,
        num_of_rows: int = 100,
        ver: str = "1.3",
    ) -> list[AirQualityMeasurement]:
        """시도 하나에 속한 모든 측정소의 실시간 측정값을 조회합니다."""

        body = await self._pollution(
            "getCtprvnRltmMesureDnsty",
            {
                "sidoName": validate_sido_name(sido_name),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
                "ver": ver,
            },
        )
        return [_measurement(row) for row in _items(body)]

    async def unhealthy_stations(
        self,
        *,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[AirQualityMeasurement]:
        """통합대기환경지수 등급이 나쁨 이상인 측정소를 조회합니다."""

        body = await self._pollution(
            "getUnityAirEnvrnIdexSnstiveAboveMsrstnList",
            {
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_measurement(row) for row in _items(body)]

    async def forecast_notices(
        self,
        *,
        search_date: str | date | None = None,
        inform_code: str | InformCode | Pollutant | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[ForecastNotice]:
        """미세먼지 또는 오존 예보 통보문을 조회합니다."""

        body = await self._pollution(
            "getMinuDustFrcstDspth",
            {
                "searchDate": _date_param(search_date),
                "InformCode": normalize_inform_code(inform_code),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_forecast_notice(row) for row in _items(body)]

    async def weekly_forecasts(
        self,
        *,
        search_date: str | date | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[WeeklyForecastNotice]:
        """주간 PM2.5 예보 통보문을 조회합니다."""

        body = await self._pollution(
            "getMinuDustWeekFrcstDspth",
            {
                "searchDate": _date_param(search_date),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_weekly_forecast(row) for row in _items(body)]

    async def stations(
        self,
        *,
        addr: str | None = None,
        station_name: str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[Station]:
        """대기질 측정소 메타데이터를 조회합니다."""

        body = await self._station(
            "getMsrstnList",
            {
                "addr": addr,
                "stationName": station_name,
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_station_metadata(row) for row in _items(body)]

    async def nearby_stations(
        self,
        *,
        coordinate: LatLon | tuple[float, float] | Mapping[str, Any] | None = None,
        tm: TmPoint | tuple[float, float] | Mapping[str, Any] | None = None,
        tm_x: float | None = None,
        tm_y: float | None = None,
        lat: float | None = None,
        lon: float | None = None,
        ver: str | None = None,
    ) -> list[NearbyStation]:
        """TM 좌표 또는 WGS84 위경도 주변의 측정소를 조회합니다."""

        query = resolve_airkorea_tm(
            coordinate=coordinate,
            tm=tm,
            tm_x=tm_x,
            tm_y=tm_y,
            lat=lat,
            lon=lon,
        )
        body = await self._station(
            "getNearbyMsrstnList",
            {
                "tmX": query.tm_x,
                "tmY": query.tm_y,
                "ver": ver,
            },
        )
        return [_nearby_station(row) for row in _items(body)]

    async def tm_coordinates(
        self,
        umd_name: str,
        *,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[TmCoordinate]:
        """읍/면/동 이름으로 AirKorea TM 기준 좌표를 조회합니다."""

        body = await self._station(
            "getTMStdrCrdnt",
            {
                "umdName": _required_text(umd_name, "umd_name"),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_tm_coordinate(row) for row in _items(body)]

    async def measurement_near(
        self,
        *,
        coordinate: LatLon | tuple[float, float] | Mapping[str, Any] | None = None,
        lat: float | None = None,
        lon: float | None = None,
        data_term: str | DataTerm = DataTerm.DAILY,
        ver: str = "1.3",
    ) -> AirQualityMeasurement | None:
        """WGS84 좌표에서 가장 가까운 측정소의 최신 측정값을 반환합니다."""

        stations = await self.nearby_stations(coordinate=coordinate, lat=lat, lon=lon)
        if not stations:
            return None
        nearest = min(
            stations,
            key=lambda station: (
                station.distance_km if station.distance_km is not None else float("inf")
            ),
        )
        return await self.latest_station_measurement(
            nearest.station_name,
            data_term=data_term,
            ver=ver,
        )

    async def sido_average_stats(
        self,
        *,
        item_code: str | Pollutant,
        data_gubun: str | StatsDataGubun = StatsDataGubun.HOUR,
        search_condition: str | StatsSearchCondition = StatsSearchCondition.WEEK,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[AirQualityStat]:
        """시도별 실시간 평균 통계를 조회합니다."""

        body = await self._stats(
            "getCtprvnMesureLIst",
            {
                "itemCode": normalize_pollutant_code(item_code),
                "dataGubun": normalize_stats_data_gubun(data_gubun),
                "searchCondition": normalize_stats_search_condition(search_condition),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_air_quality_stat(row) for row in _items(body)]

    async def city_average_stats(
        self,
        sido_name: str,
        *,
        item_code: str | Pollutant | None = None,
        data_gubun: str | StatsDataGubun | None = None,
        search_condition: str | StatsSearchCondition = StatsSearchCondition.HOUR,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[AirQualityStat]:
        """시군구별 실시간 평균 통계를 조회합니다."""

        body = await self._stats(
            "getCtprvnMesureSidoLIst",
            {
                "sidoName": _required_text(sido_name, "sido_name"),
                "itemCode": normalize_pollutant_code(item_code),
                "dataGubun": _optional_stats_data_gubun(data_gubun),
                "searchCondition": normalize_stats_search_condition(search_condition),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_air_quality_stat(row) for row in _items(body)]

    async def station_daily_stats(
        self,
        inquiry_begin_date: str | date,
        inquiry_end_date: str | date,
        *,
        station_name: str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[AirQualityStat]:
        """측정소별 실시간 일평균 통계를 조회합니다."""

        body = await self._stats(
            "getMsrstnAcctoRDyrg",
            {
                "inqBginDt": _date8_param(inquiry_begin_date, "inquiry_begin_date"),
                "inqEndDt": _date8_param(inquiry_end_date, "inquiry_end_date"),
                "msrstnName": station_name,
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_air_quality_stat(row) for row in _items(body)]

    async def station_monthly_stats(
        self,
        inquiry_begin_date: str | date,
        inquiry_end_date: str | date,
        *,
        station_name: str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[AirQualityStat]:
        """측정소별 실시간 월평균 통계를 조회합니다."""

        body = await self._stats(
            "getMsrstnAcctoRMmrg",
            {
                "inqBginDt": _date8_param(inquiry_begin_date, "inquiry_begin_date"),
                "inqEndDt": _date8_param(inquiry_end_date, "inquiry_end_date"),
                "msrstnName": station_name,
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_air_quality_stat(row) for row in _items(body)]

    async def ozone_advisories(
        self,
        *,
        year: int | str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[AdvisoryOccurrence]:
        """오존 주의보 발생 정보를 조회합니다."""

        body = await self._occurrence(
            "getOzAdvsryOccrrncInfo",
            {
                "year": _year_param(year),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_advisory_occurrence(row, kind="ozone") for row in _items(body)]

    async def yellow_dust_advisories(
        self,
        *,
        year: int | str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[AdvisoryOccurrence]:
        """황사 주의보 발생 정보를 조회합니다."""

        body = await self._occurrence(
            "getYlwsndAdvsryOccrrncInfo",
            {
                "year": _year_param(year),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_advisory_occurrence(row, kind="yellow_dust") for row in _items(body)]

    async def dust_alarms(
        self,
        year: int | str,
        *,
        item_code: str | Pollutant | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[DustAlarm]:
        """PM10/PM2.5 주의보와 경보 현황을 조회합니다."""

        body = await self._alarm(
            "getUlfptcaAlarmInfo",
            {
                "year": _year_param(year),
                "itemCode": normalize_pollutant_code(
                    item_code,
                    allowed=(Pollutant.PM10, Pollutant.PM25),
                ),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_dust_alarm(row) for row in _items(body)]

    async def traffic_stats(
        self,
        search_date: str | date,
        *,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[TrafficStat]:
        """현재 서비스키의 일별 API 트래픽 통계를 조회합니다."""

        body = await self._user_support(
            "getSvckeyDalyStats",
            {
                "searchDate": _date_param(search_date),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_traffic_stat(row) for row in _items(body)]

    async def high_pm25_forecasts(
        self,
        search_date: str | date,
        *,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[HighPm25Forecast]:
        """고농도 PM2.5(50 ug/m3 초과) 예보를 조회합니다."""

        body = await self._high_pm25_forecast(
            "getMinuDustFrcstDspth50Over",
            {
                "searchDate": _date_param(search_date),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_high_pm25_forecast(row) for row in _items(body)]

    async def cai_measurements(
        self,
        *,
        station_name: str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[CaiMeasurement]:
        """실시간 통합대기환경지수(CAI) 행을 조회합니다."""

        body = await self._cai(
            "getMsrstnKhaiRltmDnsty",
            {
                "stationName": station_name,
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_cai_measurement(row) for row in _items(body)]

    async def background_concentrations(
        self,
        measurement_date: str | date,
        station_name: str,
        *,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[BackgroundConcentration]:
        """국가배경농도 합성데이터 행을 조회합니다."""

        body = await self._background(
            "getList",
            {
                "msrmtYmd": _date8_param(measurement_date, "measurement_date"),
                "msrstnNm": _required_text(station_name, "station_name"),
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_background_concentration(row) for row in _items(body)]

    async def english_measurements(
        self,
        *,
        station_name: str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[EnglishAirQualityMeasurement]:
        """영문 실시간 측정정보 행을 조회합니다."""

        body = await self._english_measurement(
            "getList",
            {
                "msrstnNm": station_name,
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_english_measurement(row) for row in _items(body)]

    async def english_stations(
        self,
        *,
        station_name: str | None = None,
        road_address: str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> list[EnglishStation]:
        """영문 측정소 메타데이터 행을 조회합니다."""

        body = await self._english_station(
            "getList",
            {
                "msrstnNm": station_name,
                "roadNmAddr": road_address,
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        return [_english_station(row) for row in _items(body)]

    async def call(
        self,
        service_name: str,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        page_no: int | None = 1,
        num_of_rows: int | None = 100,
    ) -> AirKoreaPage[RawRecord]:
        """지원 endpoint를 호출하고 페이지 메타데이터와 원본 item을 반환합니다."""

        canonical_service_name, canonical_endpoint = _validate_supported_endpoint(
            service_name,
            endpoint,
        )
        request_params = _page_params(
            _without_reserved_call_params(params),
            page_no=page_no,
            num_of_rows=num_of_rows,
        )
        body = await self._http.get_body(
            self._base_url_for_service(canonical_service_name),
            canonical_endpoint,
            request_params,
            service_key_param=_service_key_param(canonical_service_name),
        )
        return _raw_page(
            body,
            service_name=canonical_service_name,
            endpoint=canonical_endpoint,
            request_params={"returnType": "json", **request_params},
        )

    def iter_pages(
        self,
        service_name: str,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        page_no: int = 1,
        num_of_rows: int = 100,
        max_pages: int | None = None,
        max_items: int | None = None,
    ) -> AsyncIterator[AirKoreaPage[RawRecord]]:
        """지원 endpoint의 원본 페이지를 순회합니다."""

        canonical_service_name, canonical_endpoint = _validate_supported_endpoint(
            service_name,
            endpoint,
        )
        base_params = _without_page_params(params)

        async def fetch_page(next_page_no: int, page_size: int) -> AirKoreaPage[RawRecord]:
            return await self.call(
                canonical_service_name,
                canonical_endpoint,
                base_params,
                page_no=next_page_no,
                num_of_rows=page_size,
            )

        return aiter_paginated_pages(
            fetch_page,
            page_no=page_no,
            num_of_rows=num_of_rows,
            max_pages=max_pages,
            max_items=max_items,
        )

    async def _pollution(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._http.get_body(self.pollution_base_url, endpoint, params)

    async def _station(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._http.get_body(self.station_base_url, endpoint, params)

    async def _stats(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._http.get_body(self.stats_base_url, endpoint, params)

    async def _occurrence(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._http.get_body(self.occurrence_base_url, endpoint, params)

    async def _alarm(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._http.get_body(self.alarm_base_url, endpoint, params)

    async def _user_support(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._http.get_body(
            self.user_support_base_url,
            endpoint,
            params,
            service_key_param="ServiceKey",
        )

    async def _high_pm25_forecast(
        self,
        endpoint: str,
        params: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return await self._http.get_body(self.high_pm25_forecast_base_url, endpoint, params)

    async def _cai(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._http.get_body(self.cai_base_url, endpoint, params)

    async def _background(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._http.get_body(self.background_base_url, endpoint, params)

    async def _english_measurement(
        self,
        endpoint: str,
        params: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return await self._http.get_body(self.english_measurement_base_url, endpoint, params)

    async def _english_station(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._http.get_body(self.english_station_base_url, endpoint, params)

    def _base_url_for_service(self, service_name: str) -> str:
        return {
            "ArpltnInforInqireSvc": self.pollution_base_url,
            "MsrstnInfoInqireSvc": self.station_base_url,
            "ArpltnStatsSvc": self.stats_base_url,
            "OzYlwsndOccrrncInforInqireSvc": self.occurrence_base_url,
            "UlfptcaAlarmInqireSvc": self.alarm_base_url,
            "UserSportSvc": self.user_support_base_url,
            "MinuDustFrcstDspthSvc": self.high_pm25_forecast_base_url,
            "RltmKhaiInfoSvc": self.cai_base_url,
            "arpltsNtnBkgrDnstSyrdt": self.background_base_url,
            "atmstMsrstnRltmInfo": self.english_measurement_base_url,
            "atmstMsrstnInfoEngNm": self.english_station_base_url,
        }[service_name]


def _resolve_service_key(service_key: str | None) -> str:
    resolved_service_key = service_key if service_key is not None else load_service_key()
    if resolved_service_key is None:
        raise ValueError("DATA_GO_KR_SERVICE_KEY is not set")
    return resolved_service_key


def _items(body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_items = body.get("items")
    if raw_items is None or raw_items == "":
        return []
    if isinstance(raw_items, Mapping) and "item" in raw_items:
        raw_items = raw_items["item"]
    if raw_items is None or raw_items == "":
        return []
    if isinstance(raw_items, Mapping):
        return [raw_items]
    if isinstance(raw_items, list) and all(isinstance(item, Mapping) for item in raw_items):
        return raw_items
    raise AirKoreaParseError("response.body.items must be an object or list")


def _raw_page(
    body: Mapping[str, Any],
    *,
    service_name: str,
    endpoint: str,
    request_params: Mapping[str, Any],
) -> AirKoreaPage[RawRecord]:
    items = tuple(_items(body))
    page_no = _int_from_mapping(request_params, "pageNo", default=1)
    num_of_rows = _int_from_mapping(
        body,
        "numOfRows",
        default=_int_from_mapping(request_params, "numOfRows", default=len(items)),
    )
    total_count = _int_from_mapping(body, "totalCount", default=-1)
    if total_count < 0:
        if num_of_rows > 0 and len(items) >= num_of_rows:
            raise AirKoreaParseError(
                "response.body.totalCount is missing or invalid for a full page; "
                "cannot determine whether more pages exist"
            )
        total_count = len(items)
    return AirKoreaPage[RawRecord](
        items=items,
        total_count=total_count,
        page_no=page_no,
        num_of_rows=num_of_rows,
        raw=body,
        context=make_call_context(
            service_name=service_name,
            endpoint=endpoint,
            request_params=request_params,
        ),
    )


def _validate_supported_endpoint(service_name: str, endpoint: str) -> tuple[str, str]:
    canonical_service_name = _canonical_service_name(service_name)
    endpoints = SUPPORTED_ENDPOINTS[canonical_service_name]
    if endpoint not in endpoints:
        known = ", ".join(endpoints)
        raise ValueError(
            f"{canonical_service_name} does not support endpoint {endpoint!r}; known: {known}"
        )
    return canonical_service_name, endpoint


def _canonical_service_name(service_name: str) -> str:
    if service_name in SUPPORTED_ENDPOINTS:
        return service_name
    lookup = {name.lower(): name for name in SUPPORTED_ENDPOINTS}
    try:
        return lookup[service_name.lower()]
    except KeyError as exc:
        known = ", ".join(SUPPORTED_ENDPOINTS)
        raise ValueError(f"unknown AirKorea service {service_name!r}; known: {known}") from exc


def _service_key_param(service_name: str) -> str:
    return "ServiceKey" if service_name == "UserSportSvc" else "serviceKey"


def _page_params(
    params: Mapping[str, Any] | None,
    *,
    page_no: int | None,
    num_of_rows: int | None,
) -> dict[str, Any]:
    request_params = dict(params or {})
    if page_no is not None:
        request_params["pageNo"] = page_no
    if num_of_rows is not None:
        request_params["numOfRows"] = num_of_rows
    return request_params


def _without_page_params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    cleaned = dict(params or {})
    cleaned.pop("pageNo", None)
    cleaned.pop("numOfRows", None)
    return cleaned


def _without_reserved_call_params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in (params or {}).items()
        if not is_credential_param(str(key)) and str(key) != "returnType"
    }


def _int_from_mapping(mapping: Mapping[str, Any], key: str, *, default: int) -> int:
    try:
        return int(str(mapping.get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def _measurement(
    row: Mapping[str, Any],
    *,
    station_name: str | None = None,
) -> AirQualityMeasurement:
    try:
        khai_grade = to_int_or_none(row.get("khaiGrade"))
        return AirQualityMeasurement(
            station_name=_required_text(row.get("stationName") or station_name, "stationName"),
            data_time=parse_data_time(row.get("dataTime")),
            mang_name=strip_or_none(row.get("mangName")),
            sido_name=strip_or_none(row.get("sidoName")),
            khai_value=to_int_or_none(row.get("khaiValue")),
            khai_grade=khai_grade,
            khai_grade_label=grade_label(khai_grade),
            so2_value=to_float_or_none(row.get("so2Value")),
            co_value=to_float_or_none(row.get("coValue")),
            o3_value=to_float_or_none(row.get("o3Value")),
            no2_value=to_float_or_none(row.get("no2Value")),
            pm10_value=to_float_or_none(row.get("pm10Value")),
            pm10_value_24h=to_float_or_none(row.get("pm10Value24")),
            pm25_value=to_float_or_none(row.get("pm25Value")),
            pm25_value_24h=to_float_or_none(row.get("pm25Value24")),
            so2_grade=to_int_or_none(row.get("so2Grade")),
            co_grade=to_int_or_none(row.get("coGrade")),
            o3_grade=to_int_or_none(row.get("o3Grade")),
            no2_grade=to_int_or_none(row.get("no2Grade")),
            pm10_grade=to_int_or_none(row.get("pm10Grade")),
            pm10_grade_1h=to_int_or_none(row.get("pm10Grade1h")),
            pm25_grade=to_int_or_none(row.get("pm25Grade")),
            pm25_grade_1h=to_int_or_none(row.get("pm25Grade1h")),
            raw=row,
        )
    except (TypeError, ValueError) as exc:
        raise AirKoreaParseError(f"malformed measurement item: {row!r}") from exc


def _station_metadata(row: Mapping[str, Any]) -> Station:
    try:
        return Station(
            station_name=_required_text(row.get("stationName"), "stationName"),
            addr=strip_or_none(row.get("addr")),
            year=to_int_or_none(row.get("year")),
            mang_name=strip_or_none(row.get("mangName")),
            item=strip_or_none(row.get("item")),
            lat=to_float_or_none(row.get("dmX")),
            lon=to_float_or_none(row.get("dmY")),
            raw=row,
        )
    except (TypeError, ValueError) as exc:
        raise AirKoreaParseError(f"malformed station item: {row!r}") from exc


def _nearby_station(row: Mapping[str, Any]) -> NearbyStation:
    try:
        return NearbyStation(
            station_name=_required_text(row.get("stationName"), "stationName"),
            addr=strip_or_none(row.get("addr")),
            distance_km=to_float_or_none(row.get("tm")),
            raw=row,
        )
    except (TypeError, ValueError) as exc:
        raise AirKoreaParseError(f"malformed nearby station item: {row!r}") from exc


def _tm_coordinate(row: Mapping[str, Any]) -> TmCoordinate:
    try:
        return TmCoordinate(
            sido_name=strip_or_none(row.get("sidoName")),
            sgg_name=strip_or_none(row.get("sggName")),
            umd_name=strip_or_none(row.get("umdName")),
            tm_x=to_float_or_none(row.get("tmX")),
            tm_y=to_float_or_none(row.get("tmY")),
            raw=row,
        )
    except (TypeError, ValueError) as exc:
        raise AirKoreaParseError(f"malformed TM coordinate item: {row!r}") from exc


def _forecast_notice(row: Mapping[str, Any]) -> ForecastNotice:
    try:
        image_urls = tuple(
            url
            for url in (strip_or_none(row.get(f"imageUrl{index}")) for index in range(1, 10))
            if url is not None
        )
        return ForecastNotice(
            data_time=strip_or_none(row.get("dataTime")),
            inform_code=strip_or_none(row.get("informCode")),
            inform_data=to_date_or_none(row.get("informData")),
            overall=strip_or_none(row.get("informOverall") or row.get("infornOverall")),
            cause=strip_or_none(row.get("informCause")),
            grade=strip_or_none(row.get("informGrade")),
            action=strip_or_none(row.get("actionKnack")),
            image_urls=image_urls,
            raw=row,
        )
    except (TypeError, ValueError) as exc:
        raise AirKoreaParseError(f"malformed forecast notice item: {row!r}") from exc


def _weekly_forecast(row: Mapping[str, Any]) -> WeeklyForecastNotice:
    return WeeklyForecastNotice(
        presented_at=strip_or_none(row.get("presnatnDt") or row.get("presentnDt")),
        first_date=strip_or_none(row.get("frcstOneDt")),
        first_content=strip_or_none(row.get("frcstOneCn")),
        second_date=strip_or_none(row.get("frcstTwoDt")),
        second_content=strip_or_none(row.get("frcstTwoCn")),
        third_date=strip_or_none(row.get("frcstThreeDt")),
        third_content=strip_or_none(row.get("frcstThreeCn")),
        fourth_date=strip_or_none(row.get("frcstFourDt")),
        fourth_content=strip_or_none(row.get("frcstFourCn")),
        raw=row,
    )


def _air_quality_stat(row: Mapping[str, Any]) -> AirQualityStat:
    try:
        return AirQualityStat(
            data_time=_data_time(row, "dataTime"),
            measurement_date=_date_value(row, "msurDt", "msrmtYmd", "dataDate"),
            station_name=_text(row, "msrstnName", "stationName", "msrstnNm"),
            sido_name=_text(row, "sidoName", "sidoNm"),
            city_name=_text(row, "cityName", "sggName", "districtName"),
            item_code=_text(row, "itemCode"),
            data_gubun=_text(row, "dataGubun"),
            so2_value=_float(row, "so2Value"),
            co_value=_float(row, "coValue"),
            o3_value=_float(row, "o3Value"),
            no2_value=_float(row, "no2Value"),
            pm10_value=_float(row, "pm10Value"),
            pm25_value=_float(row, "pm25Value"),
            region_values={
                key: _float(row, key)
                for key in _REGION_KEYS
                if key in row
            },
            raw=row,
        )
    except (TypeError, ValueError) as exc:
        raise AirKoreaParseError(f"malformed statistics item: {row!r}") from exc


def _advisory_occurrence(row: Mapping[str, Any], *, kind: str) -> AdvisoryOccurrence:
    try:
        return AdvisoryOccurrence(
            kind=kind,
            serial_number=_int(row, "sn"),
            data_date=_date_value(row, "dataDate", "issueDate"),
            district_name=_text(row, "districtName"),
            move_name=_text(row, "moveName"),
            issue_time=_text(row, "issueTime"),
            issue_value=_float(row, "issueVal"),
            clear_time=_text(row, "clearTime"),
            clear_value=_float(row, "clearVal"),
            max_value=_float(row, "maxVal"),
            issue_level=_int(row, "issueLvl"),
            raw=row,
        )
    except (TypeError, ValueError) as exc:
        raise AirKoreaParseError(f"malformed advisory occurrence item: {row!r}") from exc


def _dust_alarm(row: Mapping[str, Any]) -> DustAlarm:
    try:
        return DustAlarm(
            serial_number=_int(row, "sn"),
            data_date=_date_value(row, "dataDate"),
            district_name=_text(row, "districtName"),
            move_name=_text(row, "moveName"),
            item_code=_text(row, "itemCode"),
            issue_gbn=_text(row, "issueGbn"),
            issue_date=_date_value(row, "issueDate"),
            issue_time=_text(row, "issueTime"),
            issue_value=_float(row, "issueVal"),
            clear_date=_date_value(row, "clearDate"),
            clear_time=_text(row, "clearTime"),
            clear_value=_float(row, "clearVal"),
            raw=row,
        )
    except (TypeError, ValueError) as exc:
        raise AirKoreaParseError(f"malformed dust alarm item: {row!r}") from exc


def _traffic_stat(row: Mapping[str, Any]) -> TrafficStat:
    try:
        return TrafficStat(
            connection_date=_date_value(row, "conectDe", "connectDe", "searchDate"),
            service_name=_text(row, "conectOprtinNm", "connectOprtinNm"),
            count=_int(row, "conectCo", "connectCo"),
            raw=row,
        )
    except (TypeError, ValueError) as exc:
        raise AirKoreaParseError(f"malformed traffic stat item: {row!r}") from exc


def _high_pm25_forecast(row: Mapping[str, Any]) -> HighPm25Forecast:
    try:
        return HighPm25Forecast(
            data_time=_text(row, "dataTime"),
            inform_data=_date_value(row, "informData", "searchDate"),
            overall=_text(row, "informOverall", "infornOverall"),
            cause=_text(row, "informCause"),
            grade=_text(row, "informGrade"),
            raw=row,
        )
    except (TypeError, ValueError) as exc:
        raise AirKoreaParseError(f"malformed high PM2.5 forecast item: {row!r}") from exc


def _cai_measurement(row: Mapping[str, Any]) -> CaiMeasurement:
    try:
        khai_grade = _int(row, "khaiGrade")
        return CaiMeasurement(
            station_name=_text(row, "stationName", "msrstnName", "msrstnNm"),
            station_code=_text(row, "stationCode", "msrstnCode", "msrstnCd"),
            data_time=_data_time(row, "dataTime", "msurDt"),
            mang_name=_text(row, "mangName"),
            khai_value=_int(row, "khaiValue"),
            khai_grade=khai_grade,
            khai_grade_label=grade_label(khai_grade),
            main_pollutant=_text(row, "mainPollutant", "mainPollutantName"),
            raw=row,
        )
    except (TypeError, ValueError) as exc:
        raise AirKoreaParseError(f"malformed CAI item: {row!r}") from exc


def _background_concentration(row: Mapping[str, Any]) -> BackgroundConcentration:
    try:
        return BackgroundConcentration(
            measurement_date=_date_value(row, "msrmtYmd", "msurDt", "dataDate"),
            measurement_time=_text(row, "msrmtTm", "msrmtHr", "dataTime"),
            station_name=_text(row, "msrstnNm", "msrstnName", "stationName"),
            so2_value=_float(row, "so2Value", "so2Vl"),
            co_value=_float(row, "coValue", "coVl"),
            o3_value=_float(row, "o3Value", "o3Vl"),
            no2_value=_float(row, "no2Value", "no2Vl"),
            pm10_value=_float(row, "pm10Value", "pm10Vl"),
            pm25_value=_float(row, "pm25Value", "pm25Vl"),
            raw=row,
        )
    except (TypeError, ValueError) as exc:
        raise AirKoreaParseError(f"malformed background concentration item: {row!r}") from exc


def _english_measurement(row: Mapping[str, Any]) -> EnglishAirQualityMeasurement:
    try:
        khai_grade = _int(row, "khaiGrade")
        return EnglishAirQualityMeasurement(
            station_name=_text(row, "msrstnNm", "stationName"),
            station_name_english=_text(row, "msrstnEngNm", "stationNameEng", "engName"),
            data_time=_data_time(row, "dataTime", "msurDt"),
            mang_name=_text(row, "mangName"),
            station_code=_text(row, "stationCode", "msrstnCode", "msrstnCd"),
            khai_value=_int(row, "khaiValue"),
            khai_grade=khai_grade,
            khai_grade_label=grade_label(khai_grade),
            so2_value=_float(row, "so2Value"),
            co_value=_float(row, "coValue"),
            o3_value=_float(row, "o3Value"),
            no2_value=_float(row, "no2Value"),
            pm10_value=_float(row, "pm10Value"),
            pm25_value=_float(row, "pm25Value"),
            raw=row,
        )
    except (TypeError, ValueError) as exc:
        raise AirKoreaParseError(f"malformed English measurement item: {row!r}") from exc


def _english_station(row: Mapping[str, Any]) -> EnglishStation:
    try:
        return EnglishStation(
            station_name=_text(row, "msrstnNm", "stationName"),
            station_name_english=_text(row, "msrstnEngNm", "stationNameEng", "engName"),
            road_address=_text(row, "roadNmAddr", "addr"),
            road_address_english=_text(row, "roadNmAddrEng", "addrEng"),
            lat=_float(row, "dmX", "lat"),
            lon=_float(row, "dmY", "lon"),
            raw=row,
        )
    except (TypeError, ValueError) as exc:
        raise AirKoreaParseError(f"malformed English station item: {row!r}") from exc


def _required_text(value: Any, field: str) -> str:
    text = strip_or_none(value)
    if text is None:
        raise ValueError(f"{field} is required")
    return text


def _optional_stats_data_gubun(value: str | StatsDataGubun | None) -> str | None:
    if value is None:
        return None
    return normalize_stats_data_gubun(value)


def _date_param(value: str | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return _required_text(value, "search_date")


def _date8_param(value: str | date, field: str) -> str:
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = _required_text(value, field)
    parsed = to_date_or_none(text)
    return parsed.strftime("%Y%m%d") if parsed is not None else text


def _year_param(value: int | str | None) -> str | None:
    if value is None:
        return None
    text = _required_text(value, "year")
    if len(text) != 4 or not text.isdigit():
        raise ValueError("year must be a four-digit year")
    return text


def _first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if strip_or_none(value) is not None:
            return value
    return None


def _text(row: Mapping[str, Any], *keys: str) -> str | None:
    return strip_or_none(_first(row, *keys))


def _float(row: Mapping[str, Any], *keys: str) -> float | None:
    return to_float_or_none(_first(row, *keys))


def _int(row: Mapping[str, Any], *keys: str) -> int | None:
    return to_int_or_none(_first(row, *keys))


def _date_value(row: Mapping[str, Any], *keys: str) -> date | None:
    return to_date_or_none(_first(row, *keys))


def _data_time(row: Mapping[str, Any], *keys: str) -> datetime | None:
    return parse_data_time(_first(row, *keys))
