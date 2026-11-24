"""AirKorea 측정소를 weather anchor와 관측 fact로 연결하는 adapter."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from airkorea import AirKoreaClient
from airkorea.models import AirQualityMeasurement, Station

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue

AIRKOREA_PROVIDER = "python-airkorea-api"
AIRKOREA_STATION_DATASET = "airkorea_station_catalog"
AIRKOREA_MEASUREMENT_DATASET = "airkorea_realtime_measurement"

# ``python-airkorea-api`` validates these exact short SIDO names.  Keep the
# aliases here instead of depending on the vendored enum so persisted station
# metadata remains stable if the library is upgraded.
_SIDO_ALIASES: tuple[tuple[str, str], ...] = (
    ("서울특별시", "서울"),
    ("부산광역시", "부산"),
    ("대구광역시", "대구"),
    ("인천광역시", "인천"),
    ("광주광역시", "광주"),
    ("대전광역시", "대전"),
    ("울산광역시", "울산"),
    ("세종특별자치시", "세종"),
    ("경기도", "경기"),
    ("강원특별자치도", "강원"),
    ("강원도", "강원"),
    ("충청북도", "충북"),
    ("충청남도", "충남"),
    ("전라북도", "전북"),
    ("전북특별자치도", "전북"),
    ("전라남도", "전남"),
    ("경상북도", "경북"),
    ("경상남도", "경남"),
    ("제주특별자치도", "제주"),
    ("서울", "서울"),
    ("부산", "부산"),
    ("대구", "대구"),
    ("인천", "인천"),
    ("광주", "광주"),
    ("대전", "대전"),
    ("울산", "울산"),
    ("세종", "세종"),
    ("경기", "경기"),
    ("강원", "강원"),
    ("충북", "충북"),
    ("충남", "충남"),
    ("전북", "전북"),
    ("전남", "전남"),
    ("경북", "경북"),
    ("경남", "경남"),
    ("제주", "제주"),
)


#: 행정구역 통합으로 주소 첫 토큰이 바뀌었지만 AirKorea 시도별 실시간 측정
#: (getCtprvnRltmMesureDnsty)의 ``sidoName`` 묶음은 그대로인 경우. 2026-10-14 이후
#: 측정소 목록의 광주 도심 측정소 주소가 ``전남광주통합특별시``로 바뀌었고, 첫 토큰이
#: ``전남``으로 시작해 ``normalize_sido_name``이 전남으로 묶었다. 그런데 전남 묶음 응답에는
#: 광주 측정소가 없어(운영 2026-10-14~11-24 측정 원천에 sidoName=광주 0건, 매시 13곳 누락)
#: 13개 측정소가 한 번도 수집되지 않았다. 통합 주소는 두 묶음을 모두 묻고 이름으로만
#: 맞춘다. 저장된 ``sido_name`` metadata는 바꾸지 않는다(anchor는 insert-only).
_MERGED_SIDO_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("전남광주", ("광주", "전남")),
)

#: 통합 주소 측정소가 실제로 속한 통합 전 시도. 두 묶음을 모두 묻는 것은 그대로 두되,
#: 이름만으로 맞추면 같은 이름의 다른 시도 측정소 관측이 붙을 수 있다(같은 이름의 짝
#: anchor가 상한 밖이거나 비활성이면 후보가 하나뿐이라 모호성으로도 걸리지 않는다).
#: 주소 두 번째 토큰(자치구·시·군)으로 anchor의 시도를 하나로 가리고, 원천 행의
#: ``sidoName``과 대조한다. 표에 없는 토큰이면 통합 전 후보 시도 전부를 기대값으로 둔다.
_MERGED_DISTRICT_SIDOS: dict[str, tuple[tuple[str, frozenset[str]], ...]] = {
    "전남광주": (
        ("광주", frozenset({"동구", "서구", "남구", "북구", "광산구"})),
        (
            "전남",
            frozenset({
                "목포시", "여수시", "순천시", "나주시", "광양시",
                "담양군", "곡성군", "구례군", "고흥군", "보성군", "화순군",
                "장흥군", "강진군", "해남군", "영암군", "무안군", "함평군",
                "영광군", "장성군", "완도군", "진도군", "신안군",
            }),
        ),
    ),
}


class StationSidoMismatch(ValueError):
    """원천 측정 행의 시도가 anchor의 시도와 다르다(결정적 대조 실패)."""


def measurement_sido_names(point: Mapping[str, Any]) -> tuple[str, ...]:
    """측정소를 찾을 시도별 bulk 묶음들. 알 수 없으면 빈 tuple(개별 조회 경로)."""
    address = str(point.get("address") or "").strip()
    token = address.split()[0] if address else ""
    for prefix, names in _MERGED_SIDO_PREFIXES:
        if token.startswith(prefix):
            return names
    sido = normalize_sido_name(str(point.get("sido_name") or address))
    return (sido,) if sido else ()


def anchor_sido_names(point: Mapping[str, Any]) -> tuple[str, ...]:
    """anchor가 속한 시도 기대값. 두 묶음을 묻는 통합 주소 anchor는 원천 행의 ``sidoName``이
    이 안에 있어야 붙일 수 있다.

    통합 주소는 두 번째 토큰으로 시도 하나를 가린다. 가리지 못하면 통합 전 후보 시도
    전부를 돌려준다(이때는 호출부가 묶음 응답 사이의 같은 이름을 대조한다). 통합 주소가
    아니면 ``measurement_sido_names``와 같다. 이때 bulk 호출부는 행 시도를 대조하지 않고
    요청한 묶음의 시도로 판단한다. 저장된 ``sido_name`` metadata는 통합 주소에서 첫 토큰
    접두어로 정해진 값이라 쓰지 않는다.
    """
    tokens = str(point.get("address") or "").split()
    token = tokens[0] if tokens else ""
    for prefix, names in _MERGED_SIDO_PREFIXES:
        if token.startswith(prefix):
            district = tokens[1] if len(tokens) > 1 else ""
            for sido, districts in _MERGED_DISTRICT_SIDOS.get(prefix, ()):
                if district in districts:
                    return (sido,)
            return names
    return measurement_sido_names(point)


def measurement_sido_name(measurement: Any) -> str | None:
    """원천 측정 행의 시도(``sidoName``)를 짧은 시도명으로. 행에 없으면 None.

    ``python-airkorea-api``는 ``sidoName``을 ``AirQualityMeasurement.sido_name``으로 옮긴다.
    통합 명칭처럼 한 시도로 줄일 수 없는 값은 그대로 돌려줘 대조 실패로 드러낸다.
    """
    value = getattr(measurement, "sido_name", None)
    if value is None:
        raw = getattr(measurement, "raw", None)
        if isinstance(raw, Mapping):
            value = raw.get("sidoName")
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    if any(text.startswith(prefix) for prefix, _ in _MERGED_SIDO_PREFIXES):
        return text
    return normalize_sido_name(text) or text


def _slug(value: str) -> str:
    """Return an ASCII-safe, deterministic station suffix.

    AirKorea's public station list does not consistently expose a station
    code.  Korean names are not valid ``WeatherLocation.location_id`` values,
    and names alone collide (there are multiple ``중구`` stations), so retain a
    readable ASCII slug only when possible and append a stable digest for
    non-ASCII/ambiguous names at the call site.
    """
    normalized = re.sub(r"[^0-9A-Za-z]+", "-", value.strip().lower()).strip("-")
    return normalized or "station"


def normalize_sido_name(value: str | None) -> str | None:
    """Normalize an AirKorea address/SIDO token to the API's short name."""
    if not value:
        return None
    token = value.strip().split()[0] if value.strip() else ""
    for alias, canonical in _SIDO_ALIASES:
        if token == alias or token.startswith(alias):
            return canonical
    return None


