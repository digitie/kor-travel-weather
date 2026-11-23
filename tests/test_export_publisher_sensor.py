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


@pytest.fixture(autouse=True)
def _fresh_failure_streak(monkeypatch):
    # 연속 실패 tick 수는 프로세스 상태다. 시험마다 새 프로세스처럼 0에서 시작한다.
    monkeypatch.setattr(module, "_FAILING_TICKS", [0])


@job
def metadata_only_job():
    pass


def build(repository, root, *, enabled=True, gc="off", budget=None):
    options = {} if budget is None else {"service_export_publisher_budget_seconds": budget}
    settings = WeatherSettings(
        service_exports_enabled=enabled, service_export_artifact_root=root,
        service_export_artifact_gc=gc, **options,
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
    # 운영 daemon의 DAGSTER_SENSOR_GRPC_TIMEOUT_SECONDS는 60초다. tick 상한 40초에 마지막
    # 문장 하나의 최악 대기(statement 5초)와 정리 한 문장(5초)을 더해도 10초 이상 남는다
    # (45초 상한일 때는 5초였다).
    from kortravelweather.export_collector import DAILY_PRUNE_WORST_SECONDS
    from kortravelweather.settings import SERVICE_EXPORT_PUBLISHER_BUDGET_MAX as upper

    assert module.TICK_DEADLINE_SECONDS + 5 + DAILY_PRUNE_WORST_SECONDS <= 60 - 10
    # GC·퇴역이 job으로 나가 모든 tick이 기본 source 예산 전부를 쓴다(처리량 유지).
    assert upper + module.DAILY_BUDGET_SECONDS + DAILY_PRUNE_WORST_SECONDS == (
        module.TICK_DEADLINE_SECONDS
    )
    # 기본값이 도착량 대비 여유를 갖도록 상한과 같다(운영 env 변경 없이 적용).
    assert WeatherSettings().service_export_publisher_budget_seconds == upper == 30
    assert WeatherSettings(
        service_export_publisher_budget_seconds=45,
    ).service_export_publisher_budget_seconds == upper
    # 활성 publisher의 기본 GC는 참조 검증 뒤 실제 정리한다.
    assert WeatherSettings().service_export_artifact_gc == "delete"


@pytest.mark.parametrize("field,raw,expected", [
    ("service_export_publisher_budget_seconds", "12.5", 12.5),
    ("service_export_publisher_budget_seconds", "0", 30),
    ("service_export_publisher_budget_seconds", "-3", 30),
    ("service_export_publisher_budget_seconds", "abc", 30),
    ("service_export_publisher_budget_seconds", "nan", 30),
    ("service_export_artifact_gc", "DRY_RUN ", "dry_run"),
    ("service_export_artifact_gc", "off", "off"),
    ("service_export_artifact_gc", "delte", "dry_run"),
    ("service_export_artifact_retention_hours", "48", 48),
    ("service_export_artifact_retention_hours", "2", 6),
    ("service_export_artifact_retention_hours", "100000", 720),
    ("service_export_artifact_retention_hours", "x", 24),
])
def test_publisher_settings_are_corrected_not_rejected(monkeypatch, field, raw, expected):
    # 모든 weather asset이 같은 설정 객체를 만든다. 잘못된 env 하나가 수집 전체를
    # ValidationError로 멈추게 하지 않고, 경고와 함께 안전한 값으로 보정한다.
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_" + field.upper(), raw)
    assert getattr(WeatherSettings(), field) == expected


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


def test_tick_does_no_bulk_retirement_or_gc(publisher_repository, tmp_path, monkeypatch):
    # 퇴역·GC는 export_maintenance job의 일이다. sensor tick은 부르지 않는다.
    assert not hasattr(module, "retire_settled_attempts")
    assert not hasattr(module, "collect_export_artifacts")
    sensor, _ = build(publisher_repository, tmp_path, gc="delete")
    with DagsterInstance.ephemeral() as instance:
        result = evaluate(sensor, instance)
    assert "retired=" not in result.skip_message and "gc=" not in result.skip_message


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


def _progress(processed=0, results=None, cursor=None, more=False):
    from kortravelweather.export_collector import ExportPublisherProgress

    return ExportPublisherProgress(processed, results or {}, cursor, more)


def test_cooperative_deadline_starts_no_second_batch_and_keeps_resume_cursor(
    publisher_repository, tmp_path, monkeypatch,
):
    clock = _fake_clock(monkeypatch)
    resume = "00000000-0000-0000-0000-000000000002"
    batches = []

    def batch(*args, **kwargs):
        batches.append(kwargs)
        clock[0] = 100 + 30  # 첫 sweep 묶음이 source 예산을 다 썼다.
        return _progress(2, {"NOT_READY": 2}, resume, True)

    monkeypatch.setattr(module, "publish_fresh_exports", lambda *a, **k: _progress())
    monkeypatch.setattr(module, "publish_pending_exports", batch)
    sensor, _ = build(publisher_repository, tmp_path)
    with DagsterInstance.ephemeral() as instance:
        result = evaluate(sensor, instance)
    assert len(batches) == 1 and batches[0]["budget_seconds"] == 30
    assert batches[0]["limit"] == module.CANDIDATE_PAGE
    assert json.loads(result.cursor)["after"] == resume


def test_daily_budget_is_reserved_until_the_hard_tick_deadline(
    publisher_repository, tmp_path, monkeypatch,
):
    clock, daily_calls = _fake_clock(monkeypatch), []

    def source(*args, **kwargs):
        assert kwargs["budget_seconds"] == 30
        clock[0] = 100 + 33  # 마지막 DB/IO 대기가 협력 예산을 넘었지만 tick 상한 전이다.
        return _progress(2, {"NOT_READY": 2}, None, True)

    def daily(*args, **kwargs):
        daily_calls.append(kwargs)
        return {"processed": 4, "has_more": True}

    monkeypatch.setattr(module, "publish_fresh_exports", lambda *a, **k: _progress())
    monkeypatch.setattr(module, "publish_pending_exports", source)
    monkeypatch.setattr(module, "refresh_publisher_daily", daily)
    sensor, _ = build(publisher_repository, tmp_path)
    with DagsterInstance.ephemeral() as instance:
        result = evaluate(sensor, instance)
    hard = 100 + module.TICK_DEADLINE_SECONDS
    assert daily_calls == [{"deadline": 133 + module.DAILY_BUDGET_SECONDS, "hard_deadline": hard}]
    assert "일별 4세대" in result.skip_message and "stages=" in result.skip_message


def test_tick_past_the_hard_deadline_skips_daily_without_failing(
    publisher_repository, tmp_path, monkeypatch,
):
    clock = _fake_clock(monkeypatch)

    def source(*args, **kwargs):
        clock[0] += module.TICK_DEADLINE_SECONDS + 1
        return _progress(1, {"NOT_READY": 1})

    def forbidden(*args, **kwargs):
        pytest.fail("tick 상한을 넘긴 뒤에는 일별 집계를 시작하지 않습니다.")

    monkeypatch.setattr(module, "publish_fresh_exports", source)
    monkeypatch.setattr(module, "publish_pending_exports", lambda *a, **k: _progress())
    monkeypatch.setattr(module, "refresh_publisher_daily", forbidden)
    sensor, _ = build(publisher_repository, tmp_path)
    with DagsterInstance.ephemeral() as instance:
        for _ in range(module.FAILING_TICKS_BEFORE_ERROR + 1):
            result = evaluate(sensor, instance)
            # 시간 상한으로 건너뛴 일별 집계는 실패가 아니다. 연속돼도 tick은 skip이다.
            assert "daily:tick_deadline:1" in result.skip_message
    assert module._FAILING_TICKS == [0]


def _failing_sweep(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic private exception must not reach the tick")

    monkeypatch.setattr(module, "publish_fresh_exports", lambda *a, **k: _progress())
    monkeypatch.setattr(module, "publish_pending_exports", fail)


def test_one_failing_stage_is_a_skip_but_repeated_failure_fails_the_tick(
    publisher_repository, tmp_path, monkeypatch,
):
    _failing_sweep(monkeypatch)
    sensor, _ = build(publisher_repository, tmp_path)
    with DagsterInstance.ephemeral() as instance:
        for _ in range(module.FAILING_TICKS_BEFORE_ERROR - 1):
            result = evaluate(sensor, instance)
            assert "sweep:RuntimeError:1" in result.skip_message
        with pytest.raises(Exception) as raised:
            evaluate(sensor, instance)
    text_of = str(raised.value)
    assert "3_consecutive_failing_ticks:sweep:RuntimeError" in text_of
    assert "synthetic" not in text_of


def test_a_healthy_tick_resets_the_failure_streak(publisher_repository, tmp_path, monkeypatch):
    original = module.publish_pending_exports
    _failing_sweep(monkeypatch)
    sensor, _ = build(publisher_repository, tmp_path)
    with DagsterInstance.ephemeral() as instance:
        for _ in range(module.FAILING_TICKS_BEFORE_ERROR - 1):
            evaluate(sensor, instance)
        monkeypatch.setattr(module, "publish_pending_exports", original)
        evaluate(sensor, instance)
        assert module._FAILING_TICKS == [0]
        _failing_sweep(monkeypatch)
        assert "sweep:RuntimeError:1" in evaluate(sensor, instance).skip_message


def test_daily_failure_counts_as_a_failing_stage(publisher_repository, tmp_path, monkeypatch):
    monkeypatch.setattr(
        module, "refresh_publisher_daily",
        lambda *a, **k: {"processed": 0, "has_more": True, "error": "statement_timeout"},
    )
    sensor, _ = build(publisher_repository, tmp_path)
    with DagsterInstance.ephemeral() as instance:
        first = evaluate(sensor, instance)
    assert "daily:statement_timeout:1" in first.skip_message
    assert module._FAILING_TICKS == [1]


def test_unexpected_tick_error_fails_the_tick_without_the_original_message(
    publisher_repository, tmp_path,
):
    def factory(context, runtime):
        raise RuntimeError("postgresql://user:secret@host/db synthetic")

    sensor = module.build_export_publisher_sensor(
        settings_factory=lambda: WeatherSettings(
            service_exports_enabled=True, service_export_artifact_root=tmp_path,
        ),
        repository_factory=factory,
    )
    with DagsterInstance.ephemeral() as instance, pytest.raises(Exception) as raised:
        evaluate(sensor, instance)
    message = str(raised.value)
    assert "export publisher 평가 실패(RuntimeError)" in message
    assert "secret" not in message and "synthetic" not in message
    # 다음 tick은 슬롯을 다시 잡을 수 있다.
    assert module._TICK_SLOT.acquire(blocking=False)
    module._TICK_SLOT.release()


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
