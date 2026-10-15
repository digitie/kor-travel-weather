"""위치 목록 인덱스의 실행 계획과 데이터 보존을 PostgreSQL에서 확인한다."""

from __future__ import annotations

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import get_settings


def test_catalog_index_avoids_sort_and_preserves_upgrade_downgrade_rows(
    monkeypatch, weather_test_database_url: str,
) -> None:
    database_url = weather_test_database_url
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_DATABASE_URL", database_url)
    get_settings.cache_clear()
    engine = WeatherRepository(database_url).engine
    query = (
        "SELECT location_id,name,latitude,longitude,nx,ny,region_code,enabled "
        "FROM weather_locations WHERE enabled IS TRUE ORDER BY name,location_id "
        "LIMIT 1000 OFFSET 0"
    )
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP SCHEMA public CASCADE")
            conn.exec_driver_sql("CREATE SCHEMA public")
        config = Config("alembic.ini")
        command.upgrade(config, "0018_sync_run_owner")
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO weather_locations "
                "(location_id,name,latitude,longitude,enabled,created_at,updated_at) "
                "SELECT 'catalog-' || n, "
                "CASE WHEN n % 3 = 0 THEN '가나다' "
                "WHEN n % 3 = 1 THEN 'A 가나' ELSE '나 01' END, "
                "37,127,n % 31 <> 0,now(),now() FROM generate_series(1,1500) AS n"
            ))
            conn.exec_driver_sql("ANALYZE weather_locations")
        with engine.connect() as conn:
            original = conn.execute(text(query)).all()
        command.upgrade(config, "head")
        with engine.connect() as conn:
            after = conn.execute(text(query)).all()
            assert after == original and len(after) == 1000
            valid, ready = conn.execute(text(
                "SELECT indisvalid,indisready FROM pg_index "
                "WHERE indexrelid='ix_weather_locations_enabled_name_id'::regclass"
            )).one()
            assert valid and ready
            plan = conn.execute(text("EXPLAIN (FORMAT JSON) " + query)).scalar_one()
            nodes = []

            def collect(node):
                nodes.append(node)
                for child in node.get("Plans", []):
                    collect(child)

            collect(plan[0]["Plan"])
            assert not any(node["Node Type"] == "Sort" for node in nodes)
            assert any(node.get("Index Name") == "ix_weather_locations_enabled_name_id"
                       for node in nodes)
        command.downgrade(config, "0018_sync_run_owner")
        with engine.connect() as conn:
            assert conn.execute(text(query)).all() == original
            assert conn.execute(text("SELECT count(*) FROM weather_locations")).scalar_one() == 1500
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert conn.execute(text(query)).all() == original
    finally:
        engine.dispose()
        get_settings.cache_clear()