def station_code(station: Station) -> str | None:
    """Return an optional AirKorea station id when present in the raw row."""
    raw = station.raw
    for key in ("stationCode", "stationId", "stationNo", "dmCode"):
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def station_location(station: Station) -> WeatherLocation | None:
    """Convert an AirKorea station with WGS84 coordinates to a safe anchor."""
    if station.lat is None or station.lon is None:
        return None
    code = station_code(station)
    suffix = code or (
        f"{_slug(station.station_name)}-"
        f"{hashlib.sha256(f'{station.station_name}|{station.addr}|{station.lat}|{station.lon}'.encode()).hexdigest()[:12]}"
    )
    metadata: dict[str, Any] = {
        "measurement_point": {
            "provider": AIRKOREA_PROVIDER,
            "station_id": code,
            "station_name": station.station_name,
            "address": station.addr,
            "sido_name": normalize_sido_name(station.addr),
            "network": station.mang_name,
        }
    }
    return WeatherLocation(
        location_id=f"airkorea-{suffix}",
        name=station.station_name,
        latitude=station.lat,
        longitude=station.lon,
        # ``region_code`` is a compact catalog field, while the complete
        # address remains in the allow-listed measurement_point metadata.
        # AirKorea occasionally returns a long building/site description that
        # exceeds WeatherLocation's 32-character region-code contract.
        region_code=(station.addr or "").strip()[:32] or None,
        metadata=metadata,
    )


