"""수집 범위를 줄일 때 표본이 전국에 고르게 남는지 검증한다."""

from __future__ import annotations

from kortravelweather.providers import ProviderLocation
from kortravelweather.providers.sampling import spatially_even_subset
from kortravelweather.settings import WeatherSettings


def _grid_catalog(rows: int, cols: int) -> list[ProviderLocation]:
    """A catalog laid out evenly over a Korea-shaped bounding box."""
    locations = []
    for row in range(rows):
        for col in range(cols):
            locations.append(
                ProviderLocation(
                    location_id=f"loc-{row:03d}-{col:03d}",
                    latitude=33.0 + (43.0 - 33.0) * row / max(1, rows - 1),
                    longitude=125.0 + (130.0 - 125.0) * col / max(1, cols - 1),
                    metadata={},
                )
            )
    return locations


def test_a_limit_above_the_catalog_keeps_every_location() -> None:
    catalog = _grid_catalog(4, 4)
    assert spatially_even_subset(catalog, 100) == catalog
    assert spatially_even_subset(catalog, len(catalog)) == catalog


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
    # The catalog spans 10 degrees of latitude and 5 of longitude. Sampling each
    # cell's centre deliberately stops half a cell short of the outer edge, so
    # the reachable span is (1 - 1/cells_on_that_axis) of the range, not all of
    # it -- these thresholds sit just under that, and a truncated pick (which
    # collapses one axis to zero) fails them by a wide margin.
    assert lat_span > 8.0
    assert lon_span > 3.5

    # And no quadrant is left empty.
    quadrants = {
        (loc.latitude > 38.0, loc.longitude > 127.5) for loc in subset
    }
    assert len(quadrants) == 4


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
#: sweeping all 1,428 locations, because a sweep stages every value in memory
#: before it publishes. It is a rough figure and varies by dataset shape, but
#: the order of magnitude is what matters -- one step is allowed a couple of
#: gigabytes on a 14 GiB host that runs other services too.
_OBSERVED_MIB_PER_LOCATION = 8.14 * 1024 / 1428
_STEP_MEMORY_BUDGET_MIB = 2560
_CATALOG_LOCATIONS = 1_428
_DATASETS_PER_PROVIDER = 2
_SWEEPS_PER_DAY = 8  # the "15 */3 * * *" schedule


def test_no_cap_lets_one_step_exhaust_the_host() -> None:
    """Quota is not the only ceiling a cap has to respect; memory is the other.

    A sweep stages every value before publishing, so its peak memory scales
    with the location count. OpenWeatherMap's quota would happily cover the
    whole catalog, but at 1,428 locations that step held 8.14 GiB, drove the
    host to 358 MiB free, slowed every other provider sevenfold and killed two
    image builds. A cap raised on quota reasoning alone would do it again.
    """
    caps = WeatherSettings.model_construct().provider_location_caps
    for provider in list(_FREE_TIER_CALLS_PER_MONTH) + ["open_meteo"]:
        locations = caps.get(provider, _CATALOG_LOCATIONS)
        projected = locations * _OBSERVED_MIB_PER_LOCATION
        assert projected <= _STEP_MEMORY_BUDGET_MIB, (
            f"{provider} sweeps {locations} locations, projecting ~{projected:,.0f} MiB "
            f"for one step against a {_STEP_MEMORY_BUDGET_MIB:,} MiB budget"
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
        calls_per_month = locations * _DATASETS_PER_PROVIDER * _SWEEPS_PER_DAY * 30
        assert calls_per_month <= monthly_limit * 0.8, (
            f"{provider} sweeps {locations} locations, issuing {calls_per_month:,} "
            f"calls/month, over the 20% margin on {monthly_limit:,}"
        )


#: Open-Meteo charges weighted calls, not requests:
#: ``max(1, variables/10) * max(1, days/7) * locations``. This project's query
#: asks for 15 variables (7 `current` + 8 `hourly`) over the default 7 days.
#: Counting raw requests instead put the cap 8% over the daily ceiling and the
#: provider started returning "provider rate limit"; the weight belongs in the
#: assertion so the next person to raise the cap cannot miss it.
_OPEN_METEO_VARIABLES = 15
_OPEN_METEO_FORECAST_DAYS = 7
_OPEN_METEO_CALL_WEIGHT = max(1.0, _OPEN_METEO_VARIABLES / 10) * max(
    1.0, _OPEN_METEO_FORECAST_DAYS / 7
)


def test_open_meteo_also_stays_inside_its_daily_ceiling() -> None:
    """Open-Meteo caps by day and hour as well as by month, and bills by weight.

    The monthly figure alone would permit a cap that breaches the daily one, and
    the daily limit is the one a full sweep actually runs into first.
    """
    assert _OPEN_METEO_CALL_WEIGHT == 1.5

    caps = WeatherSettings.model_construct().provider_location_caps
    requests_per_sweep = caps["open_meteo"] * _DATASETS_PER_PROVIDER

    weighted_per_day = requests_per_sweep * _SWEEPS_PER_DAY * _OPEN_METEO_CALL_WEIGHT
    assert weighted_per_day <= 10_000 * 0.8, (
        f"{weighted_per_day:,.0f} weighted calls/day exceeds the 20% margin on 10,000"
    )

    weighted_per_sweep = requests_per_sweep * _OPEN_METEO_CALL_WEIGHT
    assert weighted_per_sweep <= 5_000 * 0.8  # published hourly ceiling

    weighted_per_month = weighted_per_day * 30
    assert weighted_per_month <= 300_000 * 0.8


def test_openweathermap_is_paced_under_its_per_minute_ceiling() -> None:
    """A month inside budget still gets throttled by the minute limit.

    OpenWeatherMap is the one provider left sweeping the whole catalog, so its
    2,856 requests would otherwise go out as fast as the network allows --
    roughly 200/minute once responses are healthy, against a published 60.
    """
    settings = WeatherSettings.model_construct()
    interval = settings.provider_min_request_interval_seconds["openweathermap"]
    assert interval > 0
    requests_per_minute = 60.0 / interval
    assert requests_per_minute <= 60 * 0.8

    # And the paced sweep must still fit inside its three-hour slot.
    sweep_seconds = _CATALOG_LOCATIONS * _DATASETS_PER_PROVIDER * interval
    assert sweep_seconds < 3 * 3600
