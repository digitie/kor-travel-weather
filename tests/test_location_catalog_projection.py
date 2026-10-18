"""카탈로그 조회의 DTO/정렬/필터와 JSON null 호환성을 검증한다."""

from datetime import UTC, datetime

from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import sessionmaker

from kortravelweather.repository import WeatherLocationRow, WeatherRepository


def catalog_repository():
    # PostgreSQL 운영 계약을 우회하는 인메모리 단일 테이블 테스트 더블이다.
    engine = create_engine("sqlite://")
    WeatherLocationRow.__table__.create(engine)
    timestamp = datetime.now(UTC)
    rows = [
        dict(location_id="b", name="Same", enabled=True, metadata={"measurement_point": {"id": 2}}),
        dict(location_id="a", name="Same", enabled=False, metadata={}),
        dict(location_id="c", name="Zulu", enabled=True, metadata=None),
    ]
    with engine.begin() as connection:
        connection.execute(
            insert(WeatherLocationRow),
            [
                dict(
                    latitude=37.123456,
                    longitude=127.123456,
                    created_at=timestamp,
                    updated_at=timestamp,
                    **row,
                )
                for row in rows
            ],
        )
    repo = object.__new__(WeatherRepository)
    repo._session_factory = sessionmaker(engine)
    return repo, engine


def test_catalog_projection_preserves_metadata_filters_and_paging():
    repo, engine = catalog_repository()
    try:
        rows = repo.list_locations(limit=None)
        assert [row.location_id for row in rows] == ["a", "b", "c"]
        assert rows[1].metadata == {"measurement_point": {"id": 2}}
        assert rows[2].metadata == {}
        assert rows[1].latitude == 37.123456
        assert [row.location_id for row in repo.list_locations(enabled_only=True)] == ["b", "c"]
        assert [
            row.location_id for row in repo.list_locations(search=" Same ", limit=1, offset=1)
        ] == ["b"]
        assert all(row.metadata == {} for row in repo.list_locations(include_metadata=False))
        rows[1].metadata["measurement_point"]["id"] = 99
        assert repo.list_locations(search="b")[0].metadata["measurement_point"]["id"] == 2
    finally:
        engine.dispose()


def test_catalog_projection_does_not_materialize_orm_entities():
    repo, engine = catalog_repository()
    loaded = []
    statements = []

    def record_load(instance, context):
        loaded.append(instance)

    def record_sql(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(WeatherLocationRow, "load", record_load)
    event.listen(engine, "before_cursor_execute", record_sql)
    try:
        assert len(repo.list_locations(limit=None)) == 3
        assert loaded == []
        assert len(statements) == 1
        assert "created_at" not in statements[0]
        assert "updated_at" not in statements[0]
    finally:
        event.remove(WeatherLocationRow, "load", record_load)
        engine.dispose()
