"""대표 위치는 실제 주소 소속과 지점 좌표를 보존한다."""

from datetime import UTC, datetime

from kortravelweather.regional_locations import representative_locations


def point(key, lon, *, sido="경기도", sigungu="수원시", lat=37):
    return {
        "location_id": key,
        "longitude": lon,
        "latitude": lat,
        "coordinate_key": f"{lon},{lat}",
        "sido_name": sido,
        "sigungu_name": sigungu,
        "updated_at": datetime(2026, 11, 13, tzinfo=UTC),
    }


def test_representatives_are_actual_members_not_centroid_or_weather_averages():
    rows = [point("a", 126), point("b", 127), point("c", 129, sigungu="용인시")]
    result = representative_locations(rows)
    sido = next(row for row in result if row["level"] == "sido")
    assert sido["location_id"] == "b"
    assert sido["member_count"] == 3
    assert len(result) == 3
    assert sido["location_updated_at"] == rows[1]["updated_at"]
    assert "average_temperature" not in sido
    assert representative_locations(reversed(rows)) == result


def test_tied_real_points_use_stable_id_and_keep_original_region_names():
    rows = [point("b", 126), point("a", 128)]
    result = representative_locations(rows)
    assert all(row["location_id"] == "a" for row in result)
    assert result[0]["label"] == "경기도"
    assert result[0]["sido_name"] == "경기도"
    assert representative_locations(reversed(rows)) == result


def test_unknown_regions_and_invalid_coordinates_are_not_guessed():
    rows = [
        point("bad", float("nan")),
        point("unknown", 127, sido=None),
        point("sido-only", 127, sigungu=None),
    ]
    result = representative_locations(rows)
    assert len(result) == 1
    assert result[0]["level"] == "sido"
    assert result[0]["location_id"] == "sido-only"


def _seed_repository(database_url):
    from kortravelcommon.address_providers import VworldKakaoAddressResolver
    from kortravelcommon.reverse_address import AddressTarget
    from sqlalchemy import insert

    from kortravelweather.models import WeatherLocation
    from kortravelweather.repository import Base, WeatherRepository

    repo = WeatherRepository(database_url)
    repo.create_schema()
    points = [point("a", 126), point("b", 127), point("c", 129, sigungu="용인시")]
    for row in points:
        repo.upsert_location(
            WeatherLocation(
                location_id=row["location_id"],
                name=row["location_id"],
                longitude=row["longitude"],
                latitude=row["latitude"],
                metadata={"private": "not-public"},
            )
        )
        with repo.engine.begin() as connection:
            connection.execute(
                insert(Base.metadata.tables["reverse_address_entries"]),
                {
                    "provider_id": VworldKakaoAddressResolver.provider_id,
                    "target_key": row["location_id"],
                    "coordinate_key": AddressTarget(
                        row["location_id"], row["longitude"], row["latitude"]
                    ).coordinate_key,
                    "longitude": row["longitude"],
                    "latitude": row["latitude"],
                    "status": "ok",
                    "failures": 0,
                    "checked_at": row["updated_at"],
                    "payload": {
                        "region": {"sido": row["sido_name"], "sigungu": row["sigungu_name"]},
                        "private": "not-public",
                    },
                },
            )
    return repo


def test_projection_serves_bounded_public_references_and_rejects_changed_anchor(
    weather_test_database_url,
):
    from kortravelweather.models import WeatherLocation

    repo = _seed_repository(weather_test_database_url)
    try:
        assert repo.region_locations()["ready"] is False
        assert repo.refresh_region_locations()
        result = repo.region_locations()
        assert result["ready"] and len(result["items"]) == 3
        chosen = next(row for row in result["items"] if row["level"] == "sido")
        assert chosen["location"]["location_id"] == "b"
        assert chosen["location"]["metadata"] == {}
        assert "private" not in str(result)
        repo.upsert_location(
            WeatherLocation(location_id="b", name="변경된 지점", longitude=127, latitude=37)
        )
        assert repo.region_locations()["ready"] is False
        assert repo.region_locations()["items"] == []
        assert repo.refresh_region_locations()
        assert (
            next(row for row in repo.region_locations()["items"] if row["level"] == "sido")[
                "location"
            ]["name"]
            == "변경된 지점"
        )
    finally:
        repo.engine.dispose()


def test_projection_skips_busy_writer_and_rolls_back_failed_publication(weather_test_database_url):
    import pytest
    from sqlalchemy import event, text

    repo = _seed_repository(weather_test_database_url)
    try:
        assert repo.refresh_region_locations()
        original = repo.region_locations()
        with repo.engine.begin() as owner:
            owner.execute(text("SELECT pg_advisory_xact_lock(1465017671, 1)"))
            assert repo.refresh_region_locations() is False
            assert repo.region_locations() == original

        def fail_publication(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.startswith("INSERT INTO weather_region_projection_states"):
                raise RuntimeError("fixture publication failure")

        event.listen(repo.engine, "before_cursor_execute", fail_publication)
        try:
            with pytest.raises(RuntimeError, match="fixture publication failure"):
                repo.refresh_region_locations()
        finally:
            event.remove(repo.engine, "before_cursor_execute", fail_publication)
        assert repo.region_locations() == original
    finally:
        repo.engine.dispose()


def test_changed_address_coordinate_key_invalidates_old_projection(weather_test_database_url):
    from sqlalchemy import update

    from kortravelweather.repository import Base

    repo = _seed_repository(weather_test_database_url)
    try:
        assert repo.refresh_region_locations()
        assert repo.region_locations()["ready"]
        addresses = Base.metadata.tables["reverse_address_entries"]
        with repo.engine.begin() as connection:
            connection.execute(
                update(addresses)
                .where(addresses.c.target_key == "b")
                .values(coordinate_key="different-position")
            )
        assert repo.region_locations()["ready"] is False
        assert repo.region_locations()["items"] == []
        assert repo.refresh_region_locations()
        assert repo.region_locations()["ready"]
        assert all(
            row["location"]["location_id"] != "b" for row in repo.region_locations()["items"]
        )
    finally:
        repo.engine.dispose()
