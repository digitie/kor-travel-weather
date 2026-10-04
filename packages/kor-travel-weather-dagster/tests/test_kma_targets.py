"""Where the KMA jobs get their targets from.

Production ran on a catalog of station anchors only (AirKorea, KRForest, KREX,
KHOA -- 1,429 rows, every one with ``measurement_point``) and no env targets.
Every KMA job then failed on every run with "weather target이 비어 있습니다."
because stations are kept out of the explicit KMA set and nothing else was
left.  These tests pin the catalog shape that broke and what each job reads.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from dagster import build_asset_context
from kortravelweather_dagster.definitions import (
    _kma_alert_targets,
    _kma_grid_targets,
    kma_mid_forecast_sync,
    kma_ultra_short_nowcast_sync,
    kma_weather_alerts_sync,
)
from kortravelweather_dagster.kma_weather import WeatherTarget, run_weather_sync

from kortravelweather.models import WeatherLocation
from kortravelweather.settings import WeatherSettings


def _station(location_id: str, latitude: float, longitude: float, **kwargs) -> WeatherLocation:
    return WeatherLocation(
        location_id=location_id,
        name=location_id,
        latitude=latitude,
        longitude=longitude,
        metadata={"measurement_point": {"provider": "python-airkorea-api"}},
        **kwargs,
    )


def _station_catalog(rows: int = 8, columns: int = 6) -> list[WeatherLocation]:
    # Stations roughly 20 km apart: every one on its own KMA grid.
    return [
        _station(f"airkorea-{row}-{column}", 34.0 + row * 0.2, 126.4 + column * 0.25)
        for row in range(rows)
        for column in range(columns)
    ]


class _CatalogRepository:
    def __init__(self, locations: list[WeatherLocation]) -> None:
        self._locations = {row.location_id: row for row in locations}
        self.ensured: dict[str, tuple[int, int]] = {}
        self.values: list = []
        self.runs: list = []

    def list_locations(self, *, enabled_only=False, limit=None, offset=0):
        rows = [row for row in self._locations.values() if row.enabled or not enabled_only]
        return rows[offset:] if limit is None else rows[offset : offset + limit]

    # -- what run_weather_sync touches -------------------------------------
    def get_location(self, location_id):
        return self._locations.get(location_id)

    def create_location(self, location):
        self._locations[location.location_id] = location
        return location

    def ensure_location_grid(self, location_id, *, nx, ny, latitude, longitude):
        self.ensured[location_id] = (nx, ny)

    def start_sync_run(self, **kwargs):
        run = SimpleNamespace(run_id=f"run-{len(self.runs) + 1}", status="running", **kwargs)
        self.runs.append(run)
        return run

    def heartbeat_sync_run(self, run_id):
        return True

    def ingest_batch(self, *, source_records, values):
        self.values.extend(values)
        return len(values)

    def ingest_skip_locked(self, *, source_records, values):
        # The alerts publish; no other writer holds a lock here.
        return self.ingest_batch(source_records=source_records, values=values), []

    def finish_sync_run(self, run_id, **kwargs):
        self.runs[-1].status = kwargs["status"]
        return self.runs[-1]


def _settings(**kwargs) -> WeatherSettings:
    return WeatherSettings(
        _env_file=None,
        environment="development",
        targets=kwargs.pop("targets", []),
        extra_points=kwargs.pop("extra_points", None),
        **kwargs,
    )


def _grids(targets) -> set[tuple[int, int]]:
    return {(target.location.nx, target.location.ny) for target in targets}


def _ids(targets) -> list[str]:
    return [target.location.location_id for target in targets]


def _fill(catalog, **settings):
    targets, _, _, fill = _kma_grid_targets(
        _CatalogRepository(catalog), _settings(**settings), station_fill=True
    )
    return targets, fill


def test_station_only_catalog_still_yields_kma_grid_targets() -> None:
    catalog = _station_catalog()
    targets, fill = _fill(catalog, kma_station_fill_max_grids=10)

    assert targets, "a station-only catalog must not leave the KMA grid jobs without targets"
    assert len(_grids(targets)) == 10
    assert set(_ids(targets)) <= {row.location_id for row in catalog}
    assert fill.coverage_line() == (
        "station fill: 10 of 48 stations on 10 of 48 grids (0 shared with explicit targets)"
    )


def test_station_fill_has_its_own_budget_below_the_run_ceiling() -> None:
    # MAX_GRIDS_PER_RUN is a ceiling, not a spend target: the default fill
    # budget (150) is what a station-only catalog costs, not the 300 ceiling.
    catalog = _station_catalog(rows=20, columns=12)  # 240 grids
    defaults = _settings()
    assert defaults.kma_station_fill_max_grids == 150 < defaults.max_grids_per_run
    targets, _ = _fill(catalog)
    assert len(_grids(targets)) == 150


def test_station_fill_never_pushes_the_total_past_the_run_ceiling() -> None:
    explicit = [
        WeatherLocation(location_id="seoul", name="서울", latitude=37.5665, longitude=126.978),
        WeatherLocation(location_id="busan", name="부산", latitude=35.18, longitude=129.075),
    ]
    catalog = [*explicit, *_station_catalog()]
    for ceiling, fill_budget, expected in ((4, 10, 4), (10, 3, 5), (2, 10, 2)):
        targets, _ = _fill(
            catalog, max_grids_per_run=ceiling, kma_station_fill_max_grids=fill_budget
        )
        assert len(_grids(targets)) == expected, (ceiling, fill_budget)
        assert _ids(targets)[:2] == ["seoul", "busan"]


def test_station_fill_can_be_switched_off() -> None:
    targets, fill = _fill(_station_catalog(), kma_station_fill_max_grids=0)
    assert targets == []
    assert fill.grids_filled == 0


def test_station_fill_is_stable_when_the_budget_grows() -> None:
    # Raising the cap may only add grids; a station dropping out of the sample
    # would stop publishing with nothing in the data to say why.
    catalog = _station_catalog()
    small, _ = _fill(catalog, kma_station_fill_max_grids=5)
    large, _ = _fill(catalog, kma_station_fill_max_grids=20)
    assert set(_ids(small)) <= set(_ids(large))


def test_grid_choice_survives_station_rekeying() -> None:
    # AirKorea re-keys a station on any address edit.  The chosen grids must
    # not move when every station id changes.
    catalog = _station_catalog()
    rekeyed = [
        _station(f"rekeyed-{index:03d}", row.latitude, row.longitude)
        for index, row in reversed(list(enumerate(catalog)))
    ]
    before, _ = _fill(catalog, kma_station_fill_max_grids=12)
    after, _ = _fill(rekeyed, kma_station_fill_max_grids=12)
    assert _grids(before) == _grids(after)


def test_stations_on_a_chosen_grid_all_receive_its_response() -> None:
    first = _station("airkorea-a", 37.5665, 126.978)
    twin = _station("airkorea-b", 37.5666, 126.9781)
    targets, fill = _fill([first, twin], kma_station_fill_max_grids=1)
    assert set(_ids(targets)) == {"airkorea-a", "airkorea-b"}
    assert len(_grids(targets)) == 1
    assert fill.coverage_line() == (
        "station fill: 2 of 2 stations on 1 of 1 grids (0 shared with explicit targets)"
    )


def test_explicit_targets_come_first_and_the_budget_goes_to_uncovered_grids() -> None:
    explicit = WeatherLocation(
        location_id="seoul-city-hall", name="서울시청", latitude=37.5665, longitude=126.978
    )
    on_explicit_grid = _station("airkorea-jung", 37.5665, 126.978)
    catalog = [explicit, on_explicit_grid, *_station_catalog()]
    targets, _ = _fill(catalog, kma_station_fill_max_grids=3)

    ids = _ids(targets)
    assert ids[0] == "seoul-city-hall"
    # The explicit grid is not bought twice: 1 explicit + 3 filled grids.
    assert len(_grids(targets)) == 4


def _mid_targets_on_station_cells() -> tuple[list[dict], list[WeatherLocation]]:
    # Production after the nationwide mid regions went into env TARGETS: most
    # explicit targets sit on a grid cell that already holds station anchors.
    explicit = [
        {
            "location_id": "kma-mid-seoul",
            "name": "서울",
            "latitude": 37.5665,
            "longitude": 126.978,
            "mid_land_region_code": "11B00000",
            "mid_temperature_region_code": "11B10101",
        },
        {
            "location_id": "kma-mid-busan",
            "name": "부산",
            "latitude": 35.18,
            "longitude": 129.075,
            "mid_land_region_code": "11H20000",
            "mid_temperature_region_code": "11H20201",
        },
    ]
    stations = [
        _station("airkorea-jung", 37.5666, 126.9781),
        _station("airkorea-jongno", 37.5664, 126.9779),
        _station("airkorea-busan-jung", 35.1801, 129.0751),
        *_station_catalog(),
    ]
    return explicit, stations


def test_stations_sharing_an_explicit_targets_cell_receive_its_response() -> None:
    explicit, stations = _mid_targets_on_station_cells()
    shared = {"airkorea-jung", "airkorea-jongno", "airkorea-busan-jung"}
    for fill_budget in (0, 3):
        targets, fill = _fill(
            stations, targets=explicit, kma_station_fill_max_grids=fill_budget
        )
        ids = _ids(targets)
        assert ids[:2] == ["kma-mid-seoul", "kma-mid-busan"]
        assert shared <= set(ids), fill_budget
        # Shared cells are already requested: they never spend fill budget.
        assert len(_grids(targets)) == 2 + fill_budget
        assert fill.grids_shared == 2


@pytest.mark.usefixtures("_station_only_env")
def test_shared_cell_stations_get_values_from_the_explicit_targets_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    explicit, stations = _mid_targets_on_station_cells()
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_TARGETS", json.dumps(explicit))
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_KMA_STATION_FILL_MAX_GRIDS", "0")
    repository = _CatalogRepository(stations)
    client = _NowcastClient()

    result = _run_asset(kma_ultra_short_nowcast_sync, repository, client)

    assert result["status"] == "success"
    assert len(client.grids) == 2  # one request per explicit grid, nothing more
    published = {value.location_id for value in repository.values}
    assert published == {
        "kma-mid-seoul",
        "kma-mid-busan",
        "airkorea-jung",
        "airkorea-jongno",
        "airkorea-busan-jung",
    }


def test_disabled_stations_are_never_filled_in() -> None:
    catalog = [
        _station("airkorea-off", 37.5665, 126.978, enabled=False),
        _station("airkorea-on", 35.18, 129.075),
    ]
    targets, _ = _fill(catalog)
    assert _ids(targets) == ["airkorea-on"]


def test_jobs_that_read_no_grid_do_not_build_the_fill() -> None:
    targets, _, _, fill = _kma_grid_targets(
        _CatalogRepository(_station_catalog()), _settings(), station_fill=False
    )
    assert targets == []
    assert fill is None


def test_alert_targets_cover_every_enabled_station() -> None:
    catalog = _station_catalog()
    targets = _kma_alert_targets(_CatalogRepository(catalog), _settings(), set())
    assert len(targets) == len(catalog)


# -- the assets, end to end against a fake KMA client ------------------------


class _EntersAndCloses:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _NowcastClient(_EntersAndCloses):
    """Answers each grid with that grid, so the wrong-grid check is live."""

    def __init__(self) -> None:
        self.grids: list[tuple[int, int]] = []

    async def now(self, *, nx, ny):
        self.grids.append((nx, ny))
        return SimpleNamespace(
            raw={
                "items": [
                    {
                        "baseDate": "20260101",
                        "baseTime": "0100",
                        "nx": nx,
                        "ny": ny,
                        "category": "T1H",
                        "obsrValue": "3",
                    }
                ]
            }
        )


class _AlertClient(_EntersAndCloses):
    async def weather_warning_list(self, **kwargs):
        return [{"stnId": "108", "tmFc": "202601010600", "tmSeq": "1", "title": "호우주의보"}]


def _run_asset(asset_fn, repository, client, data_client=None):
    resources = {
        "kma_client": SimpleNamespace(
            create_client=lambda **_: client,
            create_data_client=lambda **_: data_client,
        ),
        "weather_repository": SimpleNamespace(create_repository=lambda: repository),
    }
    with build_asset_context(resources=resources) as context:
        return asset_fn(context)


@pytest.fixture
def _station_only_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Production's environment: no explicit KMA target of any kind.
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_TARGETS", "[]")
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_EXTRA_POINTS", "")
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_KMA_STATION_FILL_MAX_GRIDS", "5")


@pytest.mark.usefixtures("_station_only_env")
def test_nowcast_asset_publishes_filled_stations_and_records_their_grid() -> None:
    # Catalog rows as the AirKorea sync writes them: lat/lon, no nx/ny.
    catalog = _station_catalog()
    assert all(row.nx is None for row in catalog)
    repository = _CatalogRepository(catalog)
    client = _NowcastClient()

    result = _run_asset(kma_ultra_short_nowcast_sync, repository, client)

    assert result["status"] == "success"
    assert len(client.grids) == len(set(client.grids)) == 5
    # Each filled station got its computed grid written back to the catalog
    # and received the response for exactly that grid.
    assert set(repository.ensured.values()) == set(client.grids)
    assert {value.location_id for value in repository.values} == set(repository.ensured)


#: getUltraSrtNcst for prod grid (35, 106) on 2026-10-05 04:00 KST: the station
#: reported nothing, so every category but PTY came back as a Missing sentinel.
_OFFLINE_STATION = {
    "PTY": "0",
    "REH": "-998",
    "RN1": "-998.9",
    "T1H": "-999",
    "UUU": "-998.9",
    "VEC": "-998",
    "VVV": "-998.9",
    "WSD": "-998.9",
}


class _MissingStationNowcastClient(_NowcastClient):
    """The first ``bad_grids`` grids answer with ``answer`` (category -> obsrValue)."""

    def __init__(self, bad_grids: int = 1, answer: dict[str, str] | None = None) -> None:
        super().__init__()
        self.bad_grids = bad_grids
        self.answer = _OFFLINE_STATION if answer is None else answer

    async def now(self, *, nx, ny):
        bad = len(self.grids) < self.bad_grids
        snapshot = await super().now(nx=nx, ny=ny)
        if bad:
            snapshot.raw["items"] = [
                {**snapshot.raw["items"][0], "category": category, "obsrValue": value}
                for category, value in self.answer.items()
            ]
        return snapshot


def _values_skipped_metric(reason: str) -> float:
    from kortravelweather import metrics

    counter = getattr(metrics, "SYNC_VALUES_SKIPPED", None)
    if counter is None:  # RED: the counter does not exist yet
        return 0.0
    return counter.labels(
        provider="python-kma-api", dataset="kma_ultra_short_nowcast", reason=reason
    )._value.get()


@pytest.mark.usefixtures("_station_only_env")
def test_one_station_reporting_missing_does_not_fail_the_nowcast_run() -> None:
    repository = _CatalogRepository(_station_catalog())
    client = _MissingStationNowcastClient()
    before = _values_skipped_metric("missing")

    result = _run_asset(kma_ultra_short_nowcast_sync, repository, client)

    # One offline station (7 sentinels an hour) is routine: it stays green.
    assert result["status"] == "success"
    assert len(client.grids) == 5
    # The other four grids still publish; the missing grid keeps only PTY.
    assert sorted(value.metric_key for value in repository.values) == ["PTY"] + ["T1H"] * 4
    assert all(value.value_number is not None for value in repository.values)
    assert all(abs(value.value_number) < 900 for value in repository.values)
    assert result["values_skipped"] == 7
    assert result["values_attempted"] == 12
    assert result["values_skipped_by_reason"]["kma_ultra_short_nowcast:missing:REH"] == 1
    assert _values_skipped_metric("missing") - before == 7


@pytest.mark.usefixtures("_station_only_env")
def test_many_missing_stations_finish_the_run_partial() -> None:
    repository = _CatalogRepository(_station_catalog())
    client = _MissingStationNowcastClient(bad_grids=3)

    result = _run_asset(kma_ultra_short_nowcast_sync, repository, client)

    assert result["status"] == "partial"
    assert result["values_skipped"] == 21
    # Everything that was not a sentinel still published.
    assert sorted(value.metric_key for value in repository.values) == ["PTY"] * 3 + ["T1H"] * 2


@pytest.mark.usefixtures("_station_only_env")
def test_any_out_of_range_value_finishes_the_run_partial() -> None:
    repository = _CatalogRepository(_station_catalog())
    client = _MissingStationNowcastClient(answer={"REH": "105", "T1H": "3"})
    before = _values_skipped_metric("invalid")

    result = _run_asset(kma_ultra_short_nowcast_sync, repository, client)

    assert result["status"] == "partial"
    assert result["values_skipped_by_reason"] == {"kma_ultra_short_nowcast:invalid:REH": 1}
    assert _values_skipped_metric("invalid") - before == 1


@pytest.mark.usefixtures("_station_only_env")
def test_a_run_whose_every_value_was_skipped_fails() -> None:
    repository = _CatalogRepository(_station_catalog())
    client = _MissingStationNowcastClient(bad_grids=5, answer={"REH": "-998", "T1H": "-999"})

    with pytest.raises(Exception, match="건너뛰"):
        _run_asset(kma_ultra_short_nowcast_sync, repository, client)

    assert repository.values == []
    assert repository.runs[-1].status == "failed"


@pytest.mark.usefixtures("_station_only_env")
def test_alerts_asset_runs_on_a_station_only_catalog() -> None:
    repository = _CatalogRepository(_station_catalog())
    result = _run_asset(
        kma_weather_alerts_sync, repository, _NowcastClient(), data_client=_AlertClient()
    )
    assert result["status"] == "success"
    assert result["alerts_fetched"] == 1
    assert repository.ensured == {}  # no grid target was built for alerts


@pytest.mark.usefixtures("_station_only_env")
def test_mid_asset_without_region_codes_skips_with_a_reason() -> None:
    repository = _CatalogRepository(_station_catalog())
    result = _run_asset(
        kma_mid_forecast_sync, repository, _NowcastClient(), data_client=_AlertClient()
    )
    assert result["skipped"] is True
    assert result["dataset_key"] == "kma_mid_forecast"
    assert "중기예보 지역 코드" in result["reason"]
    assert repository.runs == []  # a skip is not a failed sync run


# -- the per-dataset empty checks in run_weather_sync -----------------------


def _station_target() -> WeatherTarget:
    return WeatherTarget(_station("airkorea-station", 37.5, 127.0, nx=60, ny=127))


def test_alerts_sync_does_not_need_grid_targets() -> None:
    # The alerts job reads alert_targets only.  Gating it on the grid set made
    # it fail on every run of the station-only catalog above.
    result = run_weather_sync(
        repository=_CatalogRepository([]),
        client=_NowcastClient(),
        targets=[],
        include_base=False,
        include_alerts=True,
        data_client=_AlertClient(),
        alert_targets=[_station_target()],
    )
    assert result["status"] == "success"
    assert result["alerts_fetched"] == 1


def test_alerts_sync_with_no_alert_target_says_so() -> None:
    with pytest.raises(ValueError, match="alert target이 비어"):
        run_weather_sync(
            repository=_CatalogRepository([]),
            client=_NowcastClient(),
            targets=[_station_target()],
            include_base=False,
            include_alerts=True,
            data_client=_AlertClient(),
            alert_targets=[],
        )


def test_mid_sync_without_region_codes_refuses_rather_than_fetching_nothing() -> None:
    # The asset skips before getting here; a direct caller still gets a clear
    # error instead of a "success" that fetched nothing.
    repository = _CatalogRepository([])
    with pytest.raises(ValueError, match="중기예보 지역 코드"):
        run_weather_sync(
            repository=repository,
            client=_NowcastClient(),
            targets=[_station_target()],
            include_base=False,
            include_mid=True,
            data_client=_AlertClient(),
        )
    assert repository.runs[-1].status == "failed"
