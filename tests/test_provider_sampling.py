"""수집 범위를 줄일 때 표본이 전국에 고르게 남는지 검증한다."""

from __future__ import annotations

from typing import Any

from kortravelweather_dagster.external_weather import _PUBLISH_BATCH_VALUES

from kortravelweather.providers import ProviderLocation
from kortravelweather.providers.external import OpenMeteoProvider
from kortravelweather.providers.sampling import _cell_rank, spatially_even_subset
from kortravelweather.settings import WeatherSettings


class _CannedResponse:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.status_code = 200
        self.headers = {"content-type": "application/json"}
        self.text = "fixture"

    def json(self) -> Any:
        return self.payload


class _RecordingTransport:
    """Keeps the request params so a test can assert what the provider asked for."""

    def __init__(self, *payloads: Any) -> None:
        self.responses = [_CannedResponse(payload) for payload in payloads]
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _CannedResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


#: The sampler's lattice covers 32-40N by 124-132E, so a test catalog has to
#: stay inside it to exercise the resolution the real catalog (33.23-38.56N,
#: 124.65-130.90E) sees rather than the clamping at its edges.
_LAT_RANGE = (33.0, 38.5)
_LON_RANGE = (125.5, 129.5)


def _grid_catalog(rows: int, cols: int) -> list[ProviderLocation]:
    """A catalog laid out evenly over a Korea-shaped bounding box."""
    locations = []
    for row in range(rows):
        for col in range(cols):
            locations.append(
                ProviderLocation(
                    location_id=f"loc-{row:03d}-{col:03d}",
                    latitude=_LAT_RANGE[0]
                    + (_LAT_RANGE[1] - _LAT_RANGE[0]) * row / max(1, rows - 1),
                    longitude=_LON_RANGE[0]
                    + (_LON_RANGE[1] - _LON_RANGE[0]) * col / max(1, cols - 1),
                    metadata={},
                )
            )
    return locations


def test_a_limit_above_the_catalog_keeps_every_location() -> None:
    catalog = _grid_catalog(4, 4)
    for limit in (100, len(catalog)):
        kept = spatially_even_subset(catalog, limit)
        assert {location.location_id for location in kept} == {
            location.location_id for location in catalog
        }
    # Returned in sampling order, not the caller's, so that the whole-catalog
    # case is the same prefix as every smaller one -- and so that a sweep which
    # dies partway through has still covered the country rather than whatever
    # the catalog query happened to return first.
    assert [location.location_id for location in spatially_even_subset(catalog, 99)] == [
        location.location_id for location in spatially_even_subset(catalog, len(catalog))
    ]


def test_a_limit_of_zero_or_less_collects_nothing() -> None:
    catalog = _grid_catalog(4, 4)
    assert spatially_even_subset(catalog, 0) == []
    assert spatially_even_subset(catalog, -5) == []


def test_the_subset_is_exactly_the_requested_size() -> None:
    catalog = _grid_catalog(20, 20)
    for limit in (1, 7, 55, 199, 399):
        assert len(spatially_even_subset(catalog, limit)) == limit


def test_the_same_catalog_and_limit_always_choose_the_same_locations() -> None:
    """A location that appears and disappears between runs looks like an outage.

    The published values carry no marker for "this location was sampled out",
    so an unstable subset reads downstream as a station that keeps failing.
    """
    catalog = _grid_catalog(12, 12)
    first = spatially_even_subset(catalog, 40)
    second = spatially_even_subset(list(reversed(catalog)), 40)
    assert [location.location_id for location in first] == [
        location.location_id for location in second
    ]


def test_the_subset_spans_the_country_rather_than_one_corner() -> None:
    """Coverage is the whole point of shrinking this way.

    Taking the first N by id would satisfy every test above while leaving
    entire provinces unobserved, so this is the assertion that actually
    distinguishes an even sample from a cheap truncation.
    """
    catalog = _grid_catalog(30, 30)
    subset = spatially_even_subset(catalog, 25)

    lat_span = max(loc.latitude for loc in subset) - min(loc.latitude for loc in subset)
    lon_span = max(loc.longitude for loc in subset) - min(loc.longitude for loc in subset)
    # A truncated pick collapses one axis to nearly zero and fails these by a
    # wide margin; the sampler reaches most of both.
    assert lat_span > (_LAT_RANGE[1] - _LAT_RANGE[0]) * 0.8
    assert lon_span > (_LON_RANGE[1] - _LON_RANGE[0]) * 0.8

    # And no quadrant is left empty.
    mid_lat = sum(_LAT_RANGE) / 2
    mid_lon = sum(_LON_RANGE) / 2
    quadrants = {(loc.latitude > mid_lat, loc.longitude > mid_lon) for loc in subset}
    assert len(quadrants) == 4


