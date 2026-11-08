"""quota 회전에 선택하지 않은 위치의 승인과 파일 root를 잃지 않는다."""

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from test_export_external import (
    FakeResponse,
    FixtureTransport,
    _external_payload,
)
from test_export_external import (
    external_repository as external_repository,
)

from kortravelweather.export_artifacts import ExportArtifactStore
from kortravelweather.export_heads import ExportHeads, parse_scope_descriptor
from kortravelweather.export_inventory import (
    bootstrap_approved_locations,
    export_inventory_table,
    read_approved_locations,
    record_approved_locations,
)
from kortravelweather.export_receipts import ExportAttempt
from kortravelweather.models import WeatherLocation
from kortravelweather.providers import OpenMeteoProvider, ProviderLocation


def _publish(repo, root, locations, dataset_key="open_meteo_current"):
    from kortravelweather_dagster.external_weather import run_external_weather_sync

    forecast = dataset_key.endswith("forecast")
    result = run_external_weather_sync(
        repository=repo, provider=OpenMeteoProvider(transport=FixtureTransport(*[
            FakeResponse(_external_payload("open_meteo", forecast=forecast, at=datetime.now(UTC)))
            for _ in locations
        ])), targets=locations, dataset_key=dataset_key, export_artifact_root=root,
    )
    heads = ExportHeads(repo)
    with repo.engine.connect() as c:
        identities = c.execute(select(
            heads.lifecycle.attempts.c.attempt_id, heads.lifecycle.attempts.c.owner_id,
        ).where(heads.lifecycle.attempts.c.run_id == result["run_id"])).all()
    for identity in identities:
        assert heads.promote(ExportAttempt(*identity), ExportArtifactStore(root)) == "PUBLISHED"
    with repo.engine.connect() as c:
        return {row["location_id"]: dict(row) for row in read_approved_locations(
            c, heads.metadata, providers=["open_meteo"], dataset_keys=["open_meteo_current"],
        )}


def test_quota_rotation_updates_selected_only_and_older_projection_cannot_overwrite(
    external_repository, tmp_path,
):
    repo = external_repository
    targets = [ProviderLocation("inventory-" + uuid.uuid4().hex, 37.5, 127, {}) for _ in range(2)]
    for target in targets:
        repo.create_location(WeatherLocation(
            location_id=target.location_id, name="quota 회전", latitude=37.5, longitude=127,
        ))
    original = _publish(repo, tmp_path, targets)
    old = original[targets[0].location_id]
    updated = _publish(repo, tmp_path, targets[:1])
    assert set(updated) == set(original)
    assert updated[targets[1].location_id] == original[targets[1].location_id]
    assert updated[targets[0].location_id]["published_at"] > old["published_at"]
    selection = _selection(repo)
    assert not selection.unconfirmed
    assert set(key for scope in selection.scopes for key in scope.location_ids) == set(updated)
    heads = ExportHeads(repo)
    with heads._transaction() as s:
        descriptor = parse_scope_descriptor(s.scalar(select(heads.plans.c.descriptor).where(
            heads.plans.c.namespace == old["namespace"]
        ).limit(1)))
        record_approved_locations(
            s, heads.metadata, descriptor=descriptor, generation_id=old["generation_id"],
        )
    with repo.engine.connect() as c:
        after = {r["location_id"]: dict(r) for r in read_approved_locations(
            c, heads.metadata, providers=["open_meteo"], dataset_keys=["open_meteo_current"],
        )}
    assert after == updated


def _selection(repo, *, expected=None, optional=()):
    from kortravelweather.export_inventory_selection import (
        approved_source_selection,
        catalog_source_projection,
    )

    heads = ExportHeads(repo)
    catalog = heads.metadata.tables["weather_locations"]
    with repo.engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
        c.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
        source_from, source_columns = catalog_source_projection(catalog)
        locations = c.execute(select(
            catalog.c.location_id, catalog.c.latitude, catalog.c.longitude,
            catalog.c.nx, catalog.c.ny,
            *source_columns,
        ).select_from(source_from).where(
            catalog.c.enabled.is_(True),
        )).mappings().all()
        return approved_source_selection(
            c, heads.metadata, locations,
            expected=expected or [("open_meteo", "open_meteo_current")], optional=optional,
        )


