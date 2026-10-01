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
from kortravelweather_dagster.definitions import _kma_alert_targets, _kma_grid_targets
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


def _station_catalog() -> list[WeatherLocation]:
    # 8 x 6 stations roughly 20 km apart: every one on its own KMA grid.
    return [
        _station(f"airkorea-{row}-{column}", 34.6 + row * 0.2, 126.6 + column * 0.25)
        for row in range(8)
        for column in range(6)
    ]


class _CatalogRepository:
    def __init__(self, locations: list[WeatherLocation]) -> None:
        self._locations = locations

    def list_locations(self, *, enabled_only=False, limit=None, offset=0):
        rows = [row for row in self._locations if row.enabled or not enabled_only]
        return rows[offset:] if limit is None else rows[offset : offset + limit]


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


def test_station_only_catalog_still_yields_kma_grid_targets() -> None:
    catalog = _station_catalog()
    targets, _, _ = _kma_grid_targets(_CatalogRepository(catalog), _settings(max_grids_per_run=10))

    assert targets, "a station-only catalog must not leave the KMA grid jobs without targets"
    assert len(_grids(targets)) == 10
    assert {target.location.location_id for target in targets} <= {
        row.location_id for row in catalog
    }


def test_station_fill_never_exceeds_the_grid_budget() -> None:
    catalog = _station_catalog()
    for budget in (1, 7, 48, 300):
        targets, _, _ = _kma_grid_targets(
            _CatalogRepository(catalog), _settings(max_grids_per_run=budget)
        )
        assert len(_grids(targets)) == min(budget, len(catalog)), budget


def test_station_fill_is_stable_when_the_budget_grows() -> None:
    # Raising the cap may only add grids; a station dropping out of the sample
    # would stop publishing with nothing in the data to say why.
    catalog = _station_catalog()
    small, _, _ = _kma_grid_targets(_CatalogRepository(catalog), _settings(max_grids_per_run=5))
    large, _, _ = _kma_grid_targets(_CatalogRepository(catalog), _settings(max_grids_per_run=20))
    assert {t.location.location_id for t in small} <= {t.location.location_id for t in large}


def test_stations_on_a_chosen_grid_all_receive_its_response() -> None:
    first = _station("airkorea-a", 37.5665, 126.978)
    twin = _station("airkorea-b", 37.5666, 126.9781)
    targets, _, _ = _kma_grid_targets(
        _CatalogRepository([first, twin]), _settings(max_grids_per_run=1)
    )
    assert {target.location.location_id for target in targets} == {"airkorea-a", "airkorea-b"}
    assert len(_grids(targets)) == 1


def test_explicit_targets_come_first_and_stations_fill_only_what_is_left() -> None:
    explicit = WeatherLocation(
        location_id="seoul-city-hall", name="서울시청", latitude=37.5665, longitude=126.978
    )
    on_explicit_grid = _station("airkorea-jung", 37.5665, 126.978)
    catalog = [explicit, on_explicit_grid, *_station_catalog()]
    targets, _, _ = _kma_grid_targets(_CatalogRepository(catalog), _settings(max_grids_per_run=4))

    ids = [target.location.location_id for target in targets]
    assert ids[0] == "seoul-city-hall"
    assert "airkorea-jung" not in ids  # its grid is already covered
    assert len(_grids(targets)) == 4


def test_explicit_targets_that_use_the_whole_budget_get_no_stations() -> None:
    explicit = [
        WeatherLocation(location_id="seoul", name="서울", latitude=37.5665, longitude=126.978),
        WeatherLocation(location_id="busan", name="부산", latitude=35.18, longitude=129.075),
    ]
    targets, _, _ = _kma_grid_targets(
        _CatalogRepository([*explicit, *_station_catalog()]), _settings(max_grids_per_run=2)
    )
    assert [target.location.location_id for target in targets] == ["seoul", "busan"]


def test_disabled_stations_are_never_filled_in() -> None:
    catalog = [
        _station("airkorea-off", 37.5665, 126.978, enabled=False),
        _station("airkorea-on", 35.18, 129.075),
    ]
    targets, _, _ = _kma_grid_targets(_CatalogRepository(catalog), _settings(max_grids_per_run=5))
    assert [target.location.location_id for target in targets] == ["airkorea-on"]


def test_alert_targets_cover_every_enabled_station() -> None:
    catalog = _station_catalog()
    targets = _kma_alert_targets(_CatalogRepository(catalog), _settings(), set())
    assert len(targets) == len(catalog)


class _Repository:
    def __init__(self) -> None:
        self.values = []
        self.runs = []

    def start_sync_run(self, **kwargs):
        run = SimpleNamespace(run_id="run-1", status="running")
        self.runs.append(run)
        return run

    def ingest_batch(self, *, source_records, values):
        self.values.extend(values)
        return len(values)

    def finish_sync_run(self, run_id, **kwargs):
        self.runs[-1].status = kwargs["status"]
        return self.runs[-1]


class _AlertClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def weather_warning_list(self, **kwargs):
        return [{"stnId": "108", "tmFc": "202601010600", "tmSeq": "1", "title": "호우주의보"}]


class _GridClient(_AlertClient):
    pass


def _station_target() -> WeatherTarget:
    return WeatherTarget(_station("airkorea-station", 37.5, 127.0, nx=60, ny=127))


def test_alerts_job_does_not_need_grid_targets() -> None:
    # The alerts job reads alert_targets only.  Gating it on the grid set made
    # it fail on every run of the station-only catalog above.
    repository = _Repository()
    result = run_weather_sync(
        repository=repository,
        client=_GridClient(),
        targets=[],
        include_base=False,
        include_alerts=True,
        data_client=_AlertClient(),
        alert_targets=[_station_target()],
    )
    assert result["status"] == "success"
    assert result["alerts_fetched"] == 1


def test_alerts_job_with_no_alert_target_says_so() -> None:
    with pytest.raises(ValueError, match="alert target이 비어"):
        run_weather_sync(
            repository=_Repository(),
            client=_GridClient(),
            targets=[_station_target()],
            include_base=False,
            include_alerts=True,
            data_client=_AlertClient(),
            alert_targets=[],
        )


def test_mid_job_without_region_codes_fails_instead_of_fetching_nothing() -> None:
    repository = _Repository()
    with pytest.raises(ValueError, match="중기예보 지역 코드"):
        run_weather_sync(
            repository=repository,
            client=_GridClient(),
            targets=[_station_target()],
            include_base=False,
            include_mid=True,
            data_client=_AlertClient(),
        )
    assert repository.runs[-1].status == "failed"
