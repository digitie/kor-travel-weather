"""실제 public sensor 평가와 PostgreSQL lease를 격리 DB에서 검증한다."""

import json
from types import SimpleNamespace

import pytest
import test_export_publisher_recovery as fixtures
from dagster import (
    DagsterInstance,
    DagsterRunStatus,
    DefaultSensorStatus,
    build_sensor_context,
    job,
)
from kortravelweather_dagster import export_publisher as module
from sqlalchemy import select

from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings

publisher_repository = fixtures.publisher_repository
clear_exports = fixtures.clear_exports
candidate = fixtures.candidate
state = fixtures.state


@job
def metadata_only_job():
    pass


def build(repository, root, *, enabled=True):
    settings = WeatherSettings(
        service_exports_enabled=enabled, service_export_artifact_root=root,
    )
    owned = []

    def factory(context, runtime):
        result = WeatherRepository(str(repository.engine.url.render_as_string(hide_password=False)))
        owned.append(result)
        return result

    return module.build_export_publisher_sensor(
        settings_factory=lambda: settings, repository_factory=factory,
    ), owned


def evaluate(sensor, instance, *, cursor=None):
    with build_sensor_context(
        instance=instance, cursor=cursor,
        resources={"weather_repository": SimpleNamespace(database_url="")},
    ) as context:
        return sensor.evaluate_tick(context)


def test_disabled_and_missing_root_have_zero_db_and_instance_queries(publisher_repository):
    def forbidden(*_):
        pytest.fail("비활성 tick은 repository를 만들지 않습니다.")

    for enabled in (False, True):
        sensor = module.build_export_publisher_sensor(
            settings_factory=lambda flag=enabled: WeatherSettings(service_exports_enabled=flag),
            repository_factory=forbidden,
        )
        with build_sensor_context(resources={"weather_repository": object()}) as context:
            result = sensor.evaluate_tick(context)
        assert not result.run_requests and result.skip_message
        assert sensor.default_status is DefaultSensorStatus.STOPPED
        assert sensor.minimum_interval_seconds == 180


@pytest.mark.parametrize("status,native,outcome", [
    (DagsterRunStatus.SUCCESS, "success", "sealed"),
    (DagsterRunStatus.FAILURE, None, "failed"),
    (DagsterRunStatus.CANCELED, None, "failed"),
    (DagsterRunStatus.STARTED, None, "preparing"),
    (None, None, "preparing"),
])
def test_real_sensor_exact_status_and_no_worker(publisher_repository, tmp_path, monkeypatch,
                                              status, native, outcome):
    with DagsterInstance.ephemeral() as instance:
        run_id = "missing-worker"
        if status is not None:
            run_id = instance.create_run_for_job(metadata_only_job, status=status).run_id
        heads, identity, *_ = candidate(
            publisher_repository, tmp_path, monkeypatch, terminal=native,
            dagster=True, orchestrator=run_id,
        )
        sensor, owned = build(publisher_repository, tmp_path)
        count = len(instance.get_runs())
        result = evaluate(sensor, instance)
        assert not result.run_requests and len(instance.get_runs()) == count
        assert state(publisher_repository, heads, identity)[0]["status"] == outcome
        assert len(owned) == 1 and owned[0].engine.pool.checkedout() == 0
        assert json.loads(result.cursor) == {"v": 1, "after": None}
        again = evaluate(sensor, instance, cursor=result.cursor)
        assert not again.run_requests
        if outcome == "sealed":
            with publisher_repository.engine.connect() as connection:
                assert len(connection.execute(select(heads.generations)).all()) == 1


def test_other_publisher_lease_preserves_cursor_and_releases_resources(publisher_repository,
                                                                    tmp_path):
    sensor, owned = build(publisher_repository, tmp_path)
    with module._publisher_lease(publisher_repository) as acquired:
        assert acquired
        with DagsterInstance.ephemeral() as instance:
            result = evaluate(sensor, instance)
    assert result.cursor is None and not result.run_requests
    assert owned[0].engine.pool.checkedout() == 0
    with module._publisher_lease(publisher_repository) as acquired:
        assert acquired


def test_business_connection_returned_during_orchestrator_lookup(publisher_repository, tmp_path,
                                                              monkeypatch):
    with DagsterInstance.ephemeral() as instance:
        run = instance.create_run_for_job(metadata_only_job, status=DagsterRunStatus.SUCCESS)
        candidate(publisher_repository, tmp_path, monkeypatch, dagster=True,
                  orchestrator=run.run_id)
        sensor, owned = build(publisher_repository, tmp_path)
        original = instance.get_run_by_id

        def lookup(run_id):
            # 전용 session lease만 1개, business session/transaction은 0개다.
            assert owned[0].engine.pool.checkedout() == 1
            return original(run_id)

        monkeypatch.setattr(instance, "get_run_by_id", lookup)
        assert evaluate(sensor, instance).cursor