def test_inventory_health_ignores_inactive_or_unrelated_owner_and_rejects_epoch_mismatch(
    external_repository, tmp_path,
):
    repo = external_repository
    target = ProviderLocation("owner-health-" + uuid.uuid4().hex, 37.5, 127, {})
    repo.create_location(WeatherLocation(
        location_id=target.location_id, name="현재 epoch", latitude=37.5, longitude=127,
    ))
    before = _publish(repo, tmp_path, [target])
    heads = ExportHeads(repo)
    with repo.engine.connect() as connection:
        old_attempt = connection.scalar(select(heads.generations.c.attempt_id).where(
            heads.generations.c.generation_id == before[target.location_id]["generation_id"],
        ))
    _publish(repo, tmp_path, [target])
    with repo.engine.begin() as connection:
        connection.execute(heads.lifecycle.attempts.update().where(
            heads.lifecycle.attempts.c.attempt_id == old_attempt,
        ).values(status="failed"))
    assert not _selection(repo).unconfirmed
    unrelated = repo.start_sync_run(provider="open_meteo", dataset_key="open_meteo_current")
    from kortravelweather.export_receipts import source_scope_key

    unrelated_scope = source_scope_key(
        "open_meteo", "open_meteo_current", "weather_response", "unrelated-location",
    )
    identity = repo.begin_export_attempt(
        run_id=unrelated.run_id, scope_key="other-real-source",
        planned_sources=[unrelated_scope], execution_mode="native",
    )
    heads.lifecycle.claim(identity)
    repo.finish_sync_run(unrelated.run_id, status="failed")
    assert not _selection(repo).unconfirmed
    with repo.engine.begin() as connection:
        connection.execute(heads.lifecycle.owners.update().where(
            heads.lifecycle.owners.c.source_scope != unrelated_scope,
        ).values(owner_epoch=heads.lifecycle.owners.c.owner_epoch + 1))
    assert _selection(repo).unconfirmed


def test_selection_preserves_failed_intent_and_distinguishes_optional_from_required(
    external_repository, tmp_path,
):
    from kortravelweather.export_artifacts import ExportArtifactError

    repo = external_repository
    target = ProviderLocation("source-select-" + uuid.uuid4().hex, 37.5, 127, {})
    repo.create_location(WeatherLocation(
        location_id=target.location_id, name="미확인 source", latitude=37.5, longitude=127,
    ))
    _publish(repo, tmp_path, [target])
    assert not _selection(repo).unconfirmed
    required = [("open_meteo", "open_meteo_current"), ("python-krforest-api", "krforest_dust")]
    with pytest.raises(ExportArtifactError, match="최초 승인"):
        _selection(repo, expected=required)
    assert _selection(repo, expected=required, optional=[required[1]]).unconfirmed
    failed = repo.start_sync_run(provider="open_meteo", dataset_key="open_meteo_current",
                                 export_cohort_required=True)
    repo.finish_sync_run(failed.run_id, status="failed")  # roster 쓰기 이전 native 실패
    stale = _selection(repo)
    assert stale.unconfirmed and target.location_id in {
        key for scope in stale.scopes for key in scope.location_ids
    }
    # native/admin은 fact가 있는 좌표 변경을 막는다. 외부 DB 이전의 SQL 변경도 거부한다.
    catalog = ExportHeads(repo).metadata.tables["weather_locations"]
    with repo.engine.begin() as c:
        c.execute(catalog.update().where(catalog.c.location_id == target.location_id).values(
            latitude=37.6,
        ))
    with pytest.raises(ExportArtifactError, match="좌표"):
        _selection(repo)
    with repo.engine.begin() as c:
        c.execute(catalog.update().where(catalog.c.location_id == target.location_id).values(
            latitude=37.5,
        ))