def station_source_record(station: Station, *, fetched_at: datetime) -> dict[str, Any]:
    """Build a deterministic station-catalog lineage record."""
    raw = dict(station.raw)
    canonical = json.dumps(
        raw, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    key = "sr_" + hashlib.sha256(
        f"{AIRKOREA_STATION_DATASET}:{canonical}".encode()
    ).hexdigest()[:48]
    return {
        "source_record_key": key,
        "provider": AIRKOREA_PROVIDER,
        "dataset_key": AIRKOREA_STATION_DATASET,
        "source_entity_type": "airkorea_station",
        "source_entity_id": station_code(station) or station.station_name,
        "payload": {"station": raw},
        "fetched_at": fetched_at,
    }


_MEASUREMENT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("PM10", "pm10_value", "ug/m3"),
    ("PM25", "pm25_value", "ug/m3"),
    ("O3", "o3_value", "ppm"),
    ("NO2", "no2_value", "ppm"),
    ("SO2", "so2_value", "ppm"),
    ("CO", "co_value", "ppm"),
    ("KHAI", "khai_value", "index"),
)


def measurement_source_record(
    measurement: AirQualityMeasurement,
    *,
    location_id: str,
    fetched_at: datetime,
) -> dict[str, Any]:
    raw = dict(measurement.raw)
    canonical = json.dumps(
        raw, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    key = "sr_" + hashlib.sha256(
        f"{AIRKOREA_MEASUREMENT_DATASET}:{location_id}:{canonical}".encode()
    ).hexdigest()[:48]
    return {
        "source_record_key": key,
        "provider": AIRKOREA_PROVIDER,
        "dataset_key": AIRKOREA_MEASUREMENT_DATASET,
        "source_entity_type": "airkorea_station",
        "source_entity_id": location_id,
        "payload": {"measurement": raw},
        "fetched_at": fetched_at,
    }


def measurement_to_weather_values(
    measurement: AirQualityMeasurement,
    *,
    location_id: str,
    source_record_key: str,
    known_at: datetime,
) -> list[WeatherValue]:
    observed_at = measurement.data_time or known_at
    values: list[WeatherValue] = []
    for metric, attribute, unit in _MEASUREMENT_FIELDS:
        raw_value = getattr(measurement, attribute)
        if raw_value is None:
            continue
        values.append(
            WeatherValue(
                location_id=location_id,
                provider=AIRKOREA_PROVIDER,
                dataset_key=AIRKOREA_MEASUREMENT_DATASET,
                weather_domain="air_quality",
                forecast_style=ForecastStyle.OBSERVED,
                metric_key=metric,
                metric_name={
                    "PM10": "미세먼지 PM10",
                    "PM25": "초미세먼지 PM2.5",
                    "O3": "오존",
                    "NO2": "이산화질소",
                    "SO2": "아황산가스",
                    "CO": "일산화탄소",
                    "KHAI": "통합대기환경지수",
                }[metric],
                source_metric_key=attribute,
                value_number=Decimal(str(raw_value)),
                unit=unit,
                observed_at=observed_at,
                target_at=observed_at,
                known_at=known_at,
                collected_at=known_at,
                payload={
                    "station_name": measurement.station_name,
                    "data_time": measurement.data_time.isoformat()
                    if measurement.data_time
                    else None,
                    "raw_metric": attribute,
                },
                source_record_key=source_record_key,
            )
        )
    return values


async def fetch_station_catalog(
    client: AirKoreaClient,
    *,
    max_stations: int = 1000,
) -> list[tuple[WeatherLocation, dict[str, Any]]]:
    """Fetch and normalize a bounded, paginated AirKorea station catalog.

    The public endpoint currently caps ``numOfRows`` at a provider-defined
    page size (100).  Walking pages prevents a successful first response from
    silently dropping the rest of the nationwide station catalog.

    ``AirKoreaClient`` became async-only (the separate ``AsyncAirKoreaClient``
    it used to disambiguate from is gone, and so are the sync methods); the
    caller owns the event loop for the whole run, not one loop per call --
    the client's shared rate limiter binds to whichever loop first calls
    ``acquire()`` and raises if a later call arrives from a different one.
    """
    page_size = min(100, max_stations)
    stations: list[Station] = []
    page_no = 1
    while len(stations) < max_stations:
        page = await client.stations(page_no=page_no, num_of_rows=page_size)
        if not page:
            break
        remaining = max_stations - len(stations)
        stations.extend(page[:remaining])
        if len(page) < page_size:
            break
        page_no += 1
    fetched_at = datetime.now(UTC)
    result: list[tuple[WeatherLocation, dict[str, Any]]] = []
    for station in stations[:max_stations]:
        location = station_location(station)
        if location is None:
            continue
        result.append((location, station_source_record(station, fetched_at=fetched_at)))
    return result


async def fetch_sido_measurements(
    client: AirKoreaClient,
    *,
    sido_name: str,
    max_stations: int = 1000,
    page_size: int = 100,
) -> list[AirQualityMeasurement]:
    """Fetch a bounded SIDO measurement catalog using the bulk API.

    AirKorea's station endpoint is request-quota expensive when called once
    per station.  ``getCtprvnRltmMesureDnsty`` returns the latest row for all
    stations in a SIDO, so one hourly run needs roughly one request per page
    instead of hundreds.  The client exposes rows rather than page metadata;
    stop on a short page or when a provider repeats a page to avoid an
    accidental infinite loop.
    """
    if max_stations <= 0 or page_size <= 0:
        raise ValueError("AirKorea measurement budget은 양수여야 합니다.")
    bulk = getattr(client, "sido_measurements", None)
    if not callable(bulk):
        return []
    requested_page_size = min(page_size, max_stations)
    measurements: list[AirQualityMeasurement] = []
    seen_names: set[str] = set()
    page_no = 1
    # max_stations/page_size is the normal bound; add one page to tolerate a
    # provider that returns fewer rows than requested without metadata.
    max_pages = (max_stations + requested_page_size - 1) // requested_page_size + 1
    while len(measurements) < max_stations and page_no <= max_pages:
        page = list(
            await bulk(
                sido_name,
                page_no=page_no,
                num_of_rows=requested_page_size,
            )
        )
        if not page:
            break
        new_rows = [
            measurement
            for measurement in page
            if measurement.station_name.strip() not in seen_names
        ]
        if not new_rows:
            break
        remaining = max_stations - len(measurements)
        measurements.extend(new_rows[:remaining])
        seen_names.update(measurement.station_name.strip() for measurement in new_rows)
        if len(page) < requested_page_size:
            break
        page_no += 1
    return measurements[:max_stations]


async def fetch_station_measurement(
    client: AirKoreaClient,
    *,
    station_name: str,
    location_id: str,
    known_at: datetime,
    expected_sido: str | None = None,
) -> tuple[dict[str, Any], list[WeatherValue]] | None:
    measurement = await client.latest_station_measurement(station_name)
    if measurement is None:
        return None
    # ``stationName`` is the only selector exposed by python-airkorea-api.
    # Validate the returned identity before attaching it to an anchor so a
    # duplicate name from another province cannot silently cross-contaminate
    # the catalog.  Callers may pass the first address token as an additional
    # guard; a mismatch is quarantined (None) rather than published.
    if measurement.station_name.strip() != station_name.strip():
        raise ValueError(
            f"AirKorea 측정소 응답 이름이 요청과 다릅니다: "
            f"expected={station_name!r} got={measurement.station_name!r}"
        )
    if expected_sido and measurement.sido_name:
        returned_sido = measurement.sido_name.strip()
        if returned_sido and returned_sido != expected_sido.strip():
            raise StationSidoMismatch(
                "AirKorea 측정소 응답 시도가 요청 anchor와 다릅니다: "
                f"expected={expected_sido!r} got={returned_sido!r}"
            )
    source = measurement_source_record(
        measurement,
        location_id=location_id,
        fetched_at=known_at,
    )
    return source, measurement_to_weather_values(
        measurement,
        location_id=location_id,
        source_record_key=source["source_record_key"],
        known_at=known_at,
    )


def measurement_point_metadata(location: WeatherLocation) -> Mapping[str, Any] | None:
    value = location.metadata.get("measurement_point")
    return value if isinstance(value, Mapping) else None