def test_two_stations_in_one_cell_are_never_both_sampled_early() -> None:
    """Two picks a few hundred metres apart are one wasted request.

    The quota is the scarce thing here, so a near-duplicate pair costs coverage
    somewhere else on the map. On the real catalog the previous sampler's
    closest pair sat 0.1 km apart, and a first attempt at this one sampled an
    exactly co-located pair -- 0.00 km, two stations at the same coordinates.

    What prevents it is ordering by depth before cell: every occupied cell gives
    up one location before any cell gives up a second. So while the sample is
    smaller than the number of occupied cells, each pick is in a cell of its own.
    """
    # Dense enough that several stations share a cell, as cities do.
    catalog = _grid_catalog(60, 60)
    occupied = len({_cell_rank(location) for location in catalog})
    assert occupied < len(catalog), "need cells holding more than one station"

    for limit in (25, 60, occupied):
        subset = spatially_even_subset(catalog, limit)
        cells = {_cell_rank(location) for location in subset}
        assert len(cells) == min(limit, occupied), (
            f"at limit {limit} the sample covers {len(cells)} cells, so "
            f"{limit - len(cells)} pick(s) doubled up in a cell already sampled"
        )


def _clustered_catalog() -> list[ProviderLocation]:
    """A catalog shaped like the real one: most stations packed into cities.

    An evenly spread catalog flatters any sampler -- on a uniform grid the
    limit-sized-grid version this replaced actually spaces its picks better than
    the lattice does. What separates them is clustering, which is what the real
    catalog has: 1,428 stations concentrated on the capital region and a handful
    of other cities, with whole counties holding one.
    """
    centres = ((37.55, 126.98), (35.18, 129.08), (35.87, 128.60), (35.15, 126.92))
    locations = []
    for index, (lat, lon) in enumerate(centres):
        for step in range(150):  # a dense blob, ~25 km across
            locations.append(
                ProviderLocation(
                    location_id=f"city-{index}-{step:03d}",
                    latitude=lat + 0.22 * ((step % 13) / 13 - 0.5),
                    longitude=lon + 0.22 * ((step // 13) / 12 - 0.5),
                    metadata={},
                )
            )
    for index in range(200):  # the countryside, spread thin
        locations.append(
            ProviderLocation(
                location_id=f"rural-{index:03d}",
                latitude=_LAT_RANGE[0]
                + (_LAT_RANGE[1] - _LAT_RANGE[0]) * ((index * 7) % 200) / 200,
                longitude=_LON_RANGE[0]
                + (_LON_RANGE[1] - _LON_RANGE[0]) * ((index * 11) % 200) / 200,
                metadata={},
            )
        )
    return locations


def _region(location: ProviderLocation) -> tuple[int, int]:
    """Which of 16 coarse blocks over the country a location falls in."""
    lat_step = (_LAT_RANGE[1] - _LAT_RANGE[0]) / 4
    lon_step = (_LON_RANGE[1] - _LON_RANGE[0]) / 4
    return (
        min(3, max(0, int((location.latitude - _LAT_RANGE[0]) / lat_step))),
        min(3, max(0, int((location.longitude - _LON_RANGE[0]) / lon_step))),
    )


def test_every_populated_region_gets_its_share_of_the_sample() -> None:
    """"Whole provinces unobserved" is the failure this module exists to prevent.

    Stated as area shares rather than distances, because a distance needs an
    "ideal spacing" and there is no unambiguous one: dividing the catalog's
    bounding box by the sample size counts a lot of sea, and dividing Korea's
    land area does not generalise to a synthetic catalog.

    The share is *equal per region*, not proportional to the stations a region
    holds. Proportional would be the wrong target: a quarter of the catalog sits
    in the capital region, and matching that would spend a quarter of a scarce
    quota inside one city while counties went unobserved. Coverage is the point,
    so each populated block should get about the same number of picks, limited
    only by having that many stations to offer. Half of equal is the line;
    taking the first N by id, or sweeping the lattice in raster order, leaves
    whole blocks on zero.
    """
    for catalog in (_grid_catalog(30, 30), _clustered_catalog()):
        held: dict[tuple[int, int], int] = {}
        for location in catalog:
            held[_region(location)] = held.get(_region(location), 0) + 1

        for limit in (60, 150, 300):
            sampled: dict[tuple[int, int], int] = {}
            for location in spatially_even_subset(catalog, limit):
                sampled[_region(location)] = sampled.get(_region(location), 0) + 1

            equal_share = limit / len(held)
            for region, count in held.items():
                expected = min(count, equal_share)
                assert sampled.get(region, 0) >= expected * 0.5, (
                    f"region {region} holds {count} stations and an equal share of "
                    f"{limit} picks across {len(held)} populated regions is "
                    f"{equal_share:.1f}, but it got {sampled.get(region, 0)}"
                )


def test_raising_a_limit_keeps_everything_the_lower_one_collected() -> None:
    """Raising a cap must add locations, never swap them out.

    Nothing in a published value says "this location was sampled out", so a
    location that silently leaves the sample looks downstream like a station
    that stopped reporting. Sizing the sample's grid from the limit broke this:
    moving Open-Meteo's cap from 300 to 380 against the real 1,428-location
    catalog kept only 140 of the original 300 and dropped 160.
    """
    catalog = _grid_catalog(28, 18)
    smaller = spatially_even_subset(catalog, 120)
    larger = spatially_even_subset(catalog, 260)

    smaller_ids = {location.location_id for location in smaller}
    larger_ids = {location.location_id for location in larger}
    assert smaller_ids <= larger_ids, (
        f"{len(smaller_ids - larger_ids)} of {len(smaller_ids)} locations would "
        "stop being collected by raising the limit"
    )

    # And the sequence itself is stable, not merely its membership: growing the
    # limit appends, so run-to-run comparisons line up positionally too.
    assert [location.location_id for location in larger[: len(smaller)]] == [
        location.location_id for location in smaller
    ]


def test_one_station_arriving_or_leaving_does_not_resample_the_country() -> None:
    """The catalog changes far more often than a cap does.

    Upstream station lists are synced, and AirKorea builds ``location_id`` from
    a hash of the station's name and address, so an address correction upstream
    is a removal plus an addition. A greedy farthest-point order -- which spread
    a sample better than this and was tried first -- re-derives every pick after
    the one that moved: removing a single sampled station cost 160 of 380
    locations on the real catalog. Ranking each location by its own cell keeps
    the damage to the location that actually moved.
    """
    catalog = _grid_catalog(28, 18)
    limit = 120
    baseline = {location.location_id for location in spatially_even_subset(catalog, limit)}

    for position in (0, 1, 5, 30, 90):
        victim = spatially_even_subset(catalog, limit)[position].location_id
        without = [loc for loc in catalog if loc.location_id != victim]
        kept = baseline & {loc.location_id for loc in spatially_even_subset(without, limit)}
        # The removed location is gone by definition; nothing else may follow it
        # out, and the freed slot is filled from the same cell or the next one.
        assert kept == baseline - {victim}, (
            f"removing sampled #{position} also dropped "
            f"{len(baseline - {victim} - kept)} other location(s)"
        )

    # Additions can only displace the tail: a new station that ranks inside the
    # limit pushes out the last one, and no more than one per new station.
    added = [
        ProviderLocation(
            location_id=f"new-{index:02d}",
            latitude=_LAT_RANGE[0] + (_LAT_RANGE[1] - _LAT_RANGE[0]) * (index % 7) / 7,
            longitude=_LON_RANGE[0] + (_LON_RANGE[1] - _LON_RANGE[0]) * (index % 5) / 5,
            metadata={},
        )
        for index in range(10)
    ]
    grown = {loc.location_id for loc in spatially_even_subset(catalog + added, limit)}
    assert len(baseline - grown) <= len(added), (
        f"{len(added)} new stations displaced {len(baseline - grown)} existing ones"
    )


def test_a_catalog_stacked_on_one_point_still_returns_the_limit() -> None:
    """Degenerate bounding box: every location at the same coordinate."""
    catalog = [
        ProviderLocation(
            location_id=f"same-{index:02d}", latitude=37.5, longitude=127.0, metadata={}
        )
        for index in range(10)
    ]
    subset = spatially_even_subset(catalog, 4)
    assert len(subset) == 4
    assert len({location.location_id for location in subset}) == 4


def test_a_location_outside_the_lattice_is_still_collected() -> None:
    """The lattice is a fixed box, so anything beyond it lands in an edge cell.

    Clamping is the intended behaviour -- a station off Korea's coast should be
    sampled, not dropped -- and this pins it, because silently excluding a
    location is the failure this module is supposed to prevent.
    """
    catalog = _grid_catalog(6, 6) + [
        ProviderLocation("far-north", 45.0, 127.0, {}),
        ProviderLocation("far-south", 28.0, 127.0, {}),
        ProviderLocation("far-east", 36.0, 140.0, {}),
        ProviderLocation("far-west", 36.0, 118.0, {}),
    ]
    sampled = {loc.location_id for loc in spatially_even_subset(catalog, len(catalog))}
    for outlier in ("far-north", "far-south", "far-east", "far-west"):
        assert outlier in sampled


#: Each vendor's published free-tier ceiling, and the whole catalog as the cap
#: that applies when a provider is absent from ``provider_location_caps``.
#: Open-Meteo is deliberately absent: it bills weighted calls rather than
#: requests, so a raw-request assertion here would pass while the real usage
#: was over. Its own test below carries the weight.
_FREE_TIER_CALLS_PER_MONTH = {
    "weatherapi": 100_000,
    "openweathermap": 1_000_000,
}

#: Measured, not estimated: openweathermap's forecast step held 8.14 GiB while
#: sweeping all 1,428 locations back when a sweep staged every value before
#: publishing -- about 5.8 MiB per location.
_OBSERVED_MIB_PER_LOCATION = 8.14 * 1024 / 1428
_STEP_MEMORY_BUDGET_MIB = 2560

#: ~1,350 values per location for the widest dataset (open_meteo's forecast),
#: so the per-location figure above divided by them approximates one value.
_OBSERVED_MIB_PER_VALUE = _OBSERVED_MIB_PER_LOCATION / 1350
_CATALOG_LOCATIONS = 1_428
_DATASETS_PER_PROVIDER = 2
_SWEEPS_PER_DAY = 8  # the "15 */3 * * *" schedule
_SWEEP_INTERVAL_SECONDS = 3 * 3600
#: Months are 31 days when it matters. Billing a 30-day month against a monthly
#: ceiling quietly buys 3% of headroom that the longest months do not have.
_DAYS_PER_MONTH = 31


def test_a_sweep_cannot_hold_the_whole_host_in_memory() -> None:
    """What bounds a step's memory is the publish batch, not the location count.

    It used to be the location count: a sweep staged every value before
    publishing, so openweathermap at 1,428 locations held 8.14 GiB, drove the
    host to 358 MiB free, slowed every other provider sevenfold and killed two
    image builds. Batched publishing is what removed that coupling, so this
    asserts the batch -- if batching is ever switched off by default, the old
    failure returns and the location caps alone will not stop it.
    """
    assert _PUBLISH_BATCH_VALUES > 0, "batching off means a sweep stages everything again"
    projected = _PUBLISH_BATCH_VALUES * _OBSERVED_MIB_PER_VALUE
    assert projected <= _STEP_MEMORY_BUDGET_MIB, (
        f"a {_PUBLISH_BATCH_VALUES:,}-value batch projects ~{projected:,.0f} MiB "
        f"against a {_STEP_MEMORY_BUDGET_MIB:,} MiB budget"
    )


#: Seconds per request from the *slowest* successful production sweep of each
#: dataset after batched publishing landed -- max duration over request count,
#: not the average, because a cap has to hold on the bad sweeps too. These are
#: measurements from the deployment host, and they are what bounds the caps now
#: that memory does not: the remaining cost is database write throughput on a
#: spinning disk, so a dataset writing 169 rows and 1,351 values per location is
#: 13 times slower per request than one writing 1 row and 7 values.
#: Re-measure before editing them.
_WORST_SECONDS_PER_REQUEST = {
    "weatherapi": {"weatherapi_current": 0.43, "weatherapi_forecast": 7.81},
    "open_meteo": {"open_meteo_current": 1.65, "open_meteo_forecast": 21.94},
    "openweathermap": {
        "openweathermap_current": 3.43,
        "openweathermap_forecast": 8.22,
    },
}


def test_a_sweep_finishes_before_the_next_one_starts() -> None:
    """A sweep that outlives its own schedule is the stall this all came from.

    Runs arrived hourly and took four to thirteen hours, so they piled up until
    every concurrency slot was held by a run that would never finish and the
    Korean sources went nineteen hours without an update. A cap can sit well
    inside a vendor's free tier and still take longer to sweep than the gap
    between sweeps.

    A provider's datasets run one after another inside a single asset, so the
    wall clock is their sum. At the shipped caps a worst-case sweep *consumes*
    this share of its three hours: weatherapi 11%, openweathermap 38%,
    open_meteo 83%. Open-Meteo is the tight one, and this is the guard that holds
    it: it stops a raise at 457 locations where the quota test would allow 500.
    For openweathermap it is the only guard that binds at all, since that quota
    covers the whole 1,428-location catalog; for weatherapi the quota binds first,
    at 161.

    It is also not the only ceiling that matters, and not the tightest: nine
    external jobs fire on the same three-hourly tick against three concurrency
    slots, so a sweep can sit in the queue before it starts. Fitting inside the
    interval is necessary, not sufficient.
    """
    caps = WeatherSettings.model_construct().provider_location_caps
    for provider, per_dataset in _WORST_SECONDS_PER_REQUEST.items():
        locations = caps.get(provider, _CATALOG_LOCATIONS)
        sweep_seconds = locations * sum(per_dataset.values())
        assert sweep_seconds <= _SWEEP_INTERVAL_SECONDS, (
            f"{provider}'s slowest sweep of {locations} locations takes "
            f"~{sweep_seconds / 3600:.1f}h, past the "
            f"{_SWEEP_INTERVAL_SECONDS / 3600:.0f}h until the next one starts"
        )


def test_the_shipped_caps_stay_inside_each_vendor_free_tier() -> None:
    """The defaults are a quota calculation, so drift either way is a bug.

    A provider absent from the caps mapping must be checked against the *whole*
    catalog, since that is what it would then sweep.

    Exceeding a quota does not fail cleanly -- the vendor throttles, every
    request takes ~15s instead of ~0.3s, and the run outlives its own schedule.
    That is what filled the run queue and stalled every other source.
    """
    caps = WeatherSettings.model_construct().provider_location_caps
    for provider, monthly_limit in _FREE_TIER_CALLS_PER_MONTH.items():
        locations = caps.get(provider, _CATALOG_LOCATIONS)
        calls_per_month = (
            locations * _DATASETS_PER_PROVIDER * _SWEEPS_PER_DAY * _DAYS_PER_MONTH
        )
        assert calls_per_month <= monthly_limit * 0.8, (
            f"{provider} sweeps {locations} locations, issuing {calls_per_month:,} "
            f"calls/month, over the 20% margin on {monthly_limit:,}"
        )


#: Open-Meteo charges weighted calls, not requests. Its published rule: a request
#: for "more than 10 weather variables or extending over a period of more than 2
#: weeks for a single location" counts as multiple calls, fractionally -- with
#: the worked examples "2 weeks of data with 15 weather variables ... 1.5 API
#: calls, while 4 weeks of data equals 3.0". So the weight is
#: ``max(1, variables/10) * max(1, days/14)``, and the day threshold is two
#: weeks, not one. Counting raw requests instead of weight put the cap 8% over
#: the daily ceiling and the provider started returning "provider rate limit".
#:
#: What the rule does *not* settle is whether a request carrying a 7-variable
#: block and an 8-variable block counts 15 variables (1.5) or two blocks each
#: floored at 10 (2.0). Rather than bet on a reading, each request now carries
#: one block of at most 10 variables, which costs 1.0 either way -- so this
#: weight is a fact about the request, not an interpretation, and
#: ``test_each_open_meteo_request_costs_one_call`` holds the code to it.
_OPEN_METEO_VARIABLE_FLOOR = 10
_OPEN_METEO_DAY_FLOOR = 14
_OPEN_METEO_FORECAST_DAYS = 7  # the API default; nothing sets forecast_days
_OPEN_METEO_WEIGHT_PER_LOCATION_PER_SWEEP = _DATASETS_PER_PROVIDER * (
    max(1.0, _OPEN_METEO_VARIABLE_FLOOR / _OPEN_METEO_VARIABLE_FLOOR)
    * max(1.0, _OPEN_METEO_FORECAST_DAYS / _OPEN_METEO_DAY_FLOOR)
)


def test_each_open_meteo_request_costs_one_call() -> None:
    """The weight above is only worth trusting if the request still earns it.

    Asserting ``max(1, 7/10) == 1.0`` against a literal defined ten lines up
    proves nothing; adding a ninth hourly variable, or restoring the current
    block to the forecast request, would leave such an assertion green while the
    real cost per location went from 2.0 to 2.5 or 3.0 and the daily ceiling came
    back into reach. So this reads the params the provider actually sends.
    """
    transport = _RecordingTransport(
        {"current": {"time": "2026-09-14T00:00:00Z", "temperature_2m": 20}},
        {"hourly": {"time": ["2026-09-14T00:00:00Z"], "temperature_2m": [20]}},
    )
    provider = OpenMeteoProvider(transport=transport)

    for dataset in ("open_meteo_current", "open_meteo_forecast"):
        provider.fetch(ProviderLocation("seoul", 37.5665, 126.978, {}), dataset_key=dataset)
        params = transport.calls[-1]["params"]

        blocks = [key for key in ("current", "hourly", "daily") if key in params]
        assert len(blocks) == 1, f"{dataset} asks for {blocks}, so its weight is ambiguous"
        variables = params[blocks[0]].split(",")
        assert len(variables) <= _OPEN_METEO_VARIABLE_FLOOR, (
            f"{dataset} asks for {len(variables)} variables in one request, over the "
            f"{_OPEN_METEO_VARIABLE_FLOOR} that cost a single call"
        )
        assert "forecast_days" not in params, (
            "a forecast_days over 14 would multiply the weight; the default is 7"
        )


def test_open_meteo_stays_inside_its_daily_ceiling() -> None:
    """Open-Meteo caps by hour and day as well as by month, and bills by weight.

    The daily limit is the one a sweep runs into first -- 10,000 against a
    monthly 300,000, which is only 9,677 a day across a 31-day month.
    """
    caps = WeatherSettings.model_construct().provider_location_caps
    weighted_per_sweep = caps["open_meteo"] * _OPEN_METEO_WEIGHT_PER_LOCATION_PER_SWEEP
    weighted_per_day = weighted_per_sweep * _SWEEPS_PER_DAY

    assert weighted_per_day <= 10_000 * 0.8, (
        f"{weighted_per_day:,.0f} weighted calls/day exceeds the 20% margin on 10,000"
    )
    assert weighted_per_sweep <= 5_000 * 0.8  # published hourly ceiling
    assert weighted_per_day * _DAYS_PER_MONTH <= 300_000 * 0.8


def test_openweathermap_is_paced_under_its_per_minute_ceiling() -> None:
    """A month inside budget still gets throttled by the minute limit.

    OpenWeatherMap publishes 60 calls a minute alongside its monthly million,
    and an unpaced sweep issues roughly 200 a minute once responses are healthy.

    The pacing floor is not what decides how long a sweep takes -- measured
    requests cost 3.43s and 8.22s against this 1.25s floor, and the interval is
    timed from the start of the previous request rather than its end, so the two
    do not stack. It is a ceiling on how fast the sweep may go if the provider
    ever gets fast, which is why the sweep-duration guard is measured separately.
    """
    settings = WeatherSettings.model_construct()
    interval = settings.provider_min_request_interval_seconds["openweathermap"]
    assert interval > 0
    requests_per_minute = 60.0 / interval
    assert requests_per_minute <= 60 * 0.8

    # And pacing alone must not push the capped sweep past its slot.
    locations = settings.provider_location_caps.get("openweathermap", _CATALOG_LOCATIONS)
    sweep_seconds = locations * _DATASETS_PER_PROVIDER * interval
    assert sweep_seconds < _SWEEP_INTERVAL_SECONDS