def test_selection_reads_run_id_only_for_cohort_check_and_survives_attempt_retention(
    external_repository, tmp_path,
):
    """위치마다 attempt·plan을 join하지 않는다. attempt 이력 정리는 승인 위치를 지우지 않는다."""
    from sqlalchemy import event

    from kortravelweather.export_external_cohorts import external_cohort_table

    repo = external_repository
    target = ProviderLocation("attempt-retention-" + uuid.uuid4().hex, 37.5, 127, {})
    repo.create_location(WeatherLocation(
        location_id=target.location_id, name="attempt 이력 정리", latitude=37.5, longitude=127,
    ))
    approved = _publish(repo, tmp_path, [target])[target.location_id]
    generation = approved["generation_id"]
    heads = ExportHeads(repo)

    def statements():
        seen = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            seen.append(" ".join(statement.split()))

        event.listen(repo.engine, "before_cursor_execute", capture)
        try:
            selection = _selection(repo)
        finally:
            event.remove(repo.engine, "before_cursor_execute", capture)
        run_ids = [s for s in seen if s.startswith(
            "SELECT weather_export_attempts.attempt_id, weather_export_attempts.run_id FROM"
        )]
        inventory = [s for s in seen if "FROM weather_export_approved_locations" in s]
        return selection, inventory, run_ids

    selection, inventory, run_ids = statements()
    assert not selection.unconfirmed and len(inventory) == 1
    assert run_ids == []  # 승인 attempt가 지금의 소유 attempt면 소유자 조회의 run_id를 다시 쓴다
    assert "weather_export_attempts" not in inventory[0]
    assert "weather_export_scope_plans" not in inventory[0]
    # 다음 수집이 소유권을 가져가 진행 중이어도 이전 승인은 확인된 채다(대조군). 그래서 아래의
    # 미확인은 소유권 검사가 아니라 이력 없는 run_id 대조에서만 나온다.
    rerun = repo.start_sync_run(provider="open_meteo", dataset_key="open_meteo_current")
    heads.lifecycle.claim(repo.begin_export_attempt(
        run_id=rerun.run_id, scope_key="attempt-retention-rerun",
        planned_sources=[approved["source_scope"]], execution_mode="native",
    ))
    assert not _selection(repo).unconfirmed
    with repo.engine.begin() as c:
        c.execute(delete(heads.lifecycle.attempts).where(
            heads.lifecycle.attempts.c.attempt_id == select(heads.generations.c.attempt_id).where(
                heads.generations.c.generation_id == generation,
            ).scalar_subquery(),
        ))
    retained, _, run_ids = statements()
    assert retained.unconfirmed  # 이력이 없으면 cohort 대조를 확인할 수 없다
    assert len(run_ids) == 1  # 소유자 조회에 없는 승인 attempt만 따로 찾는다
    assert target.location_id in {key for scope in retained.scopes for key in scope.location_ids}
    with repo.engine.begin() as c:
        c.execute(delete(external_cohort_table(heads.metadata)))
    _, _, run_ids = statements()
    assert run_ids == []  # cohort 대조가 없으면 run_id를 읽지 않는다


def _captured(repo, call):
    from sqlalchemy import event

    seen = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        seen.append((" ".join(statement.split()), parameters))

    event.listen(repo.engine, "before_cursor_execute", capture)
    try:
        return call(), seen
    finally:
        event.remove(repo.engine, "before_cursor_execute", capture)


