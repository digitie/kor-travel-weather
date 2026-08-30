from __future__ import annotations

import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from kma import KmaAuthError, KmaServerError
from kortravelweather_dagster import kma_weather
from kortravelweather_dagster.kma_weather import (
    WeatherTarget,
    _source_spec,
    response_source_key,
    run_weather_sync,
    stage_grid,
    targets_from_settings,
)

from kortravelweather.models import WeatherLocation
from kortravelweather.providers.kma import mid_land_forecast_to_weather_values
from kortravelweather.repository import WeatherRepository

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)


def _target() -> WeatherTarget:
    return WeatherTarget(
        WeatherLocation(
            location_id="seoul", name="서울", latitude=37.5, longitude=127, nx=60, ny=127
        )
    )


class FakeRepository:
    def __init__(self) -> None:
        self.values = []
        self.sources = []
        self.runs = []

    def start_sync_run(self, **kwargs):
        run = SimpleNamespace(run_id="run-1", status="running")
        self.runs.append(run)
        return run

    def ingest_batch(self, *, source_records, values):
        self.sources.extend(source_records)
        self.values.extend(values)
        return len(values)

    def finish_sync_run(self, run_id, **kwargs):
        self.runs[0].status = kwargs["status"]
        return self.runs[0]


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.forecast = SimpleNamespace(short=self.short, vilage=self.vilage)

    def now(self, **kwargs):
        if self.fail:
            raise RuntimeError("quota")
        return SimpleNamespace(
            nx=60,
            ny=127,
            raw={
                "items": [
                    {
                        "baseDate": "20260101",
                        "baseTime": "0100",
                        "nx": 60,
                        "ny": 127,
                        "category": "T1H",
                        "obsrValue": "3",
                    }
                ]
            },
        )

    def short(self, **kwargs):
        return [
            {
                "baseDate": "20260101",
                "baseTime": "0000",
                "fcstDate": "20260101",
                "fcstTime": "0200",
                "nx": 60,
                "ny": 127,
                "category": "TMP",
                "fcstValue": "4",
            }
        ]

    def vilage(self, **kwargs):
        return [
            {
                "baseDate": "20260101",
                "baseTime": "0000",
                "fcstDate": "20260101",
                "fcstTime": "0300",
                "nx": 60,
                "ny": 127,
                "category": "TMP",
                "fcstValue": "5",
            }
        ]


def test_targets_dedupe_by_grid() -> None:
    rows = targets_from_settings(
        [
            {
                "location_id": "a",
                "name": "A",
                "latitude": 37,
                "longitude": 127,
                "nx": 60,
                "ny": 127,
            },
            {
                "location_id": "b",
                "name": "B",
                "latitude": 37.1,
                "longitude": 127.1,
                "nx": 60,
                "ny": 127,
            },
        ]
    )
    assert [row.location.location_id for row in rows] == ["a", "b"]


def test_duplicate_location_id_is_rejected() -> None:
    target = {
        "location_id": "duplicate",
        "name": "Duplicate",
        "latitude": 37,
        "longitude": 127,
        "nx": 60,
        "ny": 127,
    }
    with pytest.raises(ValueError, match="location_id가 중복"):
        targets_from_settings([target, target])


def test_target_mid_region_and_grid_are_supported() -> None:
    rows = targets_from_settings(
        [
            {
                "location_id": "a",
                "name": "A",
                "latitude": 37,
                "longitude": 127,
                "mid_region_code": "11B00000",
            }
        ]
    )
    assert rows[0].location.nx == 60 and rows[0].mid_region_code == "11B00000"


def test_target_supports_distinct_mid_region_codes() -> None:
    rows = targets_from_settings(
        [
            {
                "location_id": "a",
                "name": "A",
                "latitude": 37,
                "longitude": 127,
                "mid_land_region_code": "11B00000",
                "mid_temperature_region_code": "11B10101",
            }
        ]
    )
    assert rows[0].land_region_code == "11B00000"
    assert rows[0].temperature_region_code == "11B10101"


