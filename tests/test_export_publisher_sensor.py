"""실제 public sensor 평가와 PostgreSQL lease를 격리 DB에서 검증한다."""

import json
import threading
import time
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
from sqlalchemy import select, text

from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings

publisher_repository = fixtures.publisher_repository
clear_exports = fixtures.clear_exports
candidate = fixtures.candidate
state = fixtures.state


@job
def metadata_only_job():
    pass


def build(repository, root, *, enabled=True, gc="off", budget=24):
    settings = WeatherSettings(
        service_exports_enabled=enabled, service_export_artifact_root=root,
        service_export_artifact_gc=gc, service_export_publisher_budget_seconds=budget,
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
        assert sensor.minimum_interval_seconds == module.MINIMUM_INTERVAL_SECONDS == 60


def test_tick_budgets_fit_inside_the_sensor_grpc_deadline():
    # 운영 daemon의 DAGSTER_SENSOR_GRPC_TIMEOUT_SECONDS는 60초다. source 최대값과 daily·GC
    # 예산을 더하고도 퇴역 두 문장(각 5초 상한)과 마지막 후보의 초과 대기를 위해 20초를
    # 남겨야 tick이 daemon 쪽에서 시간 초과로 끝나지 않는다.
    maximum = WeatherSettings.model_fields["service_export_publisher_budget_seconds"]
    upper = next(item.le for item in maximum.metadata if hasattr(item, "le"))
    assert upper + module.DAILY_BUDGET_SECONDS + module.GC_BUDGET_SECONDS <= 40


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
        heads, identity, *_ = candidate(publisher_repository, tmp_path, monkeypatch,
                                        dagster=True, orchestrator=run.run_id)
        sensor, owned = build(publisher_repository, tmp_path)
        original_one, original_many = instance.get_run_by_id, instance.get_runs
        calls = []

        def checked_out():
            return owned[0].engine.pool.checkedout() if owned else None

        def lookup(run_id):
            calls.append(("one", checked_out()))
            return original_one(run_id)

        def lookup_many(*args, **kwargs):
            calls.append(("batch", checked_out()))
            return original_many(*args, **kwargs)

        monkeypatch.setattr(instance, "get_run_by_id", lookup)
        monkeypatch.setattr(instance, "get_runs", lookup_many)
        assert evaluate(sensor, instance).cursor
    # publisher 안의 조회는 묶음 1회뿐이고(후보별·reconcile 조회는 tick 기억에서 답한다),
    # 그동안 전용 session lease만 1개, business session/transaction은 0개다.
    mine = [call for call in calls if call[1] is not None]
    assert mine == [("batch", 1)]
    assert state(publisher_repository, heads, identity)[0]["published_at"] is not None


def test_sql_error_preserves_cursor_and_next_tick_can_retry(publisher_repository, tmp_path,
                                                         monkeypatch, caplog):
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
        assert "sweep:RuntimeError" in result.skip_message
        assert "synthetic" not in result.skip_message and "synthetic" not in caplog.text
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


def test_failed_run_backlog_is_retired_in_one_tick_without_visiting_candidates(
    publisher_repository, tmp_path,
):
    from kortravelweather.export_heads import ExportHeads

    repo = publisher_repository
    run = repo.start_sync_run(provider="test-capacity", dataset_key="synthetic")
    for _ in range(300):
        repo.begin_export_attempt(run_id=run.run_id, scope_key="synthetic",
                                 planned_sources=["0" * 64], execution_mode="native")
    repo.finish_sync_run(run.run_id, status="failed")
    sensor, _ = build(repo, tmp_path)
    with DagsterInstance.ephemeral() as instance:
        first = evaluate(sensor, instance)
        # 실패 run의 300건은 후보를 하나씩 방문하지 않고 한 문장으로 퇴역한다.
        assert "0개 후보" in first.skip_message and "retired=300" in first.skip_message
        assert "retired=0" in evaluate(sensor, instance, cursor=first.cursor).skip_message
    heads = ExportHeads(repo)
    with repo.engine.connect() as connection:
        rows = connection.execute(
            select(heads.lifecycle.attempts.c.status, heads.lifecycle.attempts.c.error_code)
        ).all()
    assert {tuple(row) for row in rows} == {("failed", "CONFIRMED_TERMINAL")} and len(rows) == 300


def test_sweep_is_bounded_per_tick_and_resumes_beyond_the_bound(
    publisher_repository, tmp_path, monkeypatch,
):
    repo = publisher_repository
    run = repo.start_sync_run(provider="test-capacity", dataset_key="running")
    try:
        for _ in range(300):
            repo.begin_export_attempt(run_id=run.run_id, scope_key="synthetic",
                                     planned_sources=["0" * 64], execution_mode="native")
        sensor, _ = build(repo, tmp_path)
        # 실제 DB의 선택/진행과 총량 상한을 확인한다. clock을 고정하므로 이것을
        # 운영 처리율/CPU 실측으로 사용하지 않는다.
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: 0))
        monkeypatch.setattr(module, "MAX_TICK_ATTEMPTS", 256)
        with DagsterInstance.ephemeral() as instance:
            first = evaluate(sensor, instance)
            assert "256개" in first.skip_message and "not_ready=256" in first.skip_message
            assert json.loads(first.cursor)["after"]
            second = evaluate(sensor, instance, cursor=first.cursor)
            assert "44개" in second.skip_message
            assert json.loads(second.cursor)["after"] is None
            # 수집 중인 run의 후보는 퇴역하지 않고 다음 sweep에서 처음부터 다시 본다.
            assert "256개" in evaluate(sensor, instance, cursor=second.cursor).skip_message
    finally:
        repo.finish_sync_run(run.run_id, status="failed")