def test_parsed_plans_are_remembered_per_attempt_and_a_lost_plan_is_still_detected(
    external_repository, tmp_path,
):
    """불변 plan은 프로세스가 한 번만 읽어 해석한다. 기억한 plan도 행이 사라지면 거절한다."""
    from kortravelweather import export_inventory_selection as selection_module
    from kortravelweather.export_artifacts import ExportArtifactError

    repo = external_repository
    targets = [ProviderLocation("plan-cache-" + uuid.uuid4().hex, 37.5, 127, {}) for _ in range(3)]
    for target in targets:
        repo.create_location(WeatherLocation(
            location_id=target.location_id, name="plan 기억", latitude=37.5, longitude=127,
        ))
    _publish(repo, tmp_path, targets)
    selection_module._parsed_plans.clear()
    selection_module._parsed_plans_bytes = 0

    def descriptor_reads(seen):
        return [s for s, _ in seen if "weather_export_scope_plans.descriptor" in s]

    first, seen = _captured(repo, lambda: _selection(repo))
    assert len(descriptor_reads(seen)) == 1
    second, seen = _captured(repo, lambda: _selection(repo))
    assert descriptor_reads(seen) == []  # 존재만 확인한다
    assert any("FROM weather_export_scope_plans WHERE" in s for s, _ in seen)
    assert [(s.descriptor, s.location_ids) for s in second.scopes] == [
        (s.descriptor, s.location_ids) for s in first.scopes
    ]
    # 누적 예산은 기억 여부와 무관하다(데워진 프로세스도 같은 결과).
    with pytest.MonkeyPatch.context() as tight:
        tight.setattr(selection_module, "MAX_MANIFEST_BYTES", 1)
        with pytest.raises(ExportArtifactError, match="예산"):
            _selection(repo)
    heads = ExportHeads(repo)
    with repo.engine.begin() as c:
        c.execute(delete(heads.plans).where(heads.plans.c.attempt_id == select(
            heads.generations.c.attempt_id,
        ).where(
            heads.generations.c.generation_id == first.scopes[0].generation_id,
        ).scalar_subquery()))
    with pytest.raises(ExportArtifactError, match="plan"):
        _selection(repo)


def test_owner_health_reads_run_status_per_run_and_big_lists_as_one_parameter(
    external_repository, tmp_path,
):
    """scope마다 run 행을 다시 찾지 않는다. 수천 개짜리 목록은 배열 파라미터 하나다."""
    from sqlalchemy import update

    repo = external_repository
    targets = [ProviderLocation("owner-run-" + uuid.uuid4().hex, 37.5, 127, {}) for _ in range(3)]
    for target in targets:
        repo.create_location(WeatherLocation(
            location_id=target.location_id, name="run 상태", latitude=37.5, longitude=127,
        ))
    _publish(repo, tmp_path, targets)
    selection, seen = _captured(repo, lambda: _selection(repo))
    assert not selection.unconfirmed
    owners = [s for s, _ in seen if "FROM weather_export_scope_owners" in s]
    assert len(owners) == 1
    # 복합 CTE 대신 같은 MVCC의 키 집합 네 개를 읽으며 scope 수에 비례한 SQL은 없다.
    assert "MATERIALIZED" not in owners[0]
    assert "JOIN" not in owners[0]
    run_queries = [s for s, _ in seen if "FROM weather_sync_runs" in s
                   and "weather_sync_runs.run_id = ANY" in s]
    assert len(run_queries) == 1 and " = ANY (" in run_queries[0]
    big = [(s, p) for s, p in seen if any(
        name in s for name in ("weather_export_scope_plans", "weather_export_heads.namespace",
                               "weather_export_scope_owners")
    )]
    # 목록 하나가 배열 파라미터 하나다(위치 3곳이라 개수로는 IN 목록과 구별되지 않는다).
    assert big and all(
        re.search(r"= ANY ?\(", s) and not re.search(r" IN \(%.+?\)", s) for s, _ in big
    ), [s[:160] for s, _ in big]
    heads = ExportHeads(repo)
    runs_table = heads.metadata.tables["weather_sync_runs"]
    with repo.engine.begin() as c:
        c.execute(update(runs_table).where(runs_table.c.run_id == select(
            heads.lifecycle.attempts.c.run_id,
        ).where(heads.lifecycle.attempts.c.attempt_id == select(
            heads.generations.c.attempt_id,
        ).where(heads.generations.c.generation_id == selection.scopes[0].generation_id)
            .scalar_subquery()).scalar_subquery()).values(status="failed"))
    assert _selection(repo).unconfirmed  # 같은 묶음의 run 상태도 건강도에 들어간다