def test_extra_points_are_converted_to_stable_targets() -> None:
    rows = targets_from_settings([], extra_points="127.0,37.0")
    assert rows[0].location.location_id.startswith("extra-grid-")
    assert rows[0].location.nx is not None and rows[0].location.ny is not None
    assert (
        targets_from_settings(
            [],
            extra_points="127.0,37.0",
            disabled_location_ids={rows[0].location.location_id},
        )
        == []
    )


def test_response_key_is_stable() -> None:
    rows = [{"category": "TMP", "fcstValue": "1"}]
    assert response_source_key("d", "x", rows) == response_source_key("d", "x", rows)


def test_response_key_ignores_volatile_collected_at() -> None:
    metadata_a = SimpleNamespace(
        provider="kma",
        service_name="x",
        endpoint="/api",
        request_params={"nx": 1},
        base_date="20260101",
        base_time="0000",
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    metadata_b = SimpleNamespace(
        provider="kma",
        service_name="x",
        endpoint="/api",
        request_params={"nx": 1},
        base_date="20260101",
        base_time="0000",
        collected_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )
    row_a = SimpleNamespace(raw={"category": "TMP"}, metadata=metadata_a)
    row_b = SimpleNamespace(raw={"category": "TMP"}, metadata=metadata_b)
    assert response_source_key("d", "x", [row_a]) == response_source_key("d", "x", [row_b])
    assert response_source_key(
        "d",
        "x",
        [{"category": "TMP"}],
        {"endpoint": "/api", "collected_at": datetime(2026, 1, 1, tzinfo=UTC)},
    ) == response_source_key(
        "d",
        "x",
        [{"category": "TMP"}],
        {"endpoint": "/api", "collected_at": datetime(2026, 1, 1, 1, tzinfo=UTC)},
    )


def test_response_metadata_redacts_provider_credentials() -> None:
    row = SimpleNamespace(
        raw={"category": "TMP"},
        metadata={
            "endpoint": "/api",
            "request_params": {"serviceKey": "secret", "nx": 60},
        },
    )
    spec = _source_spec("d", "x", [row], datetime(2026, 1, 1, tzinfo=UTC))
    assert spec["payload"]["response_metadata"]["request_params"]["serviceKey"] == "[REDACTED]"
    assert response_source_key("d", "x", [row]) == response_source_key(
        "d",
        "x",
        [
            SimpleNamespace(
                raw={"category": "TMP"},
                metadata={
                    "endpoint": "/api",
                    "request_params": {"serviceKey": "other", "nx": 60},
                },
            )
        ],
    )
    nested = _source_spec(
        "d",
        "x",
        [
            SimpleNamespace(
                raw={"category": "TMP"},
                metadata={
                    "endpoint": "/api",
                    "request_params": {
                        "authKey": "auth-secret",
                        "key": "key-secret",
                        "nested": {"token": "nested-secret", "nx": 60},
                    },
                },
            )
        ],
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert nested["payload"]["response_metadata"]["request_params"] == {
        "authKey": "[REDACTED]",
        "key": "[REDACTED]",
        "nested": {"token": "[REDACTED]", "nx": 60},
    }


def test_sync_publishes_only_after_all_grids() -> None:
    repository = FakeRepository()
    result = run_weather_sync(repository=repository, client=FakeClient(), targets=[_target()])
    assert result["status"] == "success"
    assert len(repository.sources) == 3
    assert repository.values


def test_sync_publishes_bundle_run_to_sqlalchemy_repository(tmp_path) -> None:
    repository = WeatherRepository(TEST_DATABASE_URL)
    repository.create_schema()
    repository.upsert_location(_target().location)
    result = run_weather_sync(repository=repository, client=FakeClient(), targets=[_target()])
    assert result["status"] == "success"
    assert result["values_loaded"] > 0
    assert repository.list_sync_run_sources(result["run_id"])


def test_same_grid_fans_out_to_all_locations() -> None:
    repository = FakeRepository()
    targets = [
        _target(),
        WeatherTarget(
            WeatherLocation(
                location_id="busan", name="부산", latitude=37.6, longitude=127.1, nx=60, ny=127
            )
        ),
    ]
    result = run_weather_sync(repository=repository, client=FakeClient(), targets=targets)
    assert result["grids_fetched"] == 1
    assert {value.location_id for value in repository.values} == {"seoul", "busan"}


def test_base_requests_dedupe_when_mid_regions_differ() -> None:
    class CountingClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.calls = {"now": 0, "short": 0, "vilage": 0}

        def now(self, **kwargs):
            self.calls["now"] += 1
            snapshot = super().now(**kwargs)
            snapshot.raw["items"][0].update(nx=kwargs["nx"], ny=kwargs["ny"])
            return snapshot

        def short(self, **kwargs):
            self.calls["short"] += 1
            rows = super().short(**kwargs)
            rows[0].update(nx=kwargs["nx"], ny=kwargs["ny"])
            return rows

        def vilage(self, **kwargs):
            self.calls["vilage"] += 1
            rows = super().vilage(**kwargs)
            rows[0].update(nx=kwargs["nx"], ny=kwargs["ny"])
            return rows

    client = CountingClient()
    targets = [
        WeatherTarget(_target().location, "11A"),
        WeatherTarget(
            WeatherLocation(
                location_id="other",
                name="Other",
                latitude=37.6,
                longitude=127.1,
                nx=60,
                ny=127,
            ),
            "11B",
        ),
    ]
    result = run_weather_sync(repository=FakeRepository(), client=client, targets=targets)
    assert result["grids_fetched"] == 1
    assert client.calls == {"now": 1, "short": 1, "vilage": 1}


def test_mid_requests_dedupe_by_region_pair_and_validate_response() -> None:
    class GridClient(FakeClient):
        def now(self, **kwargs):
            snapshot = super().now(**kwargs)
            snapshot.raw["items"][0].update(nx=kwargs["nx"], ny=kwargs["ny"])
            return snapshot

        def short(self, **kwargs):
            rows = super().short(**kwargs)
            rows[0].update(nx=kwargs["nx"], ny=kwargs["ny"])
            return rows

        def vilage(self, **kwargs):
            rows = super().vilage(**kwargs)
            rows[0].update(nx=kwargs["nx"], ny=kwargs["ny"])
            return rows

    class CountingDataClient:
        def __init__(self) -> None:
            self.land_calls: list[str] = []
            self.temperature_calls: list[str] = []

        def mid_land_forecast(self, *, reg_id: str):
            self.land_calls.append(reg_id)
            return [{"tmFc": "202601010600", "regId": reg_id, "wf3Am": "맑음", "rnSt3Am": "20"}]

        def mid_temperature_forecast(self, *, reg_id: str):
            self.temperature_calls.append(reg_id)
            return [{"tmFc": "202601010600", "regId": reg_id, "taMin3": "-1", "taMax3": "4"}]

    data_client = CountingDataClient()
    targets = [
        WeatherTarget(
            WeatherLocation(
                location_id="seoul",
                name="서울",
                latitude=37.5,
                longitude=127,
                nx=60,
                ny=127,
            ),
            mid_land_region_code="11B00000",
            mid_temperature_region_code="11B10101",
        ),
        WeatherTarget(
            WeatherLocation(
                location_id="other-grid",
                name="Other",
                latitude=37.6,
                longitude=127.1,
                nx=61,
                ny=127,
            ),
            mid_land_region_code="11B00000",
            mid_temperature_region_code="11B10101",
        ),
    ]
    result = run_weather_sync(
        repository=FakeRepository(),
        client=GridClient(),
        targets=targets,
        include_mid=True,
        data_client=data_client,
    )
    assert result["grids_fetched"] == 2
    assert data_client.land_calls == ["11B00000"]
    assert data_client.temperature_calls == ["11B10101"]


def test_failed_fetch_does_not_publish_facts() -> None:
    repository = FakeRepository()
    with pytest.raises(RuntimeError):
        run_weather_sync(repository=repository, client=FakeClient(fail=True), targets=[_target()])
    assert repository.values == []
    assert repository.sources == []
    assert repository.runs[0].status == "failed"


def test_mid_flat_fields_fan_out() -> None:
    row = {"tmFc": "202601010600", "regId": "11B00000", "wf3Am": "맑음", "rnSt3Am": "20"}
    values = mid_land_forecast_to_weather_values([row], location_id="seoul")
    assert any(value.metric_key == "POP" and value.value_number == 20 for value in values)


def test_wrong_grid_is_not_retried() -> None:
    class WrongGridClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.now_calls = 0

        def now(self, **kwargs):
            self.now_calls += 1
            return SimpleNamespace(
                raw={
                    "items": [
                        {
                            "baseDate": "20260101",
                            "baseTime": "0100",
                            "nx": 99,
                            "ny": 99,
                            "category": "T1H",
                            "obsrValue": "3",
                        }
                    ]
                }
            )

    client = WrongGridClient()
    with pytest.raises(ValueError, match="격자 불일치"):
        stage_grid(client=client, target=_target(), retries=3)
    assert client.now_calls == 1


def test_row_budget_rejects_before_copying_next_kma_response() -> None:
    class OversizedClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.calls = {"now": 0, "short": 0, "vilage": 0}

        def now(self, **kwargs):
            self.calls["now"] += 1
            row = super().now(**kwargs).raw["items"][0]
            return SimpleNamespace(raw={"items": [row, row]})

        def short(self, **kwargs):
            self.calls["short"] += 1
            return super().short(**kwargs)

        def vilage(self, **kwargs):
            self.calls["vilage"] += 1
            return super().vilage(**kwargs)

    client = OversizedClient()
    with pytest.raises(ValueError, match="row 수가 상한"):
        stage_grid(client=client, target=_target(), max_response_rows=1)
    assert client.calls == {"now": 1, "short": 0, "vilage": 0}


def test_value_budget_stops_before_fanout_and_next_provider_call() -> None:
    class CountingClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.calls = {"now": 0, "short": 0, "vilage": 0}

        def now(self, **kwargs):
            self.calls["now"] += 1
            return super().now(**kwargs)

        def short(self, **kwargs):
            self.calls["short"] += 1
            return super().short(**kwargs)

        def vilage(self, **kwargs):
            self.calls["vilage"] += 1
            return super().vilage(**kwargs)

    repository = FakeRepository()
    client = CountingClient()
    with pytest.raises(ValueError, match="normalized fact"):
        run_weather_sync(
            repository=repository,
            client=client,
            targets=[_target()],
            max_values=1,
        )
    assert client.calls == {"now": 1, "short": 0, "vilage": 0}
    assert repository.values == []


def test_non_retryable_kma_auth_error_is_called_once() -> None:
    calls = 0

    def fail() -> None:
        nonlocal calls
        calls += 1
        raise KmaAuthError("invalid key", retryable=False)

    with pytest.raises(KmaAuthError):
        kma_weather._retry_call(fail, retries=2)
    assert calls == 1


def test_retryable_kma_server_error_uses_exponential_backoff(monkeypatch) -> None:
    calls = 0
    delays: list[float] = []

    def fail_twice() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise KmaServerError("temporary", retryable=True)
        return "ok"

    monkeypatch.setattr(kma_weather, "sleep", delays.append)
    assert kma_weather._retry_call(fail_twice, retries=2) == "ok"
    assert calls == 3
    assert delays == [0.25, 0.5]
