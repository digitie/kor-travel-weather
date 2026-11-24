"""export 정리 job(일괄 퇴역·artifact GC)을 격리 PostgreSQL에서 검증한다."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import test_export_publisher_recovery as fixtures
from dagster import DefaultScheduleStatus
from kortravelweather_dagster import export_maintenance as module
from sqlalchemy import func, select

from kortravelweather.export_artifacts import prepare_export_chunk
from kortravelweather.export_database_artifacts import export_artifact_store
from kortravelweather.export_heads import ExportHeads
from kortravelweather.settings import WeatherSettings

publisher_repository = fixtures.publisher_repository
clear_exports = fixtures.clear_exports


def _settings(root, *, gc="delete", enabled=True):
    return WeatherSettings(
        service_exports_enabled=enabled, service_export_artifact_root=root,
        service_export_artifact_gc=gc,
    )


def _old_orphans(repo, root, count):
    store = export_artifact_store(repo, root)
    digests = []
    with repo.engine.begin() as connection:
        for index in range(count):
            chunk = prepare_export_chunk(
                [{"obsolete": index}], source_collected_at=datetime.now(UTC),
            )
            # DB 행은 UPDATE 불가이므로 오래된 orphan을 처음부터 그 시각으로 삽입한다.
            connection.execute(store.table.insert().values(
                digest=chunk.digest, payload=chunk.payload, byte_count=len(chunk.payload),
                point_count=chunk.point_count,
                created_at=datetime.now(UTC) - timedelta(hours=48, minutes=index),
            ))
            digests.append(chunk.digest)
    return store, digests


def test_failed_run_backlog_is_retired_in_one_run(publisher_repository, tmp_path):
    repo = publisher_repository
    run = repo.start_sync_run(provider="test-capacity", dataset_key="synthetic")
    for _ in range(300):
        repo.begin_export_attempt(run_id=run.run_id, scope_key="synthetic",
                                 planned_sources=["0" * 64], execution_mode="native")
    repo.finish_sync_run(run.run_id, status="failed")
    report = module.run_export_maintenance(repo, _settings(tmp_path, gc="off"))
    assert report["retired_confirmed_terminal"] == 300 and report["retire_rounds"] == 1
    assert "gc_calls" not in report
    heads = ExportHeads(repo)
    with repo.engine.connect() as connection:
        rows = connection.execute(
            select(heads.lifecycle.attempts.c.status, heads.lifecycle.attempts.c.error_code)
        ).all()
    assert {tuple(row) for row in rows} == {("failed", "CONFIRMED_TERMINAL")} and len(rows) == 300


def test_retire_repeats_while_a_statement_fills_its_batch(tmp_path, monkeypatch):
    calls = []

    def retire(repository, *, statement_timeout_seconds):
        calls.append(statement_timeout_seconds)
        full = len(calls) < 3
        return {"SUPERSEDED": 0, "CONFIRMED_TERMINAL": module.RETIRE_BATCH if full else 7}

    monkeypatch.setattr(module, "retire_settled_attempts", retire)
    report = module.run_export_maintenance(object(), _settings(tmp_path, gc="off"))
    assert calls == [module.RETIRE_STATEMENT_SECONDS] * 3
    assert report["retired_confirmed_terminal"] == 2 * module.RETIRE_BATCH + 7


def test_retire_stops_at_its_budget(tmp_path, monkeypatch):
    clock = [0.0]

    def retire(repository, *, statement_timeout_seconds):
        clock[0] += module.RETIRE_BUDGET_SECONDS / 2
        return {"SUPERSEDED": module.RETIRE_BATCH, "CONFIRMED_TERMINAL": 0}

    monkeypatch.setattr(module, "retire_settled_attempts", retire)
    report = module.run_export_maintenance(
        object(), _settings(tmp_path, gc="off"), clock=lambda: clock[0],
    )
    assert report["retire_rounds"] == 2


def test_retire_statement_timeout_override_is_validated(publisher_repository):
    from kortravelweather.export_collector import retire_settled_attempts

    with pytest.raises(ValueError):
        retire_settled_attempts(publisher_repository, statement_timeout_seconds=0)
    assert retire_settled_attempts(publisher_repository, statement_timeout_seconds=30) == {
        "SUPERSEDED": 0, "CONFIRMED_TERMINAL": 0,
    }


def test_gc_walks_every_page_in_one_run_and_deletes_old_orphans(
    publisher_repository, tmp_path, monkeypatch,
):
    # sensor 경로는 tick마다 새 repository라 목록 cursor가 버려졌다. job은 한 run 안에서
    # 작은 page를 끝까지 읽는다.
    monkeypatch.setattr(module, "GC_SCAN_PAGE", 2)
    repo = publisher_repository
    store, digests = _old_orphans(repo, tmp_path, 5)
    pauses = []
    report = module.run_export_maintenance(repo, _settings(tmp_path), sleep=pauses.append)
    with repo.engine.connect() as connection:
        left = connection.scalar(select(func.count()).select_from(store.table).where(
            store.table.c.digest.in_(digests),
        ))
    assert left == 0
    assert report["gc_deleted"] == 5 and report["gc_complete"] is True
    assert report["gc_calls"] >= 3 and report["gc_pending"] == 0
    # 배타 guard를 잡는 삭제 호출 사이마다 reader·writer에게 양보한다(마지막 완료 호출 뒤는 없음).
    # 쉬는 시간은 그 호출 시간 이상이라 실제 시계로는 최소값만 확인한다.
    assert len(pauses) == report["gc_calls"] - 1
    assert all(pause >= module.GC_YIELD_SECONDS for pause in pauses)


def test_gc_dry_run_counts_without_deleting(publisher_repository, tmp_path):
    repo = publisher_repository
    store, digests = _old_orphans(repo, tmp_path, 2)
    report = module.run_export_maintenance(repo, _settings(tmp_path, gc="dry_run"))
    assert report["gc_eligible"] == 2 and report["gc_deleted"] == 0
    with repo.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(store.table)) == 2


def test_gc_failure_fails_the_run_instead_of_being_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "retire_settled_attempts", lambda *a, **k: {})

    def fail(*args, **kwargs):
        raise TimeoutError("reference unavailable")

    monkeypatch.setattr(module, "collect_export_artifacts", fail)
    with pytest.raises(module.ExportMaintenanceError) as raised:
        module.run_export_maintenance(object(), _settings(tmp_path))
    assert raised.value.report["failures"] == "gc:TimeoutError"
    # 메시지는 분류만 담고 원 예외를 잇지 않는다.
    assert "unavailable" not in str(raised.value) and raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_retire_failure_still_runs_gc_then_fails(tmp_path, monkeypatch):
    # 퇴역이 계속 문장 상한에 걸려도 같은 run의 GC는 돈다(artifact가 쌓이지 않는다).
    rounds = []

    def retire(repository, *, statement_timeout_seconds):
        rounds.append(statement_timeout_seconds)
        if len(rounds) == 2:
            raise RuntimeError("postgresql://user:secret@host/db statement")
        return {"SUPERSEDED": module.RETIRE_BATCH, "CONFIRMED_TERMINAL": 3}

    gc_calls = []

    def collect(*args, **kwargs):
        gc_calls.append(kwargs)
        return {"mode": "delete", "visited": 2, "live": 1, "eligible": 1, "eligible_bytes": 7,
                "deleted": 1, "deleted_bytes": 7, "complete": True, "pending": 0}

    monkeypatch.setattr(module, "retire_settled_attempts", retire)
    monkeypatch.setattr(module, "collect_export_artifacts", collect)
    with pytest.raises(module.ExportMaintenanceError) as raised:
        module.run_export_maintenance(object(), _settings(tmp_path), sleep=lambda _: None)
    report = raised.value.report
    assert len(gc_calls) == 1 and report["gc_deleted"] == 1 and report["gc_complete"] is True
    # 실패 전 문장의 퇴역 수는 남는다.
    assert report["retire_rounds"] == 1 and report["retired_superseded"] == module.RETIRE_BATCH
    assert report["failures"] == "retire:RuntimeError"
    assert "secret" not in str(raised.value)


def test_both_stage_failures_are_reported(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise TimeoutError("synthetic")

    monkeypatch.setattr(module, "retire_settled_attempts", fail)
    monkeypatch.setattr(module, "collect_export_artifacts", fail)
    with pytest.raises(module.ExportMaintenanceError) as raised:
        module.run_export_maintenance(object(), _settings(tmp_path))
    assert raised.value.report["failures"] == "retire:TimeoutError,gc:TimeoutError"


def test_failed_stage_fails_the_run_with_progress_metadata(tmp_path, monkeypatch):
    from dagster import ResourceDefinition

    monkeypatch.setenv("KOR_TRAVEL_WEATHER_SERVICE_EXPORTS_ENABLED", "true")
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_SERVICE_EXPORT_ARTIFACT_ROOT", str(tmp_path))
    disposed = []
    repository = SimpleNamespace(engine=SimpleNamespace(dispose=lambda: disposed.append(True)))

    def run(repo, settings):
        assert repo is repository
        raise module.ExportMaintenanceError(
            {"retire_rounds": 0, "gc_deleted": 3, "failures": "retire:statement_timeout"},
        )

    monkeypatch.setattr(module, "run_export_maintenance", run)
    resource = ResourceDefinition.hardcoded_resource(
        SimpleNamespace(create_repository=lambda: repository),
    )
    result = module.weather_export_maintenance_job.execute_in_process(
        resources={"weather_repository": resource}, raise_on_error=False,
    )
    assert not result.success and disposed == [True]
    failure = result.failure_data_for_node("weather_export_maintenance")
    assert "retire:statement_timeout" in failure.user_failure_data.description
    assert failure.user_failure_data.metadata["gc_deleted"].value == 3


def _scripted_gc(monkeypatch, results, *, seconds_per_call):
    """정해진 결과를 차례로 돌려주는 GC 호출. 호출마다 가짜 시계를 ``seconds_per_call`` 민다."""
    clock = [0.0]
    remaining = list(results)

    def collect(*args, **kwargs):
        clock[0] += seconds_per_call
        return remaining.pop(0)

    monkeypatch.setattr(module, "retire_settled_attempts", lambda *a, **k: {})
    monkeypatch.setattr(module, "collect_export_artifacts", collect)
    return clock


def _gc_result(*, eligible, eligible_bytes=0, deleted_bytes=0, complete=False):
    return {"mode": "delete", "visited": 4, "live": 4 - eligible, "eligible": eligible,
            "eligible_bytes": eligible_bytes, "deleted": eligible, "deleted_bytes": deleted_bytes,
            "complete": complete, "pending": 0 if complete else 4}


@pytest.mark.parametrize("gc,eligible,expected", [
    # 지울 대상이 있던 delete 호출은 배타 guard를 잡았다. 그 호출 시간(2초) 이상 쉰다.
    ("delete", 1, 2.0),
    # 보호 행뿐인 호출과 dry_run은 공유 guard만 잡았다. 최소 양보만 한다.
    ("delete", 0, module.GC_YIELD_SECONDS),
    ("dry_run", 1, module.GC_YIELD_SECONDS),
])
def test_gc_rests_at_least_as_long_as_it_held_the_exclusive_guard(
    tmp_path, monkeypatch, gc, eligible, expected,
):
    clock = _scripted_gc(monkeypatch, [
        _gc_result(eligible=eligible), _gc_result(eligible=eligible),
        _gc_result(eligible=eligible, complete=True),
    ], seconds_per_call=2.0)
    pauses = []
    module.run_export_maintenance(
        object(), _settings(tmp_path, gc=gc), clock=lambda: clock[0], sleep=pauses.append,
    )
    assert pauses == [expected, expected]


def test_gc_metrics_are_observed_once_with_run_totals(tmp_path, monkeypatch):
    clock = _scripted_gc(monkeypatch, [
        _gc_result(eligible=2, eligible_bytes=50, deleted_bytes=30),
        _gc_result(eligible=1, eligible_bytes=20, deleted_bytes=20, complete=True),
    ], seconds_per_call=0.0)
    observed = []
    monkeypatch.setattr(module, "observe_export_artifact_gc", observed.append)
    report = module.run_export_maintenance(
        object(), _settings(tmp_path), clock=lambda: clock[0], sleep=lambda _: None,
    )
    # 호출마다 덮어쓰면 reclaimable에 마지막 호출분(0)만 남는다. run 합계(70 - 50)를 한 번 쓴다.
    assert len(observed) == 1
    assert observed[0]["eligible_bytes"] == 70 and observed[0]["deleted_bytes"] == 50
    assert observed[0]["deleted"] == 3 and report["gc_calls"] == 2


def test_gc_stops_without_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "retire_settled_attempts", lambda *a, **k: {})
    calls = []

    def stuck(*args, **kwargs):
        calls.append(kwargs)
        return {"mode": "delete", "visited": 0, "live": 0, "eligible": 0, "complete": False,
                "pending": 3}

    monkeypatch.setattr(module, "collect_export_artifacts", stuck)
    report = module.run_export_maintenance(object(), _settings(tmp_path))
    assert len(calls) == 1 and report["gc_pending"] == 3 and report["gc_complete"] is False
    assert calls[0]["scan"].page_size == module.GC_SCAN_PAGE
    assert calls[0]["scan"].statement_seconds == module.GC_SCAN_STATEMENT_SECONDS


def _state(name, *, kind="SENSOR", status="RUNNING", location=module.LOCATION_NAME):
    return SimpleNamespace(
        instigator_name=name, instigator_type=SimpleNamespace(value=kind),
        status=SimpleNamespace(value=status),
        repository_selector=SimpleNamespace(location_name=location),
    )


@pytest.mark.parametrize("states,expected", [
    ([], False),
    ([_state("weather_export_publisher")], True),
    ([_state("weather_export_publisher", status="STOPPED")], False),
    ([_state("weather_export_publisher", location="other.definitions")], False),
    ([_state("weather_export_publisher", kind="SCHEDULE")], False),
    ([_state("other_sensor")], False),
])
def test_maintenance_follows_the_publisher_switch(states, expected):
    instance = SimpleNamespace(all_instigator_state=lambda: states)
    assert module.publisher_sensor_running(instance) is expected


def test_maintenance_needs_enabled_exports_with_absolute_root(tmp_path):
    assert module.maintenance_enabled(_settings(tmp_path))
    assert not module.maintenance_enabled(_settings(tmp_path, enabled=False))
    assert not module.maintenance_enabled(WeatherSettings(service_exports_enabled=True))


def test_job_and_schedule_are_registered_with_their_bounds():
    from kortravelweather_dagster import definitions

    job = definitions.defs.get_job_def(module.JOB_NAME)
    assert job.tags["dagster/max_runtime"] == str(module.MAX_RUNTIME_SECONDS)
    schedule = definitions.defs.get_schedule_def(module.SCHEDULE_NAME)
    assert schedule.cron_schedule == module.CRON_SCHEDULE == "52 * * * *"
    assert schedule.execution_timezone == "Asia/Seoul"
    assert schedule.default_status is DefaultScheduleStatus.RUNNING
    assert schedule.job_name == module.JOB_NAME


def test_disabled_job_run_does_nothing(monkeypatch):
    from dagster import ResourceDefinition

    monkeypatch.setenv("KOR_TRAVEL_WEATHER_SERVICE_EXPORTS_ENABLED", "false")

    def forbidden():
        pytest.fail("비활성 정리는 repository를 만들지 않습니다.")

    resource = ResourceDefinition.hardcoded_resource(SimpleNamespace(create_repository=forbidden))
    result = module.weather_export_maintenance_job.execute_in_process(
        resources={"weather_repository": resource},
    )
    assert result.success
    assert result.output_for_node("weather_export_maintenance") == {"skipped": True}