def _age(repo, location_id, age):
    from sqlalchemy import update

    table = export_inventory_table(ExportHeads(repo).metadata)
    with repo.engine.begin() as c:
        c.execute(update(table).where(table.c.location_id == location_id).values(
            source_collected_at=datetime.now(UTC) - age,
        ))


def test_long_dead_location_expires_instead_of_dragging_freshness_or_partial_flag(
    external_repository, tmp_path,
):
    """응답에서 계속 빠지는 위치(같은 cohort 안)의 오래된 승인은 값·meta·미확인에서 빠진다.

    운영 2026-11-02: 그런 위치 몇 곳의 10-26 승인 때문에 current 전체 meta가 10-26·stale이었다.
    신선도 상한(3시간)과 만료(24시간) 사이는 계속 내보내고 stale로 표시한다.
    """
    from kortravelweather.export_inventory_selection import source_expiry, source_max_age

    repo = external_repository
    targets = [ProviderLocation("expiry-" + uuid.uuid4().hex, 37.5, 127, {}) for _ in range(2)]
    for target in targets:
        repo.create_location(WeatherLocation(
            location_id=target.location_id, name="만료", latitude=37.5, longitude=127,
        ))
    _publish(repo, tmp_path, targets)
    dead = targets[1].location_id
    source = ("open_meteo", "open_meteo_current")
    assert source_expiry(source[1]) == max(timedelta(hours=24), 2 * source_max_age(source[1]))
    _age(repo, dead, source_max_age(source[1]) + timedelta(hours=2))
    lagging = _selection(repo)
    assert lagging.unconfirmed  # 상한과 만료 사이: 계속 내보내되 stale
    assert dead in {key for scope in lagging.scopes for key in scope.location_ids}
    _age(repo, dead, source_expiry(source[1]) + timedelta(hours=1))
    selection = _selection(repo)
    assert dead not in {key for scope in selection.scopes for key in scope.location_ids}
    assert (*source, dead) in selection.expired
    assert not selection.unconfirmed  # 공백이지 이번 수집의 실패가 아니다
    assert min(scope.source_collected_at for scope in selection.scopes) > (
        datetime.now(UTC) - timedelta(hours=1)
    )


def test_fully_expired_required_source_is_served_without_it_and_marked_stale(
    external_repository, tmp_path,
):
    from kortravelweather.export_artifacts import ExportArtifactError
    from kortravelweather.export_inventory_selection import source_expiry

    repo = external_repository
    target = ProviderLocation("expired-source-" + uuid.uuid4().hex, 37.5, 127, {})
    repo.create_location(WeatherLocation(
        location_id=target.location_id, name="원천 만료", latitude=37.5, longitude=127,
    ))
    _publish(repo, tmp_path, [target])
    _age(repo, target.location_id, source_expiry("open_meteo_current") + timedelta(hours=1))
    # 유일한 source가 전부 만료면 낼 값이 없다(이전 완전 snapshot으로 fallback한다).
    with pytest.raises(ExportArtifactError, match="비었거나"):
        _selection(repo)
    # 다른 원천이 살아 있으면 만료한 원천 없이 내보내고 stale이다(최초 승인 거절·503이 아니다).
    other = ProviderLocation("live-source-" + uuid.uuid4().hex, 37.6, 127.1, {})
    repo.create_location(WeatherLocation(
        location_id=other.location_id, name="살아 있는 원천", latitude=37.6, longitude=127.1,
    ))
    _publish(repo, tmp_path, [other], "open_meteo_forecast")
    both = [("open_meteo", "open_meteo_current"), ("open_meteo", "open_meteo_forecast")]
    selection = _selection(repo, expected=both)
    assert selection.unconfirmed
    assert {scope.descriptor.dataset_key for scope in selection.scopes} == {"open_meteo_forecast"}
    assert ("open_meteo", "open_meteo_current", target.location_id) in selection.expired