def _fake_clock(monkeypatch, start=100.0):
    clock = [start]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    return clock


def test_cooperative_deadline_starts_no_second_batch_and_keeps_resume_cursor(
    publisher_repository, tmp_path, monkeypatch,
):
    from kortravelweather.export_collector import ExportPublisherProgress

    clock = _fake_clock(monkeypatch)
    resume = "00000000-0000-0000-0000-000000000002"
    batches = []

    def fresh(*args, **kwargs):
        return ExportPublisherProgress(0, {}, None, False)

    def batch(*args, **kwargs):
        batches.append(kwargs)
        clock[0] = 100 + 24  # 첫 sweep 묶음이 source 예산을 다 썼다.
        return ExportPublisherProgress(2, {"NOT_READY": 2}, resume, True)

    monkeypatch.setattr(module, "publish_fresh_exports", fresh)
    monkeypatch.setattr(module, "publish_pending_exports", batch)
    sensor, _ = build(publisher_repository, tmp_path)
    with DagsterInstance.ephemeral() as instance:
        result = evaluate(sensor, instance)
    assert len(batches) == 1 and batches[0]["budget_seconds"] == 24
    assert batches[0]["limit"] == module.CANDIDATE_PAGE
    assert json.loads(result.cursor)["after"] == resume


def test_daily_budget_is_reserved_even_when_last_source_operation_overruns(
    publisher_repository, tmp_path, monkeypatch,
):
    from kortravelweather.export_collector import ExportPublisherProgress

    clock, daily_deadlines = _fake_clock(monkeypatch), []

    def fresh(*args, **kwargs):
        return ExportPublisherProgress(0, {}, None, False)

    def source(*args, **kwargs):
        assert kwargs["budget_seconds"] == 24
        clock[0] = 200  # 마지막 DB/IO 대기가 협력 예산을 넘은 경우다.
        return ExportPublisherProgress(2, {"NOT_READY": 2}, None, True)

    def daily(*args, **kwargs):
        daily_deadlines.append(kwargs["deadline"])
        return {"processed": 4, "has_more": True}

    monkeypatch.setattr(module, "publish_fresh_exports", fresh)
    monkeypatch.setattr(module, "publish_pending_exports", source)
    monkeypatch.setattr(module, "refresh_publisher_daily", daily)
    sensor, _ = build(publisher_repository, tmp_path)
    with DagsterInstance.ephemeral() as instance:
        result = evaluate(sensor, instance)
    assert daily_deadlines == [200 + module.DAILY_BUDGET_SECONDS]
    assert "일별 4세대" in result.skip_message


