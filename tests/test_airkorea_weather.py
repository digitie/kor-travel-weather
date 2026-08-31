from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from airkorea.models import Station
from kortravelweather_dagster import airkorea_weather

from kortravelweather.models import WeatherLocation, WeatherValue
from kortravelweather.providers.airkorea import (
    fetch_station_catalog,
    fetch_station_measurement,
    station_location,
)
from kortravelweather.providers.kma import weather_warning_to_weather_values


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


class _PagedStationsClient:
    def __init__(self, pages: dict[int, list[Station]]) -> None:
        self.pages = pages
        self.calls: list[tuple[int, int]] = []

    def stations(self, *, page_no: int, num_of_rows: int) -> list[Station]:
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

    result = fetch_station_catalog(client, max_stations=150)

    assert len(result) == 150
    assert client.calls == [(1, 100), (2, 100)]
    assert len({location.location_id for location, _ in result}) == 150


def test_airkorea_measurement_identity_mismatch_is_rejected() -> None:
    class _MismatchedClient:
        def latest_station_measurement(self, station_name: str):
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
        fetch_station_measurement(
            _MismatchedClient(),
            station_name="종로구",
            location_id="airkorea-jongno",
            known_at=datetime.now(UTC),
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

        def publish_and_finish(self, *, run_id, source_records, values, error=None, **kwargs):
            self.published.append((run_id, source_records, values, error))
            return len(values), SimpleNamespace(run_id=run_id, status="success")

        def finish_sync_run(self, *args, **kwargs):
            raise AssertionError("station-level failures must not abort the run")

    class FakeClient:
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

    def fake_fetch_measurement(client, *, station_name, location_id, **kwargs):
        if station_name == "Limited":
            raise RuntimeError("provider rate limit")
        return ({"source_record_key": "measurement-good"}, values)

    monkeypatch.setattr(airkorea_weather, "fetch_station_catalog", lambda *args, **kwargs: catalog)
    monkeypatch.setattr(airkorea_weather, "fetch_station_measurement", fake_fetch_measurement)

    repository = FakeRepository()
    result = airkorea_weather.run_airkorea_weather_sync(
        repository=repository, client=FakeClient(), max_stations=2, max_values=10
    )

    assert result["status"] == "success"
    assert result["stations_failed"] == 1
    assert result["values_loaded"] == 1
    assert repository.published[-1][2] == values
    assert "1개 측정소 요청 실패" in (repository.published[-1][3] or "")