def test_expiry_horizon_derives_from_freshness_and_spares_kma_rotation():
    """만료는 dataset 신선도 상한에서 파생한다. KMA 회전 slot(수 시간 안 갱신)은 만료되지 않는다."""
    from kortravelweather.export_inventory_selection import (
        source_expiry,
        source_max_age,
    )

    expected_hours = {
        "kma_ultra_short_nowcast": 24, "kma_ultra_short_forecast": 24, "kma_short_forecast": 24,
        "kma_mid_forecast": 36, "airkorea_realtime_measurement": 24, "open_meteo_current": 24,
        "weatherapi_forecast": 24, "krforest_mountain_weather": 72, "krex_restarea_weather": 72,
    }
    for dataset, hours in expected_hours.items():
        assert source_expiry(dataset) == timedelta(hours=hours), dataset
        assert source_expiry(dataset) > source_max_age(dataset)
    rotation_refresh = timedelta(hours=6)  # 운영: 단기 437 target/run 300 → 두 slot(3시간 간격)
    kma = [key for key in expected_hours if key.startswith("kma_")]
    assert all(source_expiry(dataset) >= 2 * rotation_refresh for dataset in kma)


def test_out_of_window_approval_is_served_but_not_counted_for_freshness(
    external_repository, tmp_path, monkeypatch,
):
    """신선도 창 밖·만료 전 승인은 값으로 내되 미확인·meta에서 뺀다(2026-11-07 소유자 결정).

    open_meteo current의 만료는 창과 같은 24시간이라 창을 1시간으로 좁혀 창과 만료 사이를 만든다.
    """
    from kortravelweather import export_inventory_selection as selection_module
    from kortravelweather.export_inventory_selection import freshness_min

    monkeypatch.setattr(selection_module, "FRESHNESS_WINDOW", timedelta(hours=1))
    repo = external_repository
    targets = [ProviderLocation("window-" + uuid.uuid4().hex, 37.5, 127, {}) for _ in range(2)]
    for target in targets:
        repo.create_location(WeatherLocation(
            location_id=target.location_id, name="창", latitude=37.5, longitude=127,
        ))
    _publish(repo, tmp_path, targets)
    slow = targets[1].location_id
    _age(repo, slow, timedelta(hours=4))  # 신선도 상한 3시간·창 1시간 밖, 만료 24시간 안
    selection = _selection(repo)
    assert slow in {key for scope in selection.scopes for key in scope.location_ids}  # 값은 낸다
    assert not selection.unconfirmed  # 다른 위치가 창 안이라 원천은 살아 있다
    now = datetime.now(UTC)
    fresh, old = now - timedelta(minutes=5), now - timedelta(hours=4)
    assert freshness_min([old, fresh], now) == fresh
    assert freshness_min([old], now) == old  # 창 안이 없으면 전체(나이 검사가 stale로 만든다)
    _age(repo, targets[0].location_id, timedelta(hours=4))
    assert _selection(repo).unconfirmed  # 원천 통째로 창 밖이면 미확인


def test_inventory_generation_is_a_live_root_even_after_head_replacement(
    external_repository, tmp_path,
):
    repo = external_repository
    target = ProviderLocation("inventory-pin-" + uuid.uuid4().hex, 37.5, 127, {})
    repo.create_location(WeatherLocation(
        location_id=target.location_id, name="파일 root", latitude=37.5, longitude=127,
    ))
    inventory = _publish(repo, tmp_path, [target])
    heads = ExportHeads(repo)
    generation = inventory[target.location_id]["generation_id"]
    # head FK만으로 보호된다는 가정을 배제한다. inventory만 남겨도 삭제 불가다.
    with heads._transaction() as s:
        s.execute(delete(heads.heads).where(heads.heads.c.generation_id == generation))
        s.execute(delete(heads.refs).where(heads.refs.c.generation_id == generation))
    with pytest.raises(IntegrityError), heads._transaction() as s:
        s.execute(delete(heads.generations).where(heads.generations.c.generation_id == generation))


