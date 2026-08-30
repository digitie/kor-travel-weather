"""AirKorea TM 요청을 위한 좌표 값 객체와 변환 헬퍼."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

DEFAULT_AIRKOREA_TM_CRS = "EPSG:2097"
WGS84_CRS = "EPSG:4326"
_ALLOWED_CRS = frozenset({DEFAULT_AIRKOREA_TM_CRS, WGS84_CRS})


@dataclass(frozen=True)
class LatLon:
    """WGS84 위경도 쌍을 표준 ``lat, lon`` 순서로 표현합니다."""

    lat: float
    lon: float

    def __post_init__(self) -> None:
        validate_latlon(self.lat, self.lon)

    @property
    def latitude(self) -> float:
        return self.lat

    @property
    def longitude(self) -> float:
        return self.lon

    def as_tuple(self) -> tuple[float, float]:
        return self.lat, self.lon

    def to_tm(self, *, target_crs: str = DEFAULT_AIRKOREA_TM_CRS) -> TmPoint:
        tm_x, tm_y = wgs84_to_tm(self.lat, self.lon, target_crs=target_crs)
        return TmPoint(tm_x, tm_y)


@dataclass(frozen=True)
class TmPoint:
    """AirKorea TM 좌표 쌍을 표준 ``tm_x, tm_y`` 순서로 표현합니다."""

    tm_x: float
    tm_y: float

    @property
    def x(self) -> float:
        return self.tm_x

    @property
    def y(self) -> float:
        return self.tm_y

    def as_tuple(self) -> tuple[float, float]:
        return self.tm_x, self.tm_y

    def to_wgs84(self, *, source_crs: str = DEFAULT_AIRKOREA_TM_CRS) -> LatLon:
        lat, lon = tm_to_wgs84(self.tm_x, self.tm_y, source_crs=source_crs)
        return LatLon(lat, lon)


LatLonLike = LatLon | tuple[float, float] | Mapping[str, Any]
TmPointLike = TmPoint | tuple[float, float] | Mapping[str, Any]


def validate_latlon(lat: float, lon: float) -> None:
    if not -90.0 <= lat <= 90.0:
        raise ValueError("lat must be between -90 and 90")
    if not -180.0 <= lon <= 180.0:
        raise ValueError("lon must be between -180 and 180")


def coerce_latlon(
    value: LatLon | tuple[float, float] | Mapping[str, Any] | None = None,
    *,
    lat: float | None = None,
    lon: float | None = None,
) -> LatLon:
    """WGS84 좌표 입력을 ``LatLon``으로 정규화합니다.

    허용 형식은 ``LatLon``, ``(lat, lon)``, ``lat``/``lon`` 또는
    ``latitude``/``longitude`` mapping, 명시적 keyword 쌍입니다.
    """

    if value is not None and (lat is not None or lon is not None):
        raise ValueError("Provide either coordinate value or lat/lon keywords, not both")
    if isinstance(value, LatLon):
        return value
    if isinstance(value, tuple):
        if len(value) != 2:
            raise ValueError("coordinate tuple must be (lat, lon)")
        return LatLon(float(value[0]), float(value[1]))
    if isinstance(value, Mapping):
        return LatLon(
            _mapping_float(value, "lat", "latitude"),
            _mapping_float(value, "lon", "lng", "longitude"),
        )
    if lat is None or lon is None:
        raise ValueError("Both lat and lon are required")
    return LatLon(float(lat), float(lon))


def coerce_tm_point(
    value: TmPoint | tuple[float, float] | Mapping[str, Any] | None = None,
    *,
    tm_x: float | None = None,
    tm_y: float | None = None,
) -> TmPoint:
    """AirKorea TM 좌표 입력을 ``TmPoint``로 정규화합니다."""

    if value is not None and (tm_x is not None or tm_y is not None):
        raise ValueError("Provide either TM value or tm_x/tm_y keywords, not both")
    if isinstance(value, TmPoint):
        return value
    if isinstance(value, tuple):
        if len(value) != 2:
            raise ValueError("TM tuple must be (tm_x, tm_y)")
        return TmPoint(float(value[0]), float(value[1]))
    if isinstance(value, Mapping):
        return TmPoint(
            _mapping_float(value, "tm_x", "tmX", "x"),
            _mapping_float(value, "tm_y", "tmY", "y"),
        )
    if tm_x is None or tm_y is None:
        raise ValueError("Both tm_x and tm_y are required")
    return TmPoint(float(tm_x), float(tm_y))


def resolve_airkorea_tm(
    *,
    coordinate: LatLon | tuple[float, float] | Mapping[str, Any] | None = None,
    tm: TmPoint | tuple[float, float] | Mapping[str, Any] | None = None,
    lat: float | None = None,
    lon: float | None = None,
    tm_x: float | None = None,
    tm_y: float | None = None,
) -> TmPoint:
    """WGS84 또는 TM 입력을 AirKorea TM 조회 좌표로 해석합니다."""

    has_wgs84 = coordinate is not None or lat is not None or lon is not None
    has_tm = tm is not None or tm_x is not None or tm_y is not None
    if has_wgs84 and has_tm:
        raise ValueError("Provide either WGS84 coordinate or TM coordinate, not both")
    if has_tm:
        return coerce_tm_point(tm, tm_x=tm_x, tm_y=tm_y)
    if has_wgs84:
        return coerce_latlon(coordinate, lat=lat, lon=lon).to_tm()
    raise ValueError("Either WGS84 coordinate or TM coordinate is required")


def wgs84_to_tm(
    lat: float,
    lon: float,
    *,
    target_crs: str = DEFAULT_AIRKOREA_TM_CRS,
) -> tuple[float, float]:
    """WGS84 위경도를 AirKorea 레거시 TM 좌표로 변환합니다.

    AirKorea 근접 측정소 endpoint는 TM 중부원점 좌표를 문서화합니다.
    기존 AirKorea 예제는 EPSG:2097과 맞습니다. 도로명주소 API 좌표를
    이미 가지고 있다면 변환하지 말고 클라이언트에 직접 넘기세요.
    """

    validate_latlon(lat, lon)
    transformer = _transformer(WGS84_CRS, target_crs)
    tm_x, tm_y = transformer.transform(lon, lat)
    return float(tm_x), float(tm_y)


def tm_to_wgs84(
    tm_x: float,
    tm_y: float,
    *,
    source_crs: str = DEFAULT_AIRKOREA_TM_CRS,
) -> tuple[float, float]:
    """AirKorea 레거시 TM 좌표를 WGS84 위경도로 변환합니다."""

    transformer = _transformer(source_crs, WGS84_CRS)
    lon, lat = transformer.transform(tm_x, tm_y)
    return float(lat), float(lon)


def _mapping_float(mapping: Mapping[str, Any], *keys: str) -> float:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return float(value)
    raise ValueError(f"mapping must contain one of {keys}")


@lru_cache(maxsize=16)
def _transformer(source_crs: str, target_crs: str):  # type: ignore[no-untyped-def]
    if source_crs not in _ALLOWED_CRS or target_crs not in _ALLOWED_CRS:
        raise ValueError(f"CRS must be one of {sorted(_ALLOWED_CRS)}")
    try:
        from pyproj import Transformer
    except ModuleNotFoundError as exc:  # pragma: no cover - 패키지 필수 의존성
        raise RuntimeError("pyproj is required for AirKorea coordinate conversion") from exc
    return Transformer.from_crs(source_crs, target_crs, always_xy=True)
