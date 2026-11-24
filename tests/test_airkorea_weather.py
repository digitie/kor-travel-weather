from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from airkorea.client import parse_measurement
from airkorea.models import Station
from kortravelweather_dagster import airkorea_weather

from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.providers.airkorea import (
    StationSidoMismatch,
    anchor_sido_names,
    fetch_sido_measurements,
    fetch_station_catalog,
    fetch_station_measurement,
    measurement_sido_name,
    measurement_sido_names,
    normalize_sido_name,
    station_location,
)
from kortravelweather.providers.kma import weather_warning_to_weather_values


async def _resolved(value):
    """Wrap an already-known value as a coroutine.

    ``AirKoreaClient`` became async-only, so every fake below stands in for a
    coroutine function; ``lambda *a, **k: _resolved(value)`` gives a
    monkeypatch target the same shape without every test spelling out its own
    ``async def``.
    """
    return value


class _EntersAndCloses:
    """Mixin so a fake client survives ``async with client:``."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _station(name: str, address: str, lat: float, lon: float) -> Station:
    return Station(
        station_name=name,
        addr=address,
        year=2024,
        mang_name="도시대기",
        item="PM10",
        lat=lat,
        lon=lon,
        raw={"stationName": name, "addr": address, "dmX": str(lat), "dmY": str(lon)},
    )


def test_airkorea_station_ids_are_ascii_and_collision_resistant() -> None:
    first = station_location(_station("중구", "서울 중구", 37.56, 126.97))
    second = station_location(_station("중구", "부산 중구", 35.1, 129.03))
    assert first is not None and second is not None
    assert first.location_id != second.location_id
    assert first.location_id.isascii() and second.location_id.isascii()


def test_airkorea_long_station_address_stays_in_metadata() -> None:
    address = "전남 광주 통합 특별 관측소 노인당 옥상 측정 지점"
    location = station_location(_station("광주 관측소", address, 35.15, 126.85))
    assert location is not None
    assert len(location.region_code or "") <= 32
    assert location.metadata["measurement_point"]["address"] == address


def test_airkorea_normalizes_sido_aliases_for_bulk_measurements() -> None:
    assert normalize_sido_name("경상북도 경산시") == "경북"
    assert normalize_sido_name("서울특별시 중구") == "서울"
    assert normalize_sido_name("unknown province") is None


def test_airkorea_bulk_measurements_walk_pages_and_stop_on_repeat() -> None:
    class _BulkClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, int]] = []

        async def sido_measurements(self, sido_name: str, *, page_no: int, num_of_rows: int):
            self.calls.append((sido_name, page_no, num_of_rows))
            row = SimpleNamespace(station_name=f"Station {page_no}")
            return [row] if page_no < 3 else [row]

    client = _BulkClient()
    rows = asyncio.run(
        fetch_sido_measurements(client, sido_name="서울", max_stations=3, page_size=1)
    )

    assert [row.station_name for row in rows] == ["Station 1", "Station 2", "Station 3"]
    assert client.calls == [("서울", 1, 1), ("서울", 2, 1), ("서울", 3, 1)]


def test_kma_warning_rows_have_distinct_fact_keys() -> None:
    rows = [
        {"stnId": "108", "tmFc": "202608311200", "tmSeq": "1", "title": "호우주의보"},
        {"stnId": "108", "tmFc": "202608311200", "tmSeq": "2", "title": "강풍주의보"},
    ]
    values = weather_warning_to_weather_values(
        rows, location_id="kma-alert:108", source_record_key="response-key"
    )
    assert len(values) == 2
    assert len({value.identity_key() for value in values}) == 2
    assert {value.severity for value in values} == {"watch"}


def test_kma_warning_identifier_types_are_canonicalized() -> None:
    numeric = weather_warning_to_weather_values(
        [{"stnId": 108, "tmFc": 202609040300, "tmSeq": 22, "title": "강풍주의보 발표"}],
        location_id="kma-alert:108",
    )[0]
    textual = weather_warning_to_weather_values(
        [{"stnId": "108", "tmFc": "202609040300", "tmSeq": "22", "title": "강풍주의보 발표"}],
        location_id="kma-alert:108",
    )[0]

    assert numeric.source_record_key == textual.source_record_key
    assert numeric.payload == textual.payload


class _PagedStationsClient:
    def __init__(self, pages: dict[int, list[Station]]) -> None:
        self.pages = pages
        self.calls: list[tuple[int, int]] = []

    async def stations(self, *, page_no: int, num_of_rows: int) -> list[Station]:
        self.calls.append((page_no, num_of_rows))
        return self.pages.get(page_no, [])


def test_airkorea_catalog_walks_pages_with_a_hard_station_cap() -> None:
    first = [
        _station(f"Station {index}", f"서울 {index}", 37 + index / 1000, 127)
        for index in range(100)
    ]
    second = [
        _station(f"Station {index + 100}", f"서울 {index + 100}", 37 + index / 1000, 127)
        for index in range(100)
    ]
    client = _PagedStationsClient({1: first, 2: second})

    result = asyncio.run(fetch_station_catalog(client, max_stations=150))

    assert len(result) == 150
    assert client.calls == [(1, 100), (2, 100)]
    assert len({location.location_id for location, _ in result}) == 150


def test_airkorea_measurement_identity_mismatch_is_rejected() -> None:
    class _MismatchedClient:
        async def latest_station_measurement(self, station_name: str):
            class Measurement:
                station_name = "다른 측정소"
                sido_name = "서울"
                data_time = None
                raw = {}
                pm10_value = None
                pm25_value = None
                o3_value = None
                no2_value = None
                so2_value = None
                co_value = None
                khai_value = None

            return Measurement()

    with pytest.raises(ValueError, match="응답 이름"):
        asyncio.run(
            fetch_station_measurement(
                _MismatchedClient(),
                station_name="종로구",
                location_id="airkorea-jongno",
                known_at=datetime.now(UTC),
            )
        )


def test_airkorea_measurement_failure_keeps_successful_stations(monkeypatch) -> None:
    locations = [
        WeatherLocation(
            location_id=f"airkorea-{name.lower()}",
            name=name,
            latitude=37.5 + index / 100,
            longitude=127.0,
            metadata={"measurement_point": {"station_name": name, "address": "서울"}},
        )
        for index, name in enumerate(("Good", "Limited"))
    ]
    catalog = [
        (
            location,
            {
                "source_record_key": f"catalog-{location.location_id}",
                "provider": "python-airkorea-api",
                "dataset_key": "airkorea_station_catalog",
                "source_entity_type": "airkorea_station",
                "source_entity_id": location.location_id,
                "payload": {},
                "fetched_at": datetime.now(UTC),
            },
        )
        for location in locations
    ]

    class FakeRepository:
        def __init__(self) -> None:
            self.runs: list[SimpleNamespace] = []
            self.published: list[tuple[str, list[dict], list[WeatherValue], str | None]] = []
            self.ingested: list[WeatherValue] = []

        def start_sync_run(self, *, provider: str, dataset_key: str, locations_total: int):
            run = SimpleNamespace(
                run_id=f"run-{len(self.runs)}", status="running", locations_total=locations_total
            )
            self.runs.append(run)
            return run

        def get_location(self, location_id: str):
            return next(
                (location for location in locations if location.location_id == location_id), None
            )

        def create_location(self, location):
            return location

        def heartbeat_sync_run(self, run_id: str) -> bool:
            return True

        def ingest_batch(self, *, source_records, values):
            # Measurements publish in location chunks before the finish.
            self.ingested.extend(values)
            return len(values)

        def publish_and_finish(self, *, run_id, source_records, values, error=None, **kwargs):
            self.published.append((run_id, source_records, values, error))
            loaded = len(values) + kwargs.get("values_loaded_offset", 0)
            return loaded, SimpleNamespace(run_id=run_id, status="success")

        def finish_sync_run(self, *args, **kwargs):
            raise AssertionError("station-level failures must not abort the run")

    class FakeClient(_EntersAndCloses):
        pass

    values = [
        WeatherValue(
            location_id=locations[0].location_id,
            provider="python-airkorea-api",
            dataset_key="airkorea_realtime_measurement",
            weather_domain="air_quality",
            forecast_style="observed",
            metric_key="PM10",
            value_number=12,
            target_at=datetime.now(UTC),
            source_record_key="measurement-good",
        )
    ]

    async def fake_fetch_measurement(client, *, station_name, location_id, **kwargs):
        if station_name == "Limited":
            raise RuntimeError("provider rate limit")
        return ({"source_record_key": "measurement-good"}, values)

    monkeypatch.setattr(
        airkorea_weather, "fetch_station_catalog", lambda *a, **k: _resolved(catalog)
    )
    monkeypatch.setattr(airkorea_weather, "fetch_station_measurement", fake_fetch_measurement)

    repository = FakeRepository()
    result = airkorea_weather.run_airkorea_weather_sync(
        repository=repository, client=FakeClient(), max_stations=2, max_values=10
    )

    # native run은 success(받은 측정소는 승격), Dagster 결과는 partial이다.
    assert result["status"] == "partial"
    assert result["stations_failed"] == 1
    assert result["stations_failed_transient"] == 1
    assert result["stations_failed_deterministic"] == 0
    assert result["station_failure_reasons"] == {"request_error": 1}
    assert result["failed_station_sample"] == ["airkorea-limited"]
    assert result["values_loaded"] == 1
    assert repository.ingested == values
    assert repository.published[-1][2] == []
    error = repository.published[-1][3] or ""
    assert "1개 측정소 수집 실패" in error and "request_error=1" in error
    assert "RuntimeError" in error


def test_airkorea_sync_uses_sido_bulk_endpoint(monkeypatch) -> None:
    locations = [
        WeatherLocation(
            location_id=f"airkorea-{name.lower()}",
            name=name,
            latitude=37.5 + index / 100,
            longitude=127.0,
            metadata={"measurement_point": {"station_name": name, "address": "서울"}},
        )
        for index, name in enumerate(("Good", "Missing"))
    ]
    catalog = [
        (
            location,
            {
                "source_record_key": f"catalog-{location.location_id}",
                "provider": "python-airkorea-api",
                "dataset_key": "airkorea_station_catalog",
                "source_entity_type": "airkorea_station",
                "source_entity_id": location.location_id,
                "payload": {},
                "fetched_at": datetime.now(UTC),
            },
        )
        for location in locations
    ]

    class FakeRepository:
        def __init__(self) -> None:
            self.runs: list[SimpleNamespace] = []
            self.published: list[tuple[str, list[dict], list[WeatherValue], str | None]] = []
            self.ingested: list[WeatherValue] = []

        def start_sync_run(self, *, provider: str, dataset_key: str, locations_total: int):
            run = SimpleNamespace(
                run_id=f"run-{len(self.runs)}", status="running", locations_total=locations_total
            )
            self.runs.append(run)
            return run

        def get_location(self, location_id: str):
            return next(
                (location for location in locations if location.location_id == location_id), None
            )

        def create_location(self, location):
            return location

        def heartbeat_sync_run(self, run_id: str) -> bool:
            return True

        def ingest_batch(self, *, source_records, values):
            # Measurements publish in location chunks before the finish.
            self.ingested.extend(values)
            return len(values)

        def publish_and_finish(self, *, run_id, source_records, values, error=None, **kwargs):
            self.published.append((run_id, source_records, values, error))
            loaded = len(values) + kwargs.get("values_loaded_offset", 0)
            return loaded, SimpleNamespace(run_id=run_id, status="success")

        def finish_sync_run(self, *args, **kwargs):
            raise AssertionError("bulk run should not need terminal error handling")

    def _measurement() -> SimpleNamespace:
        return SimpleNamespace(
            station_name="Good",
            data_time=None,
            raw={"stationName": "Good", "sidoName": "서울"},
            pm10_value=12.0,
            pm25_value=None,
            o3_value=None,
            no2_value=None,
            so2_value=None,
            co_value=None,
            khai_value=None,
        )

    class FakeClient(_EntersAndCloses):
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, int]] = []

        async def sido_measurements(self, sido_name: str, *, page_no: int, num_of_rows: int):
            self.calls.append((sido_name, page_no, num_of_rows))
            return [_measurement()]

        async def latest_station_measurement(self, station_name: str):
            raise AssertionError("bulk-capable client must not issue station requests")

    monkeypatch.setattr(
        airkorea_weather, "fetch_station_catalog", lambda *a, **k: _resolved(catalog)
    )
    client = FakeClient()
    repository = FakeRepository()

    result = airkorea_weather.run_airkorea_weather_sync(
        repository=repository, client=client, max_stations=2, max_values=10
    )

    assert client.calls == [("서울", 1, 2)]
    assert result["requests_fetched"] == 2  # catalog + one SIDO request
    assert result["values_loaded"] == 1
    assert result["stations_failed"] == 1
    assert result["status"] == "partial"
    assert result["station_failure_reasons"] == {"not_in_response": 1}
    assert result["stations_failed_deterministic"] == 1
    assert result["failed_station_sample"] == ["airkorea-missing"]


def test_merged_gwangju_address_queries_both_bulk_groups() -> None:
    # 2026-10 이후 AirKorea 측정소 목록의 광주 도심 주소. 첫 토큰이 "전남"으로 시작한다.
    merged = {"address": "전남광주통합특별시 북구 군왕로 141번길 6", "sido_name": "전남"}
    assert normalize_sido_name(merged["address"]) == "전남"
    assert measurement_sido_names(merged) == ("광주", "전남")
    assert measurement_sido_names({"address": "서울 중구", "sido_name": "서울"}) == ("서울",)
    assert measurement_sido_names({"address": "경기도 수원시"}) == ("경기",)
    assert measurement_sido_names({"address": "unknown"}) == ()


def test_merged_gwangju_stations_are_matched_from_either_group(monkeypatch) -> None:
    """운영 결함 재현: 통합 주소 측정소가 전남 응답에 없어도 광주 응답에서 맞춘다."""
    merged = "전남광주통합특별시"
    specs = (
        ("두암동", f"{merged} 북구 군왕로 141번길 6"),  # 광주 묶음에만 응답
        ("중동", f"{merged} 목포시 어딘가 1"),  # 전남 묶음에만 응답
        ("없는곳", f"{merged} 남구 어딘가 2"),  # 어느 응답에도 없음
        # 두 응답 모두에 같은 이름이 있다. 서구는 광주라 광주 행만 붙고 전남 행은 시도
        # 대조로 거른다(같은 anchor에 한 번만 쓴다).
        ("겹침", f"{merged} 서구 어딘가 3"),
    )
    locations = [
        WeatherLocation(
            location_id=f"airkorea-{index}",
            name=name,
            latitude=35.1 + index / 100,
            longitude=126.9,
            metadata={"measurement_point": {
                "station_name": name, "address": address, "sido_name": "전남",
            }},
        )
        for index, (name, address) in enumerate(specs)
    ]
    catalog = [
        (location, {
            "source_record_key": f"catalog-{location.location_id}",
            "provider": "python-airkorea-api",
            "dataset_key": "airkorea_station_catalog",
            "source_entity_type": "airkorea_station",
            "source_entity_id": location.location_id,
            "payload": {},
            "fetched_at": datetime.now(UTC),
        })
        for location in locations
    ]

    class FakeRepository:
        def __init__(self) -> None:
            self.ingested_sources: list[str] = []
            self.published: list[str | None] = []

        def start_sync_run(self, *, provider: str, dataset_key: str, locations_total: int):
            return SimpleNamespace(run_id=f"run-{dataset_key}", status="running")

        def get_location(self, location_id: str):
            return next(item for item in locations if item.location_id == location_id)

        def create_location(self, location):
            return location

        def heartbeat_sync_run(self, run_id: str) -> bool:
            return True

        def ingest_batch(self, *, source_records, values):
            self.ingested_sources.extend(record["source_entity_id"] for record in source_records)
            return len(values)

        def publish_and_finish(self, *, run_id, source_records, values, error=None, **kwargs):
            # catalog run의 source는 측정 기록이 아니므로 measurement run만 센다.
            if run_id == "run-airkorea_realtime_measurement":
                self.ingested_sources.extend(
                    record["source_entity_id"] for record in source_records
                )
            self.published.append(error)
            return kwargs.get("values_loaded_offset", 0), SimpleNamespace(status="success")

        def finish_sync_run(self, *args, **kwargs):
            raise AssertionError("station-level failures must not abort the run")

    def row(name: str, sido: str) -> SimpleNamespace:
        return SimpleNamespace(
            station_name=name, data_time=None, raw={"stationName": name, "sidoName": sido},
            pm10_value=10.0, pm25_value=None, o3_value=None, no2_value=None,
            so2_value=None, co_value=None, khai_value=None,
        )

    class FakeClient(_EntersAndCloses):
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def sido_measurements(self, sido_name: str, *, page_no: int, num_of_rows: int):
            self.calls.append(sido_name)
            if sido_name == "광주":
                return [row("두암동", "광주"), row("겹침", "광주")]
            return [row("중동", "전남"), row("겹침", "전남")]

        async def latest_station_measurement(self, station_name: str):
            raise AssertionError("bulk-capable client must not issue station requests")

    monkeypatch.setattr(
        airkorea_weather, "fetch_station_catalog", lambda *a, **k: _resolved(catalog)
    )
    client = FakeClient()
    repository = FakeRepository()
    result = airkorea_weather.run_airkorea_weather_sync(
        repository=repository, client=client, max_stations=10, max_values=100
    )

    assert sorted(client.calls) == ["광주", "전남"]
    assert result["status"] == "partial"
    assert result["stations_failed"] == 1
    assert result["station_failure_reasons"] == {"not_in_response": 1}
    assert result["failed_station_sample"] == ["airkorea-2"]
    # 두 응답에 모두 나온 측정소는 한 번만 쓴다.
    assert sorted(repository.ingested_sources) == ["airkorea-0", "airkorea-1", "airkorea-3"]
    assert "not_in_response=1" in (repository.published[-1] or "")


_MERGED = "전남광주통합특별시"


def test_anchor_sido_names_resolve_merged_district() -> None:
    # 통합 주소는 두 번째 토큰(자치구·시·군)으로 통합 전 시도 하나를 가린다.
    assert anchor_sido_names({"address": f"{_MERGED} 북구 군왕로 141번길 6"}) == ("광주",)
    assert anchor_sido_names({"address": f"{_MERGED} 광산구 어딘가 1"}) == ("광주",)
    assert anchor_sido_names({"address": f"{_MERGED} 목포시 어딘가 1"}) == ("전남",)
    assert anchor_sido_names({"address": f"{_MERGED} 화순군 어딘가 1"}) == ("전남",)
    # 저장된 sido_name(첫 토큰 접두어로 정해진 "전남")은 통합 주소에서 쓰지 않는다.
    assert anchor_sido_names(
        {"address": f"{_MERGED} 서구 어딘가 1", "sido_name": "전남"}
    ) == ("광주",)
    # 가리지 못하면 통합 전 후보 전부. 묻는 묶음은 가린 뒤에도 그대로 둘이다.
    assert anchor_sido_names({"address": f"{_MERGED} 미상구 어딘가 1"}) == ("광주", "전남")
    assert anchor_sido_names({"address": _MERGED}) == ("광주", "전남")
    assert measurement_sido_names({"address": f"{_MERGED} 북구 어딘가 1"}) == ("광주", "전남")
    # 통합 주소가 아니면 기존 정규화와 같다.
    assert anchor_sido_names({"address": "서울특별시 중구", "sido_name": "서울"}) == ("서울",)
    assert anchor_sido_names({"address": "unknown"}) == ()


def test_measurement_sido_name_reads_source_sido_field() -> None:
    # python-airkorea-api는 원천 ``sidoName``을 ``sido_name``으로 옮긴다.
    row = parse_measurement({"stationName": "가", "sidoName": " 광주 "})
    assert measurement_sido_name(row) == "광주"
    assert measurement_sido_name(parse_measurement({"stationName": "가"})) is None
    # 모델 속성이 없는 행은 raw의 sidoName을 본다.
    assert measurement_sido_name(SimpleNamespace(raw={"sidoName": "전남"})) == "전남"
    assert measurement_sido_name(SimpleNamespace(sido_name="광주광역시", raw={})) == "광주"
    assert measurement_sido_name(SimpleNamespace(sido_name="", raw={})) is None
    # 통합 명칭은 어느 한 시도로 줄이지 않는다(대조 실패로 드러난다).
    assert measurement_sido_name(SimpleNamespace(sido_name=_MERGED, raw={})) == _MERGED


def _anchor(index: int, name: str, address: str) -> WeatherLocation:
    return WeatherLocation(
        location_id=f"airkorea-{index}",
        name=name,
        latitude=35.1 + index / 100,
        longitude=126.9,
        metadata={"measurement_point": {
            "station_name": name,
            "address": address,
            # 운영 anchor와 같게 첫 토큰 접두어로 정한 값을 저장해 둔다(통합 주소는 "전남").
            "sido_name": normalize_sido_name(address),
        }},
    )


def _row(name: str, sido: str | None, pm10: float) -> object:
    raw: dict[str, str] = {"stationName": name, "pm10Value": str(pm10)}
    if sido is not None:
        raw["sidoName"] = sido
    return parse_measurement(raw)


class _MeasurementRecorder:
    """측정 run의 적재 값과 오류 문자열만 남기는 repository."""

    def __init__(self, locations: list[WeatherLocation]) -> None:
        self.locations = locations
        self.values: list[WeatherValue] = []
        self.errors: list[str | None] = []

    def start_sync_run(self, *, provider: str, dataset_key: str, locations_total: int):
        return SimpleNamespace(run_id=f"run-{dataset_key}", status="running")

    def get_location(self, location_id: str):
        return next(item for item in self.locations if item.location_id == location_id)

    def create_location(self, location):
        return location

    def heartbeat_sync_run(self, run_id: str) -> bool:
        return True

    def ingest_batch(self, *, source_records, values):
        self.values.extend(values)
        return len(values)

    def publish_and_finish(self, *, run_id, source_records, values, error=None, **kwargs):
        if run_id == "run-airkorea_realtime_measurement":
            self.errors.append(error)
        return kwargs.get("values_loaded_offset", 0), SimpleNamespace(status="success")

    def finish_sync_run(self, *args, **kwargs):
        raise AssertionError("station-level failures must not abort the run")

    def pm10_by_location(self) -> dict[str, float]:
        return {
            value.location_id: float(value.value_number)
            for value in self.values
            if value.metric_key == "PM10"
        }


def _run_bulk_sync(
    monkeypatch,
    locations: list[WeatherLocation],
    responses: dict[str, list[object] | Exception],
) -> tuple[dict, _MeasurementRecorder]:
    catalog = [
        (location, {
            "source_record_key": f"catalog-{location.location_id}",
            "provider": "python-airkorea-api",
            "dataset_key": "airkorea_station_catalog",
            "source_entity_type": "airkorea_station",
            "source_entity_id": location.location_id,
            "payload": {},
            "fetched_at": datetime.now(UTC),
        })
        for location in locations
    ]

    class FakeClient(_EntersAndCloses):
        async def sido_measurements(self, sido_name: str, *, page_no: int, num_of_rows: int):
            response = responses.get(sido_name, [])
            if isinstance(response, Exception):
                raise response
            return list(response)

        async def latest_station_measurement(self, station_name: str):
            raise AssertionError("bulk-capable client must not issue station requests")

    monkeypatch.setattr(
        airkorea_weather, "fetch_station_catalog", lambda *a, **k: _resolved(catalog)
    )
    repository = _MeasurementRecorder(locations)
    result = airkorea_weather.run_airkorea_weather_sync(
        repository=repository, client=FakeClient(), max_stations=10, max_values=100
    )
    return result, repository


@pytest.mark.parametrize("row_sido", ["전남", None])
def test_merged_anchor_rejects_same_name_row_from_other_sido(monkeypatch, row_sido) -> None:
    """#282 후속 회귀: 같은 이름의 전남 측정소 anchor가 활성 목록에 없을 때(상한·비활성)
    전남 묶음의 그 관측이 광주 anchor에 붙으면 안 된다. 행에 시도가 없으면 요청한 묶음의
    시도로 대조한다."""
    locations = [
        _anchor(0, "두암동", f"{_MERGED} 북구 군왕로 141번길 6"),
        _anchor(1, "같은이름", f"{_MERGED} 서구 어딘가 2"),
    ]
    result, repository = _run_bulk_sync(monkeypatch, locations, {
        # 광주 묶음에는 이번 시각 "같은이름"이 없다.
        "광주": [_row("두암동", "광주", 31.0)],
        "전남": [_row("같은이름", row_sido, 99.0)],
    })

    assert repository.pm10_by_location() == {"airkorea-0": 31.0}
    assert result["status"] == "partial"
    assert result["stations_failed"] == 1
    assert result["station_failure_reasons"] == {"sido_mismatch": 1}
    assert result["stations_failed_deterministic"] == 1
    assert result["stations_failed_transient"] == 0
    assert result["failed_station_sample"] == ["airkorea-1"]
    assert "sido_mismatch=1" in (repository.errors[-1] or "")


def test_same_name_stations_in_both_sidos_bind_to_their_own_anchor(monkeypatch) -> None:
    # 광주·전남에 같은 이름 측정소가 있고 둘 다 활성이면, 이름만으로는 두 묶음 모두에서
    # 후보가 둘이라 모두 격리됐다. 행의 sidoName으로 각자 제 관측을 받는다.
    locations = [
        _anchor(0, "같은이름", f"{_MERGED} 북구 어딘가 1"),
        _anchor(1, "같은이름", f"{_MERGED} 목포시 어딘가 2"),
    ]
    result, repository = _run_bulk_sync(monkeypatch, locations, {
        "광주": [_row("같은이름", "광주", 11.0)],
        "전남": [_row("같은이름", "전남", 22.0)],
    })

    assert repository.pm10_by_location() == {"airkorea-0": 11.0, "airkorea-1": 22.0}
    assert result["status"] == "success"
    assert result["stations_failed"] == 0


def test_plain_anchor_rejects_row_with_other_sido(monkeypatch) -> None:
    # 통합 주소가 아니어도 원천 행의 시도가 anchor와 다르면 붙이지 않는다.
    locations = [_anchor(0, "중구", "서울특별시 중구 덕수궁길 15")]
    result, repository = _run_bulk_sync(monkeypatch, locations, {
        "서울": [_row("중구", "부산", 40.0)],
    })

    assert repository.pm10_by_location() == {}
    assert result["station_failure_reasons"] == {"sido_mismatch": 1}
    assert result["stations_failed_deterministic"] == 1


@pytest.mark.parametrize(
    ("responses", "expected_pm10", "expected_reasons"),
    [
        # 두 시도 응답 모두에 같은 이름이 있으면 어느 쪽인지 정할 수 없다.
        (
            {"광주": [_row("미상", "광주", 1.0)], "전남": [_row("미상", "전남", 2.0)]},
            {},
            {"ambiguous_name": 1},
        ),
        # 한 시도 응답에만 있으면 붙인다(#282 동작 유지).
        ({"광주": [], "전남": [_row("미상", "전남", 2.0)]}, {"airkorea-0": 2.0}, {}),
        # 다른 묶음을 못 받았으면 거기에도 같은 이름이 있었는지 몰라 붙이지 않는다.
        (
            {"광주": RuntimeError("timeout"), "전남": [_row("미상", "전남", 2.0)]},
            {},
            {"request_error": 1},
        ),
    ],
)
def test_unresolved_merged_anchor_requires_one_sido(
    monkeypatch, responses, expected_pm10, expected_reasons
) -> None:
    locations = [_anchor(0, "미상", f"{_MERGED} 미상구 어딘가 1")]
    result, repository = _run_bulk_sync(monkeypatch, locations, responses)

    assert repository.pm10_by_location() == expected_pm10
    assert result["station_failure_reasons"] == expected_reasons


def test_station_fallback_sido_mismatch_is_deterministic(monkeypatch) -> None:
    # bulk가 없는 client의 측정소별 경로도 시도 대조 실패를 결정적 실패로 남긴다.
    location = _anchor(0, "종로구", "서울 종로구 종로35가길 19")
    catalog = [(location, {
        "source_record_key": "catalog-airkorea-0",
        "provider": "python-airkorea-api",
        "dataset_key": "airkorea_station_catalog",
        "source_entity_type": "airkorea_station",
        "source_entity_id": location.location_id,
        "payload": {},
        "fetched_at": datetime.now(UTC),
    })]

    class StationOnlyClient(_EntersAndCloses):
        async def latest_station_measurement(self, station_name: str):
            return _row(station_name, "부산", 5.0)

    monkeypatch.setattr(
        airkorea_weather, "fetch_station_catalog", lambda *a, **k: _resolved(catalog)
    )
    repository = _MeasurementRecorder([location])
    result = airkorea_weather.run_airkorea_weather_sync(
        repository=repository, client=StationOnlyClient(), max_stations=1, max_values=10
    )

    assert repository.pm10_by_location() == {}
    assert result["station_failure_reasons"] == {"sido_mismatch": 1}
    assert result["stations_failed_deterministic"] == 1
    assert "StationSidoMismatch" in (repository.errors[-1] or "")
    with pytest.raises(StationSidoMismatch):
        asyncio.run(fetch_station_measurement(
            StationOnlyClient(), station_name="종로구", location_id=location.location_id,
            known_at=datetime.now(UTC), expected_sido="서울",
        ))


def test_airkorea_asset_tags_partial_outcome(monkeypatch) -> None:
    from kortravelweather_dagster import definitions

    recorded: list[str] = []
    monkeypatch.setattr(
        definitions, "_record_collection_outcome",
        lambda context, outcome: recorded.append(outcome),
    )
    monkeypatch.setattr(
        definitions, "run_airkorea_weather_sync",
        lambda **kwargs: {
            "provider": "python-airkorea-api", "status": "partial", "stations_failed": 13,
            "stations_failed_transient": 0, "stations_failed_deterministic": 13,
            "station_failure_reasons": {"not_in_response": 13},
            "failed_station_sample": ["airkorea-station-06b41d6c8d39"],
        },
    )
    monkeypatch.setattr(definitions, "_run_repository", lambda context: object())
    metadata: dict = {}
    warnings: list[str] = []
    context = SimpleNamespace(
        resources=SimpleNamespace(
            airkorea_client=SimpleNamespace(create_client=lambda **kwargs: object()),
        ),
        add_output_metadata=metadata.update,
        log=SimpleNamespace(warning=warnings.append, info=lambda *a, **k: None),
    )
    result = definitions.airkorea_weather_sync.op.compute_fn.decorated_fn(context)
    assert result["collection_outcome"] == "partial"
    assert metadata["collection_outcome"] == "partial"
    assert recorded == ["partial"]
    assert warnings and "13곳" in warnings[0]