def test_legacy_head_bootstrap_keyset_restores_inventory_without_new_collection(
    external_repository, tmp_path,
):
    repo = external_repository
    targets = [ProviderLocation("bootstrap-" + uuid.uuid4().hex, 37.5, 127, {}) for _ in range(3)]
    for target in targets:
        repo.create_location(WeatherLocation(
            location_id=target.location_id, name="이전 승인 복구", latitude=37.5, longitude=127,
        ))
    before = _publish(repo, tmp_path, targets)
    heads = ExportHeads(repo)
    table = export_inventory_table(heads.metadata)
    with heads._transaction() as s:
        s.execute(delete(table))
    cursor, seen = None, 0
    while True:
        result = bootstrap_approved_locations(repo, after_namespace=cursor, limit=1)
        assert result.visited <= 1
        seen += result.visited
        cursor = result.next_namespace
        if result.exhausted:
            break
    assert seen >= 3
    with repo.engine.connect() as c:
        restored = {r["location_id"]: dict(r) for r in read_approved_locations(
            c, heads.metadata, providers=["open_meteo"], dataset_keys=["open_meteo_current"],
        )}
    assert {target.location_id: restored[target.location_id] for target in targets} == {
        target.location_id: before[target.location_id] for target in targets
    }
    done = bootstrap_approved_locations(repo, after_namespace=cursor, limit=1)
    assert done.visited == 0 and done.exhausted


def test_source_cohort_requires_atomic_native_intent_and_preserves_exact_plan(external_repository):
    from kortravelweather.export_artifacts import ExportArtifactError
    from kortravelweather.export_external import RetrievalScopeDescriptor
    from kortravelweather.export_external_cohorts import external_cohort_table
    from kortravelweather.export_source_cohorts import declare_source_cohort

    repo = external_repository
    key = "source-plan-" + uuid.uuid4().hex
    repo.create_location(WeatherLocation(
        location_id=key, name="HTTP 전 source", latitude=37.5, longitude=127,
    ))
    descriptor = RetrievalScopeDescriptor(
        provider="open_meteo", dataset_key="open_meteo_current", entity_id=key,
        location_ids=(key,), forecast_style="observed", latitude=37.5, longitude=127,
        normalizer_revision="open_meteo-v1",
    )
    run = repo.start_sync_run(provider="open_meteo", dataset_key="open_meteo_current")
    with pytest.raises(ExportArtifactError, match="intent"):
        declare_source_cohort(repo, run_id=run.run_id, provider="open_meteo",
                              dataset_key="open_meteo_current", descriptors=[descriptor])
    repo.finish_sync_run(run.run_id, status="failed")

    run = repo.start_sync_run(provider="open_meteo", dataset_key="open_meteo_current",
                              export_cohort_required=True)
    args = {"run_id": run.run_id, "provider": "open_meteo",
            "dataset_key": "open_meteo_current", "descriptors": [descriptor]}
    first = declare_source_cohort(repo, **args)
    assert first == declare_source_cohort(repo, **args)  # 동일 run의 불변 plan만 재시도 가능
    table = external_cohort_table(ExportHeads(repo).metadata)
    with repo.engine.connect() as c:
        saved = c.execute(select(table).where(table.c.run_id == run.run_id)).mappings().one()
    assert saved["descriptors"] == [descriptor.model_dump(mode="json")]
    assert saved["targets"] == [{"location_id": key, "latitude": 37.5, "longitude": 127.0}]
    repo.finish_sync_run(run.run_id, status="failed")



