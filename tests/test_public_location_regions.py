"""지역 대표값은 좌표가 일치하는 파생 주소만 사용한다."""
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, insert
from kortravelcommon.address_providers import VworldKakaoAddressResolver
from kortravelcommon.reverse_address import AddressTarget
from kortravelweather.repository import Base, WeatherRepository


def test_regions_never_expose_metadata_or_reuse_address_after_coordinate_move():
    engine = create_engine("sqlite://")
    table = Base.metadata.tables["reverse_address_entries"]
    table.create(engine)
    point = SimpleNamespace(location_id="point", longitude=127.0, latitude=37.0)
    with engine.begin() as connection:
        connection.execute(insert(table), dict(
            provider_id=VworldKakaoAddressResolver.provider_id, target_key="point",
            coordinate_key=AddressTarget("point", 127.0, 37.0).coordinate_key,
            longitude=127.0, latitude=37.0, status="ok", failures=0,
            checked_at=datetime.now(timezone.utc),
            payload={"region": {"sido": "경기도", "sigungu": "용인시"}, "private": "not-public"},
        ))
    repository = WeatherRepository.__new__(WeatherRepository)
    repository.engine = engine
    try:
        assert repository.public_location_regions([point]) == {"point": {"sido_name": "경기도", "sigungu_name": "용인시"}}
        point.longitude = 128.0
        assert repository.public_location_regions([point]) == {}
        assert repository.public_location_regions([]) == {}
    finally:
        engine.dispose()
