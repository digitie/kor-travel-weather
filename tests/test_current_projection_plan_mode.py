"""실제 PG FK·TX·savepoint와 projection 전용 plan 설정 복원을 검증한다."""

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_ingest_batch_work import _fact, _record, _repository

from kortravelweather.models import kst_now
from kortravelweather.repository import WeatherCurrentValueRow, WeatherValueRow, _ResolvedFact


def resolved(repository):
    clock = kst_now()
    value = _fact("plan-mode-source", clock)
    assert repository.ingest_batch(
        source_records=[_record("plan-mode-source", clock)], values=[value],
    ) == 1
    with repository._session_factory() as session:
        row = session.scalars(select(WeatherValueRow)).one()
        return _ResolvedFact(
            value=value, source_key=row.source_record_key, value_id=row.value_id,
            target_at=row.target_at, known_at=row.known_at, collected_at=row.collected_at,
        )


@pytest.mark.parametrize("mode", ["auto", "force_custom_plan", "force_generic_plan"])
def test_projection_restores_caller_mode_and_keeps_exact_pointer(weather_test_database_url, mode):
    repository = _repository(weather_test_database_url)
    try:
        fact = resolved(repository)
        with repository.engine.connect() as connection:
            connection.execute(text("SELECT set_config('plan_cache_mode', :mode, false)"),
                               {"mode": mode})
            connection.commit()
            with Session(bind=connection) as session, session.begin():
                before = session.execute(select(WeatherCurrentValueRow.__table__)).all()
                modes = []

                def observe(_c, cursor, sql, _params, _ctx, _many):
                    if sql.lstrip().startswith("INSERT INTO weather_current_values"):
                        cursor.execute("SHOW plan_cache_mode")
                        modes.append(cursor.fetchone()[0])

                event.listen(connection, "before_cursor_execute", observe)
                try:
                    repository._refresh_current_projection_session(session, [fact])
                finally:
                    event.remove(connection, "before_cursor_execute", observe)
                assert modes == ["force_generic_plan"]
                assert session.scalar(text("SHOW plan_cache_mode")) == mode
                assert session.execute(select(WeatherCurrentValueRow.__table__)).all() == before
            assert connection.scalar(text("SHOW plan_cache_mode")) == mode
    finally:
        repository.engine.dispose()


@pytest.mark.parametrize("mode", ["auto", "force_custom_plan", "force_generic_plan"])
@pytest.mark.parametrize("savepoint", [False, True])
def test_bad_fact_fk_keeps_original_error_and_restores_after_rollback(
    weather_test_database_url, mode, savepoint,
):
    repository = _repository(weather_test_database_url)
    try:
        fact = resolved(repository)
        bad = replace(fact, value_id="no-such-native-fact")
        with repository.engine.connect() as connection:
            connection.execute(text("SELECT set_config('plan_cache_mode', :mode, false)"),
                               {"mode": mode})
            connection.commit()
            with Session(bind=connection) as session:
                if savepoint:
                    with session.begin():
                        with pytest.raises(IntegrityError) as caught, session.begin_nested():
                            session.execute(delete(WeatherCurrentValueRow))
                            repository._refresh_current_projection_session(session, [bad])
                        assert session.scalar(text("SHOW plan_cache_mode")) == mode
                        assert session.scalar(text("SELECT 1")) == 1
                        assert (
                            session.scalar(select(WeatherCurrentValueRow.value_id)) == fact.value_id
                        )
                else:
                    with pytest.raises(IntegrityError) as caught, session.begin():
                        session.execute(delete(WeatherCurrentValueRow))
                        repository._refresh_current_projection_session(session, [bad])
                assert caught.value.orig.sqlstate == "23503"
                assert connection.scalar(text("SHOW plan_cache_mode")) == mode
                connection.rollback()
                assert connection.scalar(select(WeatherCurrentValueRow.value_id)) == fact.value_id
    finally:
        repository.engine.dispose()


def test_projection_empty_input_does_not_change_plan_mode(weather_test_database_url):
    repository = _repository(weather_test_database_url)
    try:
        statements = []

        def observe(_c, _cursor, sql, _params, _ctx, _many):
            statements.append(sql)

        with repository._session_factory.begin() as session:
            event.listen(repository.engine, "before_cursor_execute", observe)
            try:
                repository._refresh_current_projection_session(session, [])
            finally:
                event.remove(repository.engine, "before_cursor_execute", observe)
        assert statements == []
    finally:
        repository.engine.dispose()


@pytest.mark.parametrize("failure", [RuntimeError("injected projection failure"),
                                     asyncio.CancelledError("injected projection cancel")])
def test_projection_original_failure_rolls_back_local_mode_and_pointer(
    weather_test_database_url, failure,
):
    repository = _repository(weather_test_database_url)
    try:
        fact = resolved(repository)
        with repository.engine.connect() as connection:
            connection.execute(text("SET plan_cache_mode = 'force_custom_plan'"))
            connection.commit()

            def fail(_c, _cursor, sql, _params, _ctx, _many):
                if sql.lstrip().startswith("INSERT INTO weather_current_values"):
                    raise failure

            with Session(bind=connection) as session:
                event.listen(connection, "before_cursor_execute", fail)
                try:
                    with pytest.raises(type(failure)) as caught, session.begin():
                        session.execute(delete(WeatherCurrentValueRow))
                        repository._refresh_current_projection_session(session, [fact])
                finally:
                    event.remove(connection, "before_cursor_execute", fail)
                assert caught.value is failure
                # SQLAlchemy는 BaseException 취소 시 물리 연결을 폐기한다. 새 연결은
                # 서버 기본값이어야 하며, 일반 오류는 같은 연결의 호출자 값을 보존한다.
                invalidated = connection.invalidated
                assert invalidated is isinstance(failure, asyncio.CancelledError)
                expected_mode = "auto" if invalidated else "force_custom_plan"
                assert connection.scalar(text("SHOW plan_cache_mode")) == expected_mode
                connection.rollback()
                assert connection.scalar(select(WeatherCurrentValueRow.value_id)) == fact.value_id
    finally:
        repository.engine.dispose()


def test_projection_legacy_null_source_keeps_newest_revision(weather_test_database_url):
    repository = _repository(weather_test_database_url)
    try:
        clock = kst_now()
        first = _fact("a-source", clock)
        newest = _fact("z-source", clock)
        assert repository.ingest_batch(
            source_records=[_record("a-source", clock), _record("z-source", clock)],
            values=[first, newest],
        ) == 2
        with repository._session_factory.begin() as session:
            row = session.scalars(
                select(WeatherValueRow).where(WeatherValueRow.source_record_key == "a-source")
            ).one()
            older = _ResolvedFact(
                value=first, source_key=row.source_record_key, value_id=row.value_id,
                target_at=row.target_at, known_at=row.known_at, collected_at=row.collected_at,
            )
            winner = session.scalar(select(WeatherCurrentValueRow.value_id))
            session.execute(update(WeatherCurrentValueRow).values(source_record_key=None))
            repository._refresh_current_projection_session(session, [older])
            assert session.scalar(select(WeatherCurrentValueRow.value_id)) == winner
            assert session.scalar(select(WeatherCurrentValueRow.source_record_key)) is None
            assert session.scalar(text("SHOW plan_cache_mode")) == "auto"
    finally:
        repository.engine.dispose()