def test_current_inventory_reader_retains_rotation_and_failed_cursor_stays_stale(
    external_repository, tmp_path, monkeypatch,
):
    from kortravelweather.export_snapshots import FrozenCurrentExports

    repo = external_repository
    catalog = ExportHeads(repo).metadata.tables["weather_locations"]
    with repo.engine.begin() as c:
        previous = list(c.scalars(select(catalog.c.location_id).where(catalog.c.enabled.is_(True))))
        c.execute(catalog.update().values(enabled=False))
    try:
        targets = [ProviderLocation("reader-" + uuid.uuid4().hex, 37.5, 127, {}) for _ in range(2)]
        for target in targets:
            repo.create_location(WeatherLocation(
                location_id=target.location_id, name="순환 수집 reader",
                latitude=37.5, longitude=127,
            ))
        _publish(repo, tmp_path, targets)
        reader = FrozenCurrentExports(
            repo, ExportArtifactStore(tmp_path), signing_key="inventory-cursor",
            providers=["open_meteo"], source_inventory=True,
            expected_sources=[("open_meteo", "open_meteo_current")],
        )
        first, etag = reader.page("current", limit=1, cursor=None)
        assert first["meta"]["source_status"] == "ok"
        cursor = first["meta"]["next_cursor"]
        assert cursor
        _publish(repo, tmp_path, targets[:1])
        rotated, _ = reader.page("current", limit=5000, cursor=None)
        assert rotated["meta"]["source_status"] == "ok" and len(rotated["items"]) == 6
        assert {item["location_id"] for item in rotated["items"]} == {
            target.location_id for target in targets
        }
        deleted = repo.get_location(targets[1].location_id)
        import kortravelweather.export_snapshots as snapshots_module

        wall = {"now": datetime.now(UTC) + timedelta(minutes=2)}

        class SnapshotClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return wall["now"].astimezone(tz)

        monkeypatch.setattr(snapshots_module, "datetime", SnapshotClock)
        with repo.engine.begin() as c:
            c.execute(catalog.update().where(
                catalog.c.location_id == deleted.location_id,
            ).values(enabled=False))
        disabled, _ = reader.page("current", limit=5000, cursor=None)
        assert len(disabled["items"]) == 3  # 명시적인 비활성화만 새 vector에서 제외한다.
        with repo.engine.begin() as c:
            c.execute(catalog.update().where(
                catalog.c.location_id == deleted.location_id,
            ).values(enabled=True))
        wall["now"] -= timedelta(minutes=1)
        restored, _ = reader.page("current", limit=5000, cursor=None)
        assert len(restored["items"]) == 6
        assert restored["meta"]["snapshot_generated_at"] < disabled["meta"]["snapshot_generated_at"]
        with repo.engine.begin() as c:
            # 승인 artifact가 native 이력 보존 기간보다 오래 남은 이전/복구 경계.
            c.exec_driver_sql("TRUNCATE TABLE weather_values CASCADE")
            c.execute(catalog.delete().where(catalog.c.location_id == deleted.location_id))
        lost, _ = reader.page("current", limit=5000, cursor=None)
        assert len(lost["items"]) == 6 and lost["meta"]["source_status"] == "stale"
        lost_cursor, _ = reader.page("current", limit=1, cursor=cursor)
        assert lost_cursor["meta"]["source_status"] == "stale"
        repo.create_location(deleted)
        failed = repo.start_sync_run(provider="open_meteo", dataset_key="open_meteo_current",
                                     export_cohort_required=True)
        repo.finish_sync_run(failed.run_id, status="failed")
        stale, changed = reader.page("current", limit=1, cursor=None)
        assert stale["meta"]["source_status"] == "stale" and changed != etag
        continued, _ = reader.page("current", limit=1, cursor=cursor)
        assert continued["meta"]["source_status"] == "stale"
    finally:
        with repo.engine.begin() as c:
            c.execute(catalog.update().values(enabled=False))
            if previous:
                c.execute(catalog.update().where(catalog.c.location_id.in_(previous)).values(
                    enabled=True,
                ))
