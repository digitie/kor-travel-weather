from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

import airkorea.cli as cli
from airkorea._convert import KST


@dataclass(frozen=True)
class FakeMeasurement:
    station_name: str
    data_time: datetime
    pm10_value: float


@dataclass(frozen=True)
class FakeStation:
    station_name: str
    distance_km: float


class FakeClient:
    last_init_key: str | None = None
    last_from_env_called = False
    last_call: tuple[str, dict[str, Any]] | None = None

    def __init__(self, service_key: str | None = None) -> None:
        FakeClient.last_init_key = service_key

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    @classmethod
    def from_env(cls) -> FakeClient:
        cls.last_from_env_called = True
        return cls("env-key")

    def station_measurements(self, station_name: str, **kwargs: Any) -> list[FakeMeasurement]:
        FakeClient.last_call = (
            "station_measurements",
            {"station_name": station_name, **kwargs},
        )
        return [FakeMeasurement(station_name, datetime(2026, 4, 30, 14, tzinfo=KST), 35.0)]

    def sido_measurements(self, *args: Any, **kwargs: Any) -> list[FakeMeasurement]:
        FakeClient.last_call = ("sido_measurements", {"args": args, **kwargs})
        return [FakeMeasurement("종로구", datetime(2026, 4, 30, 14, tzinfo=KST), 35.0)]

    def stations(self, **kwargs: Any) -> list[FakeStation]:
        FakeClient.last_call = ("stations", kwargs)
        return [FakeStation("종로구", 0.0)]

    def nearby_stations(self, **kwargs: Any) -> list[FakeStation]:
        FakeClient.last_call = ("nearby_stations", kwargs)
        return [FakeStation("종로구", 0.4)]

    def forecast_notices(self, **kwargs: Any) -> list[dict[str, str]]:
        FakeClient.last_call = ("forecast_notices", kwargs)
        return [{"informCode": "PM10"}]


@pytest.fixture(autouse=True)
def patch_client(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeClient.last_init_key = None
    FakeClient.last_from_env_called = False
    FakeClient.last_call = None
    monkeypatch.setattr(cli, "AirKoreaClient", FakeClient)


def test_cli_station_outputs_json_and_uses_explicit_key(capsys: pytest.CaptureFixture[str]) -> None:
    result = cli.main(
        [
            "--service-key",
            "decoded-key",
            "station",
            "--station-name",
            "종로구",
            "--num-of-rows",
            "1",
        ]
    )

    assert result == 0
    assert FakeClient.last_init_key == "decoded-key"
    assert FakeClient.last_call == (
        "station_measurements",
        {"station_name": "종로구", "data_term": "DAILY", "num_of_rows": 1},
    )
    assert '"station_name": "종로구"' in capsys.readouterr().out


def test_cli_nearby_uses_env_and_latlon(capsys: pytest.CaptureFixture[str]) -> None:
    result = cli.main(["nearby", "--lat", "37.5665", "--lon", "126.9780"])

    assert result == 0
    assert FakeClient.last_from_env_called is False
    assert FakeClient.last_init_key is None
    assert FakeClient.last_call == (
        "nearby_stations",
        {"lat": 37.5665, "lon": 126.978},
    )
    assert '"distance_km": 0.4' in capsys.readouterr().out


def test_cli_forecast_passes_filters() -> None:
    assert cli.main(["forecast", "--search-date", "2026-04-30", "--inform-code", "PM10"]) == 0
    assert FakeClient.last_call == (
        "forecast_notices",
        {"search_date": "2026-04-30", "inform_code": "PM10"},
    )


def test_cli_rejects_incomplete_location_pairs() -> None:
    with pytest.raises(SystemExit):
        cli.main(["nearby", "--lat", "37.5"])

    with pytest.raises(SystemExit):
        cli.main(["nearby", "--tm-x", "198242"])
