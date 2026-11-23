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
    assert pauses == [module.GC_YIELD_SECONDS] * (report["gc_calls"] - 1)


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
    with pytest.raises(TimeoutError):
        module.run_export_maintenance(object(), _settings(tmp_path))


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