def test_sql_error_preserves_cursor_and_next_tick_can_retry(publisher_repository, tmp_path,
                                                         monkeypatch):
    sensor, owned = build(publisher_repository, tmp_path)
    original = module.publish_pending_exports
    after = "00000000-0000-0000-0000-000000000001"
    cursor = json.dumps({"v": 1, "after": after})

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic private exception must not reach log")

    monkeypatch.setattr(module, "publish_pending_exports", fail)
    with DagsterInstance.ephemeral() as instance:
        result = evaluate(sensor, instance, cursor=cursor)
        assert result.cursor == cursor
        assert owned[0].engine.pool.checkedout() == 0
        monkeypatch.setattr(module, "publish_pending_exports", original)
        assert json.loads(evaluate(sensor, instance, cursor=cursor).cursor)["after"] is None


def test_malformed_cursor_resets(publisher_repository, tmp_path):
    sensor, owned = build(publisher_repository, tmp_path)
    with DagsterInstance.ephemeral() as instance:
        result = evaluate(sensor, instance, cursor="not-json")
    assert json.loads(result.cursor) == {"v": 1, "after": None}
    assert owned[0].engine.pool.checkedout() == 0


def test_process_slot_skips_before_repository_creation(publisher_repository, tmp_path):
    sensor, owned = build(publisher_repository, tmp_path)
    assert module._TICK_SLOT.acquire(blocking=False)
    try:
        with DagsterInstance.ephemeral() as instance:
            result = evaluate(sensor, instance)
        assert not owned and result.cursor is None and not result.run_requests
    finally:
        module._TICK_SLOT.release()


@pytest.mark.parametrize("claim", [False, True])
@pytest.mark.parametrize("status,native_terminal,expected", [
    (DagsterRunStatus.FAILURE, None, "failed"),
    (DagsterRunStatus.CANCELED, None, "failed"),
    (DagsterRunStatus.SUCCESS, None, "preparing"),
    (DagsterRunStatus.SUCCESS, "success", "preparing"),
    (DagsterRunStatus.STARTED, None, "preparing"),
    (None, None, "preparing"),
])
def test_planless_crash_is_seen_by_terminal_sweep_without_inventing_success(
    publisher_repository, tmp_path, monkeypatch, claim, status, native_terminal, expected,
):
    from test_export_heads import _descriptor

    from kortravelweather.export_heads import ExportHeads

    repo = publisher_repository
    monkeypatch.setattr(repo, "export_status_enabled", True)
    descriptor = _descriptor(repo)
    heads = ExportHeads(repo)
    with DagsterInstance.ephemeral() as instance:
        run_id = "missing-planless-worker"
        if status is not None:
            run_id = instance.create_run_for_job(metadata_only_job, status=status).run_id
        monkeypatch.setattr(repo, "orchestrator_run_id", run_id)
        run = repo.start_sync_run(provider=descriptor.provider, dataset_key=descriptor.dataset_key)
        identity = repo.begin_export_attempt(
            run_id=run.run_id, scope_key="planless-crash",
            planned_sources=[descriptor.source_scope], execution_mode="dagster",
        )
        if claim:
            heads.lifecycle.claim(identity)
        if native_terminal:
            repo.finish_sync_run(run.run_id, status=native_terminal)
        # begin/claim은 commit했지만 declare를 호출하기 전에 worker가 끝난 상태다.
        with repo.engine.connect() as connection:
            assert connection.scalar(select(heads.plans.c.attempt_id)) is None
        sensor, _ = build(repo, tmp_path)
        evaluated = evaluate(sensor, instance)
        assert "1개" in evaluated.skip_message
        attempt, native = state(repo, heads, identity)
        assert attempt["status"] == expected
        assert native["status"] == (
            "failed" if expected == "failed" else native_terminal or "running"
        )
        assert native["values_loaded"] == 0
        with repo.engine.connect() as connection:
            assert connection.scalar(select(heads.generations.c.generation_id)) is None
        if expected == "failed":
            assert native["export_status_pending"] is True
            assert repo.reconcile_export_status({descriptor.provider}) == 1
            assert state(repo, heads, identity)[1]["export_status_pending"] is False
            assert "0개" in evaluate(sensor, instance, cursor=evaluated.cursor).skip_message
