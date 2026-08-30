"""AirKorea 클라이언트가 반환하는 공개 Pydantic 모델."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from airkorea.codes import AirQualityGrade, InformCode, Pollutant
from airkorea.coords import LatLon

RawRecord = Mapping[str, Any]
T = TypeVar("T")


class AirKoreaModel(BaseModel):
    """변경 불가능한 airkorea 응답 모델의 기본 클래스."""

    model_config = ConfigDict(frozen=True)


class AirKoreaCallContext(AirKoreaModel):
    """응답을 만든 API 호출을 설명하는 인증키 제거 메타데이터."""

    provider: str = "data.go.kr"
    service_name: str | None = None
    endpoint: str | None = None
    request_params: RawRecord = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AirKoreaPage(AirKoreaModel, Generic[T]):
    """페이지 정보가 있는 AirKorea 원본 응답 페이지."""

    items: tuple[T, ...]
    total_count: int
    page_no: int
    num_of_rows: int
    raw: RawRecord = Field(repr=False)
    context: AirKoreaCallContext = Field(default_factory=AirKoreaCallContext)

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def has_next_page(self) -> bool:
        if self.page_no < 1 or self.num_of_rows < 1 or self.total_count < 1:
            return False
        return self.page_no * self.num_of_rows < self.total_count

    @property
    def next_page_no(self) -> int | None:
        if not self.has_next_page:
            return None
        return self.page_no + 1

    @property
    def service_name(self) -> str | None:
        return self.context.service_name

    @property
    def endpoint(self) -> str | None:
        return self.context.endpoint

    @property
    def request_params(self) -> RawRecord:
        return self.context.request_params

    @property
    def collected_at(self) -> datetime:
        return self.context.collected_at


class AirQualityMeasurement(AirKoreaModel):
    """측정소 하나의 시간별 대기질 측정값."""

    station_name: str
    data_time: datetime | None
    mang_name: str | None
    sido_name: str | None
    khai_value: int | None
    khai_grade: int | None
    khai_grade_label: str | None
    so2_value: float | None
    co_value: float | None
    o3_value: float | None
    no2_value: float | None
    pm10_value: float | None
    pm10_value_24h: float | None
    pm25_value: float | None
    pm25_value_24h: float | None
    so2_grade: int | None
    co_grade: int | None
    o3_grade: int | None
    no2_grade: int | None
    pm10_grade: int | None
    pm10_grade_1h: int | None
    pm25_grade: int | None
    pm25_grade_1h: int | None
    raw: RawRecord = Field(repr=False)

    @property
    def khai_grade_enum(self) -> AirQualityGrade | None:
        return AirQualityGrade.from_code(self.khai_grade)


class Station(AirKoreaModel):
    """대기질 측정소 메타데이터."""

    station_name: str
    addr: str | None
    year: int | None
    mang_name: str | None
    item: str | None
    lat: float | None
    lon: float | None
    raw: RawRecord = Field(repr=False)

    @property
    def coordinates(self) -> LatLon | None:
        if self.lat is None or self.lon is None:
            return None
        return LatLon(lat=self.lat, lon=self.lon)


class NearbyStation(AirKoreaModel):
    """TM 좌표 주변의 측정소."""

    station_name: str
    addr: str | None
    distance_km: float | None
    raw: RawRecord = Field(repr=False)


class TmCoordinate(AirKoreaModel):
    """읍/면/동 이름에 대한 AirKorea TM 기준 좌표."""

    sido_name: str | None
    sgg_name: str | None
    umd_name: str | None
    tm_x: float | None
    tm_y: float | None
    raw: RawRecord = Field(repr=False)


class ForecastNotice(AirKoreaModel):
    """미세먼지 또는 오존 예보 통보문."""

    data_time: str | None
    inform_code: str | None
    inform_data: date | None
    overall: str | None
    cause: str | None
    grade: str | None
    action: str | None
    image_urls: tuple[str, ...]
    raw: RawRecord = Field(repr=False)

    @property
    def inform_code_enum(self) -> InformCode | None:
        try:
            return InformCode(self.inform_code)
        except ValueError:
            return None


class WeeklyForecastNotice(AirKoreaModel):
    """주간 PM2.5 예보 통보문."""

    presented_at: str | None
    first_date: str | None
    first_content: str | None
    second_date: str | None
    second_content: str | None
    third_date: str | None
    third_content: str | None
    fourth_date: str | None
    fourth_content: str | None
    raw: RawRecord = Field(repr=False)


class AirQualityStat(AirKoreaModel):
    """AirKorea 통계 API의 평균/통계 대기질 행."""

    data_time: datetime | None
    measurement_date: date | None
    station_name: str | None
    sido_name: str | None
    city_name: str | None
    item_code: str | None
    data_gubun: str | None
    so2_value: float | None
    co_value: float | None
    o3_value: float | None
    no2_value: float | None
    pm10_value: float | None
    pm25_value: float | None
    region_values: Mapping[str, float | None] = Field(default_factory=dict, repr=False)
    raw: RawRecord = Field(default_factory=dict, repr=False)

    @property
    def item_code_enum(self) -> Pollutant | None:
        try:
            return Pollutant(self.item_code)
        except ValueError:
            return None


class AdvisoryOccurrence(AirKoreaModel):
    """오존 또는 황사 주의보 발생 행."""

    kind: str
    serial_number: int | None
    data_date: date | None
    district_name: str | None
    move_name: str | None
    issue_time: str | None
    issue_value: float | None
    clear_time: str | None
    clear_value: float | None
    max_value: float | None
    issue_level: int | None
    raw: RawRecord = Field(repr=False)


class DustAlarm(AirKoreaModel):
    """PM10/PM2.5 주의보 또는 경보 행."""

    serial_number: int | None
    data_date: date | None
    district_name: str | None
    move_name: str | None
    item_code: str | None
    issue_gbn: str | None
    issue_date: date | None
    issue_time: str | None
    issue_value: float | None
    clear_date: date | None
    clear_time: str | None
    clear_value: float | None
    raw: RawRecord = Field(repr=False)

    @property
    def item_code_enum(self) -> Pollutant | None:
        try:
            return Pollutant(self.item_code)
        except ValueError:
            return None


class TrafficStat(AirKoreaModel):
    """AirKorea 서비스키의 일별 API 트래픽 통계."""

    connection_date: date | None
    service_name: str | None
    count: int | None
    raw: RawRecord = Field(repr=False)


class HighPm25Forecast(AirKoreaModel):
    """고농도 PM2.5 예보 행."""

    data_time: str | None
    inform_data: date | None
    overall: str | None
    cause: str | None
    grade: str | None
    raw: RawRecord = Field(repr=False)


class CaiMeasurement(AirKoreaModel):
    """실시간 통합대기환경지수(CAI) 행."""

    station_name: str | None
    station_code: str | None
    data_time: datetime | None
    mang_name: str | None
    khai_value: int | None
    khai_grade: int | None
    khai_grade_label: str | None
    main_pollutant: str | None
    raw: RawRecord = Field(repr=False)

    @property
    def khai_grade_enum(self) -> AirQualityGrade | None:
        return AirQualityGrade.from_code(self.khai_grade)


class BackgroundConcentration(AirKoreaModel):
    """국가배경농도 합성데이터 행."""

    measurement_date: date | None
    measurement_time: str | None
    station_name: str | None
    so2_value: float | None
    co_value: float | None
    o3_value: float | None
    no2_value: float | None
    pm10_value: float | None
    pm25_value: float | None
    raw: RawRecord = Field(repr=False)


class EnglishAirQualityMeasurement(AirKoreaModel):
    """영문 실시간 측정정보 행."""

    station_name: str | None
    station_name_english: str | None
    data_time: datetime | None
    mang_name: str | None
    station_code: str | None
    khai_value: int | None
    khai_grade: int | None
    khai_grade_label: str | None
    so2_value: float | None
    co_value: float | None
    o3_value: float | None
    no2_value: float | None
    pm10_value: float | None
    pm25_value: float | None
    raw: RawRecord = Field(repr=False)

    @property
    def khai_grade_enum(self) -> AirQualityGrade | None:
        return AirQualityGrade.from_code(self.khai_grade)


class EnglishStation(AirKoreaModel):
    """영문 측정소 메타데이터 행."""

    station_name: str | None
    station_name_english: str | None
    road_address: str | None
    road_address_english: str | None
    lat: float | None
    lon: float | None
    raw: RawRecord = Field(repr=False)

    @property
    def coordinates(self) -> LatLon | None:
        if self.lat is None or self.lon is None:
            return None
        return LatLon(lat=self.lat, lon=self.lon)
