"""해양·산악·고속도로 관측망 adapter와 publish 경로."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import pytest
from kortravelweather_dagster.regional_sources import publish_regional_records

from kortravelweather.models import ForecastStyle
from kortravelweather.providers import khoa, krex, krforest
from kortravelweather.repository import WeatherRepository

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)
KNOWN_AT = datetime(2026, 3, 1, 9, tzinfo=UTC)


class _Row:
    """A stand-in for a library model: attribute access plus ``raw``."""

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)
        self.raw = dict(fields)


def _mountain(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "obs_id": "M001",
        "obs_name": "지리산",
        "local_area": "경상남도 함양군",
        "observed_at": datetime(2026, 3, 1, 8, tzinfo=UTC),
        "temperature_2m": 3.5,
        "temperature_10m": 3.1,
        "humidity_2m": 71.0,
        "humidity_10m": None,
        "pressure": 902.4,
        "rainfall_tipping": 0.0,
        "rainfall_weight": None,
        "ground_temperature": 5.2,
        "wind_direction_2m": 270.0,
        "wind_direction_2m_name": "서",
        "wind_direction_10m": None,
        "wind_direction_10m_name": None,
        "wind_speed_2m": 1.8,
        "wind_speed_10m": 2.4,
        "latitude": 35.337,
        "longitude": 127.730,
    }
    fields.update(overrides)
    return _Row(**fields)


def _restarea(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "observed_at": datetime(2026, 3, 1, 8, tzinfo=UTC),
        "sdate": "20260301",
        "std_hour": "08",
        "unit_code": "000001",
        "unit_name": "안성휴게소",
        "route_no": "0010",
        "route_name": "경부선",
        "direction_code": "1",
        "lat": 37.02,
        "lon": 127.25,
        "address": "경기도 안성시",
        "measurement_station": "안성",
        "weather": "맑음",
        "temperature": 7.4,
        "humidity": 44.0,
        "wind_speed": 2.1,
        "wind_direction_code": "W",
        "rainfall": 0.0,
        "rainfall_strength": None,
        "new_snow": None,
        "snow": None,
        "cloud": 2.0,
        "dew_point": -4.1,
    }
    fields.update(overrides)
    return _Row(**fields)


class _Forecast:
    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)
        self.raw = dict(fields)


def _beach(**overrides: Any) -> Any:
    from datetime import date

    fields: dict[str, Any] = {
        "id": "BCH110",
        "name": "38(기사문)해수욕장",
        "latitude": 38.005,
        "longitude": 128.731,
        "road_address": None,
        "parcel_address": "강원특별자치도 양양군 현북면 잔교리 41-9",
        "forecasts": (
            _Forecast(
                predicted_on=date(2026, 3, 2),
                forecast_period="D+1",
                max_wave_height_m=0.8,
                average_water_temperature_c=11.2,
                average_air_temperature_c=9.4,
                max_wind_speed_m_s=5.1,
                open_status="개장",
                total_index=72,
            ),
        ),
    }
    fields.update(overrides)
    return _Row(**fields)


def test_mountain_rows_become_observed_values_with_units() -> None:
    values = krforest.mountain_weather_to_weather_values(
        _mountain(), location_id="krforest-M001", source_record_key="s", known_at=KNOWN_AT
    )
    by_metric = {value.metric_key: value for value in values}
    # Only the fields that were present; a null reading must not become a zero.
    assert "HUMIDITY_10M" not in by_metric
    assert "RAINFALL_WEIGHT" not in by_metric
    assert by_metric["TEMP"].unit == "℃"
    assert float(by_metric["TEMP"].value_number) == 3.5
    assert by_metric["WIND_SPEED_10M"].source_metric_key == "wind_speed_10m"
    assert all(value.forecast_style is ForecastStyle.OBSERVED for value in values)
    # target_at follows the observation, not the collection time, or every
    # station would appear to have reported at the moment of the fetch.
    assert by_metric["TEMP"].target_at == datetime(2026, 3, 1, 8, tzinfo=UTC)


def test_rest_area_text_condition_rides_as_its_own_value() -> None:
    values = krex.restarea_weather_to_weather_values(
        _restarea(), location_id="krex-000001", source_record_key="s", known_at=KNOWN_AT
    )
    by_metric = {value.metric_key: value for value in values}
    assert by_metric["WEATHER_TEXT"].value_text == "맑음"
    assert by_metric["WEATHER_TEXT"].value_number is None
    assert float(by_metric["DEW_POINT"].value_number) == -4.1
    # A blank condition must not produce an empty-string value.
    blank = krex.restarea_weather_to_weather_values(
        _restarea(weather="  "), location_id="l", source_record_key="s", known_at=KNOWN_AT
    )
    assert "WEATHER_TEXT" not in {value.metric_key for value in blank}


def test_beach_index_rows_are_recorded_as_forecasts() -> None:
    """These are next-day outlooks, and filing them as observations would make
    a bundle read present tomorrow's wave height as the current sea state."""
    values = khoa.beach_index_to_weather_values(
        _beach(), location_id="khoa-BCH110", source_record_key="s", known_at=KNOWN_AT
    )
    assert values, "no values produced"
    assert all(value.forecast_style is ForecastStyle.SHORT for value in values)
    assert all(value.target_at.date().isoformat() == "2026-03-02" for value in values)
    by_metric = {value.metric_key: value for value in values}
    assert float(by_metric["WAVE_HEIGHT"].value_number) == 0.8
    assert by_metric["BEACH_OPEN_STATUS"].value_text == "개장"


