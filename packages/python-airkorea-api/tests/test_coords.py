from __future__ import annotations

import pytest

from airkorea.coords import (
    LatLon,
    TmPoint,
    coerce_latlon,
    coerce_tm_point,
    resolve_airkorea_tm,
    tm_to_wgs84,
    validate_latlon,
    wgs84_to_tm,
)


def test_wgs84_to_airkorea_tm_default_matches_legacy_examples() -> None:
    tm_x, tm_y = wgs84_to_tm(37.5665, 126.9780)

    assert tm_x == pytest.approx(198242, abs=2)
    assert tm_y == pytest.approx(451580, abs=2)


def test_tm_to_wgs84_roundtrip() -> None:
    lat, lon = 37.5665, 126.9780
    tm_x, tm_y = wgs84_to_tm(lat, lon)

    out_lat, out_lon = tm_to_wgs84(tm_x, tm_y)

    assert out_lat == pytest.approx(lat, abs=1e-6)
    assert out_lon == pytest.approx(lon, abs=1e-6)


def test_coordinate_value_objects_roundtrip() -> None:
    point = LatLon(37.5665, 126.9780)
    tm = point.to_tm()

    assert point.latitude == 37.5665
    assert point.longitude == 126.9780
    assert point.as_tuple() == (37.5665, 126.9780)
    assert isinstance(tm, TmPoint)
    assert tm.as_tuple() == (tm.tm_x, tm.tm_y)
    assert tm.to_wgs84().lat == pytest.approx(point.lat, abs=1e-6)
    assert tm.to_wgs84().lon == pytest.approx(point.lon, abs=1e-6)


def test_coordinate_coercion_accepts_common_shapes() -> None:
    coord = LatLon(lat=37.5, lon=127.0)

    assert coerce_latlon(coord) == LatLon(37.5, 127.0)
    assert coerce_latlon((37.5, 127.0)) == LatLon(37.5, 127.0)
    assert coerce_latlon({"latitude": "37.5", "longitude": "127.0"}) == LatLon(37.5, 127.0)
    assert coerce_latlon(lat=37.5, lon=127.0) == LatLon(37.5, 127.0)
    assert coerce_tm_point((198242, 451580)) == TmPoint(198242.0, 451580.0)
    assert coerce_tm_point({"tmX": "198242", "tmY": "451580"}) == TmPoint(198242.0, 451580.0)


def test_resolve_airkorea_tm_rejects_mixed_coordinate_modes() -> None:
    resolved = resolve_airkorea_tm(coordinate=LatLon(lat=37.5665, lon=126.9780))

    assert resolved.tm_x == pytest.approx(198242, abs=2)
    assert resolved.tm_y == pytest.approx(451580, abs=2)

    with pytest.raises(ValueError):
        resolve_airkorea_tm(coordinate=LatLon(37.5, 127.0), tm=TmPoint(1, 2))

    with pytest.raises(ValueError):
        resolve_airkorea_tm()


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        (-91.0, 127.0),
        (91.0, 127.0),
        (37.0, -181.0),
        (37.0, 181.0),
    ],
)
def test_validate_latlon_rejects_out_of_range(lat: float, lon: float) -> None:
    with pytest.raises(ValueError):
        validate_latlon(lat, lon)
