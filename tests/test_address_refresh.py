"""파생 주소 갱신은 원천·좌표·관리자 설정을 수정하지 않는다."""

from datetime import UTC, datetime
from types import SimpleNamespace

from kortravelcommon.reverse_address import AddressResolver, reverse_address_tables
from sqlalchemy import MetaData, create_engine, insert, select

from kortravelweather.address_refresh import address_source, run_address_refresh
from kortravelweather.repository import WeatherLocationRow
from kortravelweather.settings import WeatherSettings


class Resolver(AddressResolver):
    provider_id = "vworld-kakao-reverse-v1"

    def reverse(self, target):
        return {
            "provider": "fixture",
            "address": {"full": "새 주소"},
            "region": {"sido": "새 지역"},
        }


def test_current_address_is_separate_from_weather_source(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "address.db"))
    metadata = MetaData()
    reverse_address_tables(metadata)
    WeatherLocationRow.__table__.to_metadata(metadata)
    metadata.create_all(engine)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            insert(WeatherLocationRow.__table__),
            [
                {
                    "location_id": "active",
                    "name": "기준 지점",
                    "longitude": 127,
                    "latitude": 37,
                    "region_code": "원천 지역",
                    "nx": 60,
                    "ny": 127,
                    "enabled": True,
                    "metadata": {"admin_note": "보존"},
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "location_id": "disabled",
                    "name": "비활성",
                    "longitude": 127,
                    "latitude": 37,
                    "enabled": False,
                    "region_code": None,
                    "nx": None,
                    "ny": None,
                    "metadata": {},
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
    repository = SimpleNamespace(engine=engine)
    settings = WeatherSettings(_env_file=None, environment="development")
    try:
        result = run_address_refresh(repository, settings, force=True, resolver=Resolver())
        assert result["processed"] == result["requests"] == 1
        source = address_source(repository)
        assert source.summary()["total"] == 1
        assert source.sample()[0]["address"]["full"] == "새 주소"
        with engine.connect() as conn:
            row = (
                conn.execute(
                    select(WeatherLocationRow.__table__).where(
                        WeatherLocationRow.location_id == "active"
                    )
                )
                .mappings()
                .one()
            )
        assert row["region_code"] == "원천 지역"
        assert row["metadata"] == {"admin_note": "보존"}
        assert (row["nx"], row["ny"], float(row["longitude"]), float(row["latitude"])) == (
            60,
            127,
            127,
            37,
        )
    finally:
        engine.dispose()