def test_a_forecast_without_a_date_is_dropped_not_dated_now() -> None:
    from datetime import date

    place = _beach(
        forecasts=(
            _Forecast(
                predicted_on=None,
                forecast_period=None,
                max_wave_height_m=1.0,
                average_water_temperature_c=None,
                average_air_temperature_c=None,
                max_wind_speed_m_s=None,
                open_status=None,
                total_index=None,
            ),
            _Forecast(
                predicted_on=date(2026, 3, 3),
                forecast_period="D+2",
                max_wave_height_m=1.4,
                average_water_temperature_c=None,
                average_air_temperature_c=None,
                max_wind_speed_m_s=None,
                open_status=None,
                total_index=None,
            ),
        )
    )
    values = khoa.beach_index_to_weather_values(
        place, location_id="khoa-BCH110", source_record_key="s", known_at=KNOWN_AT
    )
    assert [float(value.value_number) for value in values] == [1.4], (
        "a forecast with no date was kept; it would be filed at the collection "
        "time and read as if it described now"
    )


@pytest.mark.parametrize(
    ("module", "factory", "to_location", "prefix"),
    [
        (krforest, _mountain, "station_location", "krforest-M001"),
        (krex, _restarea, "restarea_location", "krex-000001"),
        (khoa, _beach, "beach_location", "khoa-BCH110"),
    ],
)
def test_rows_without_coordinates_are_dropped(
    module: Any, factory: Any, to_location: str, prefix: str
) -> None:
    """An anchor at (0, 0) is worse than no anchor: it is on the map, in the
    sea off Ghana, and every nearest-anchor read can reach it."""
    convert = getattr(module, to_location)
    assert convert(factory()).location_id == prefix
    for missing in ({"latitude": None}, {"longitude": None}, {"lat": None}, {"lon": None}):
        row = factory()
        if not all(hasattr(row, key) for key in missing):
            continue
        assert convert(factory(**missing)) is None


def test_source_record_key_is_stable_for_identical_readings() -> None:
    """The key digests the reading, so an unchanged fetch replays onto the same
    source record instead of creating one every run."""
    first = krforest.mountain_source_record(
        _mountain(), location_id="krforest-M001", fetched_at=KNOWN_AT
    )
    second = krforest.mountain_source_record(
        _mountain(),
        location_id="krforest-M001",
        fetched_at=datetime(2026, 3, 1, 10, tzinfo=UTC),
    )
    assert first["source_record_key"] == second["source_record_key"], (
        "the fetch time leaked into the key, so every run would create a new "
        "source record for identical data"
    )
    changed = krforest.mountain_source_record(
        _mountain(temperature_2m=3.6), location_id="krforest-M001", fetched_at=KNOWN_AT
    )
    assert changed["source_record_key"] != first["source_record_key"]
    # The entity has to be the location, which is what the repository's lineage
    # check compares a fact against.
    assert first["source_entity_id"] == "krforest-M001"


def test_publish_creates_anchors_and_publishes_atomically() -> None:
    repository = WeatherRepository(TEST_DATABASE_URL)
    repository.create_schema()

    result = publish_regional_records(
        repository=repository,
        provider=krforest.KRFOREST_PROVIDER,
        dataset_key=krforest.KRFOREST_MOUNTAIN_DATASET,
        records=[_mountain(), _mountain(obs_id="M002", obs_name="설악산", latitude=None)],
        to_location=krforest.station_location,
        to_values=krforest.mountain_weather_to_weather_values,
        build_source_record=krforest.mountain_source_record,
        max_values=1000,
    )

    assert result["locations_anchored"] == 1
    assert result["skipped_without_coordinates"] == 1
    assert result["values_loaded"] > 0
    assert result["values_truncated"] is False
    anchor = repository.get_location("krforest-M001")
    assert anchor is not None
    assert anchor.name == "지리산"
    point = krforest.measurement_point_metadata(anchor)
    assert point is not None
    assert point["network"] == "산악기상관측망"


def _publish_mountains(records: list[Any], *, max_values: int) -> dict[str, Any]:
    repository = WeatherRepository(TEST_DATABASE_URL)
    repository.create_schema()
    return publish_regional_records(
        repository=repository,
        provider=krforest.KRFOREST_PROVIDER,
        dataset_key=krforest.KRFOREST_MOUNTAIN_DATASET,
        records=records,
        to_location=krforest.station_location,
        to_values=krforest.mountain_weather_to_weather_values,
        build_source_record=krforest.mountain_source_record,
        max_values=max_values,
    )


def test_publish_cuts_between_stations_and_reports_the_cut() -> None:
    """The budget must never split one station's readings.

    Half a station's metrics, published as though complete, is worse than
    fewer stations: a reader cannot tell an absent measurement from one the
    budget dropped.  So the cut lands between stations, and it is reported --
    a silent cap is indistinguishable from a quiet upstream.
    """
    one_station = len(
        krforest.mountain_weather_to_weather_values(
            _mountain(), location_id="l", source_record_key="s", known_at=KNOWN_AT
        )
    )
    assert one_station > 1, "fixture produces too few values to test a cut"

    exact = _publish_mountains(
        [_mountain(), _mountain(obs_id="M003", obs_name="덕유산")],
        max_values=one_station,
    )
    assert exact["values_loaded"] == one_station
    assert exact["source_records"] == 1
    assert exact["values_truncated"] is True

    # A budget below a single station admits nothing at all, rather than a
    # partial reading set.  That is a real outcome an operator has to be able
    # to see, so it must be reported rather than looking like an empty fetch.
    too_small = _publish_mountains([_mountain()], max_values=one_station - 1)
    assert too_small["values_loaded"] == 0
    assert too_small["source_records"] == 0
    assert too_small["values_truncated"] is True
