"""quota 회전에 선택하지 않은 위치의 승인과 파일 root를 잃지 않는다."""

import uuid
from datetime import UTC, datetime

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


def _publish(repo, root, locations):
    from kortravelweather_dagster.external_weather import run_external_weather_sync

    result = run_external_weather_sync(
        repository=repo, provider=OpenMeteoProvider(transport=FixtureTransport(*[
            FakeResponse(_external_payload("open_meteo", forecast=False, at=datetime.now(UTC)))
            for _ in locations
        ])), targets=locations, dataset_key="open_meteo_current", export_artifact_root=root,
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
        catalog_source_columns,
    )

    heads = ExportHeads(repo)
    catalog = heads.metadata.tables["weather_locations"]
    with repo.engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
        c.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
        locations = c.execute(select(
            catalog.c.location_id, catalog.c.latitude, catalog.c.longitude,
            catalog.c.nx, catalog.c.ny,
            *catalog_source_columns(catalog),
        ).where(catalog.c.enabled.is_(True))).mappings().all()
        return approved_source_selection(
            c, heads.metadata, locations,
            expected=expected or [("open_meteo", "open_meteo_current")], optional=optional,
        )


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
