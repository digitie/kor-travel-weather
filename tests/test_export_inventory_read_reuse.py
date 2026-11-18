"""같은 RR의 generation 재사용은 새 owner의 실패·publication 검사를 생략하지 않는다."""

import uuid

from sqlalchemy import event, select
from test_export_external import external_repository as external_repository
from test_export_inventory import _publish

from kortravelweather.export_heads import ExportHeads
from kortravelweather.export_inventory_selection import _active_scope_states
from kortravelweather.models import WeatherLocation
from kortravelweather.providers import ProviderLocation


def test_same_rr_reuses_known_publications_but_queries_new_active_owner(
    external_repository, tmp_path,
):
    repo = external_repository
    target = ProviderLocation("reuse-" + uuid.uuid4().hex, 37.5, 127, {})
    repo.create_location(WeatherLocation(
        location_id=target.location_id, name="reuse", latitude=37.5, longitude=127,
    ))
    _publish(repo, tmp_path, [target])
    heads = ExportHeads(repo)
    owners = heads.lifecycle.owners
    calls = []

    def observe(connection, cursor, statement, parameters, context, many):
        if ("FROM weather_export_generations" in statement
                and "WHERE weather_export_generations.attempt_id" in statement):
            calls.append(1)

    event.listen(repo.engine, "before_cursor_execute", observe)
    try:
        with (repo.engine.connect().execution_options(
            isolation_level="REPEATABLE READ",
        ) as c, c.begin()):
            known = dict(c.execute(select(
                heads.generations.c.attempt_id, heads.generations.c.generation_id,
            )).all())
            scopes = set(c.scalars(select(owners.c.source_scope)))
            baseline = _active_scope_states(c, heads.metadata, scopes)
            assert len(calls) == 1
            calls.clear()
            assert _active_scope_states(
                c, heads.metadata, scopes, known_publications=known,
            ) == baseline
            assert not calls
        # A fresh failed owner has no approved generation. Its state must be freshly read.
        run = repo.start_sync_run(provider="open_meteo", dataset_key="open_meteo_current")
        identity = repo.begin_export_attempt(
            run_id=run.run_id, scope_key="new-owner", planned_sources=sorted(scopes),
            execution_mode="native",
        )
        heads.lifecycle.claim(identity)
        repo.finish_sync_run(run.run_id, status="failed")
        with (repo.engine.connect().execution_options(
            isolation_level="REPEATABLE READ",
        ) as c, c.begin()):
            baseline = _active_scope_states(c, heads.metadata, scopes)
            calls.clear()
            result = _active_scope_states(
                c, heads.metadata, scopes, known_publications=known,
            )
            assert result == baseline
            assert len(calls) == 1
            assert any(row[2] == run.run_id and row[4] is None and row[5] == "failed"
                       for row in result)
    finally:
        event.remove(repo.engine, "before_cursor_execute", observe)