def _hold_row(repository, run_id, locked, release):
    """같은 run의 ingest transaction처럼 run 행을 잡는다. ``release``까지(최대 60초) 둔다."""
    with repository.engine.connect() as connection, connection.begin():
        connection.execute(
            text("SELECT run_id FROM weather_sync_runs WHERE run_id = :run_id FOR UPDATE"),
            {"run_id": run_id},
        )
        locked.set()
        release.wait(60)


def test_lock_timeout_on_one_candidate_does_not_defer_the_tick(
    publisher_repository, tmp_path, monkeypatch,
):
    """운영 2026-10-26의 연속 deferred tick 재현: 한 후보의 run 행을 다른 transaction이
    lock_timeout보다 오래 잡아도 그 후보만 미루고 나머지를 승격하며 cursor·일별 집계를
    정상으로 마친다."""
    repo = publisher_repository
    heads, blocked, *_ = candidate(repo, tmp_path, monkeypatch)
    _, free, *_ = candidate(repo, tmp_path, monkeypatch)
    blocked_run = state(repo, heads, blocked)[0]["run_id"]
    locked, release = threading.Event(), threading.Event()
    holder = threading.Thread(target=_hold_row, args=(repo, blocked_run, locked, release))
    holder.start()
    assert locked.wait(10)
    try:
        # 기존 설정 필드만 쓴다. 수정 전 sensor에도 그대로 돌려 tick 전체 연기를 재현한다.
        owned = []

        def factory(context, runtime):
            owned.append(WeatherRepository(repo.engine.url.render_as_string(hide_password=False)))
            return owned[-1]

        sensor = module.build_export_publisher_sensor(
            settings_factory=lambda: WeatherSettings(
                service_exports_enabled=True, service_export_artifact_root=tmp_path,
            ),
            repository_factory=factory,
        )
        with DagsterInstance.ephemeral() as instance:
            started = time.monotonic()
            result = evaluate(sensor, instance)
            # 잠긴 후보는 lock_timeout(3×deadlock_timeout) 한 번만 기다린다.
            assert time.monotonic() - started < 30
    finally:
        release.set()
        holder.join()
    assert "다음 tick" not in result.skip_message
    assert "published=1" in result.skip_message and "deferred=1" in result.skip_message
    assert "lock_timeout:1" in result.skip_message and "일별" in result.skip_message
    assert json.loads(result.cursor)["v"] == 1
    assert state(repo, heads, free)[0]["published_at"] is not None
    assert state(repo, heads, blocked)[0]["status"] == "preparing"
    assert owned[0].engine.pool.checkedout() == 0
    with DagsterInstance.ephemeral() as instance:
        again = evaluate(sensor, instance, cursor=result.cursor)
    assert "published=1" in again.skip_message
    assert state(repo, heads, blocked)[0]["published_at"] is not None


def test_gc_runs_on_its_interval_and_reports_in_the_skip_reason(
    publisher_repository, tmp_path, monkeypatch,
):
    monkeypatch.setattr(module, "_GC_NEXT", [0.0])
    sensor, _ = build(publisher_repository, tmp_path, gc="dry_run")
    with DagsterInstance.ephemeral() as instance:
        first = evaluate(sensor, instance)
        second = evaluate(sensor, instance, cursor=first.cursor)
    assert "gc=dry_run:0files/0B" in first.skip_message
    assert "gc=" not in second.skip_message
