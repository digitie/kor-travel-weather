from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from airkorea.models import Station
from kortravelweather_dagster import airkorea_weather

from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.providers.airkorea import (
    fetch_sido_measurements,
    fetch_station_catalog,
    fetch_station_measurement,
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
        ("겹침", f"{merged} 서구 어딘가 3"),  # 두 응답 모두에 있음
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
