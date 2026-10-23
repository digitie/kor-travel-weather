"""W4가 실제 native 실행/CAS/재수집/중단 복구와 연결되는지 검증한다."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from kortravelweather_api.app import create_app
from sqlalchemy import event, select, text

from kortravelweather.export_status import ExportSourceStatuses
from kortravelweather.export_status_reader import read_status
from kortravelweather.export_status_scopes import dataset_scopes
from kortravelweather.repository import Base, SourceRecordRow, WeatherRepository
from kortravelweather.settings import WeatherSettings

PROVIDER = "python-kma-api"
DATASET = "kma_ultra_short_nowcast"


@pytest.fixture
def repository(weather_test_database_url):
    repo = WeatherRepository(weather_test_database_url, export_status_enabled=True)
    repo.create_schema()
    # 선행 fixture의 create_all은 기존 표의 열을 바꾸지 않는다. 전용 시험 DB만 맞춘다.
    with repo.engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE weather_sync_runs ADD COLUMN IF NOT EXISTS export_status_scopes JSON"
        ))
        connection.execute(ExportSourceStatuses(Base.metadata).table.delete())
    yield repo
    repo.engine.dispose()


def records(run, *, clock, key="response", payload=None):
    return [{
        "run_id": run.run_id, "source_record_key": key, "provider": PROVIDER,
        "dataset_key": DATASET, "source_entity_type": "grid", "source_entity_id": "60,127",
        "payload": payload or {"temperature": 10}, "fetched_at": clock,
    }]


def state(repo):
    with repo.engine.begin() as connection:
        return ExportSourceStatuses(Base.metadata).read(
            connection, dataset_scopes(PROVIDER, {DATASET}), as_of=datetime.now(UTC),
        )[0]


def test_real_recollection_keeps_actual_clock_after_raw_alias(repository):
    first_clock = datetime.now(UTC) - timedelta(minutes=30)
    first = repository.start_sync_run(provider=PROVIDER, dataset_key=DATASET)
    repository.publish_and_finish(
        run_id=first.run_id, source_records=records(first, clock=first_clock),
        values=[], grids_fetched=1,
    )
    second_clock = first_clock + timedelta(minutes=20)
    second = repository.start_sync_run(provider=PROVIDER, dataset_key=DATASET)
    repository.publish_and_finish(
        run_id=second.run_id, source_records=records(second, clock=second_clock, key="new-key"),
        values=[], grids_fetched=1,
    )
    assert state(repository).source_collected_at == second_clock
    with repository.engine.begin() as connection:
        assert connection.scalar(select(SourceRecordRow.fetched_at)) == first_clock
    assert repository.engine.pool.checkedout() == 0


def test_failed_before_fetch_and_late_finish_preserve_last_success(repository):
    first = repository.start_sync_run(provider=PROVIDER, dataset_key=DATASET)
    repository.ingest_batch(source_records=records(first, clock=datetime.now(UTC)), values=[])
    repository.finish_sync_run(first.run_id, status="success")
    successful = state(repository)
    failed = repository.start_sync_run(provider=PROVIDER, dataset_key=DATASET)
    repository.finish_sync_run(failed.run_id, status="failed")
    repository.finish_sync_run(first.run_id, status="failed")
    current = state(repository)
    assert current.last_status == "failed" and current.last_attempt_failed
    assert current.last_success_at == successful.last_success_at
    assert current.source_collected_at == successful.source_collected_at


@pytest.mark.parametrize("owned", [False, True])
def test_recovery_projects_only_actual_cas_rows(repository, owned):
    repository.orchestrator_run_id = "dead-worker" if owned else None
    run = repository.start_sync_run(provider=PROVIDER, dataset_key=DATASET)
    if owned:
        assert repository.reconcile_interrupted_sync_runs(lambda _: True) == 1
    else:
        with repository.engine.begin() as connection:
            connection.execute(text(
                "UPDATE weather_sync_runs SET heartbeat_at=now()-interval '5 hours' "
                "WHERE run_id=:run"
            ), {"run": run.run_id})
        assert repository.reconcile_stale_sync_runs() == 1
    assert state(repository).last_status == "failed"
    assert repository.finish_sync_run(run.run_id, status="success").status == "failed"
    assert state(repository).last_status == "failed"


def test_bundle_plan_does_not_mark_other_datasets_success(repository):
    run = repository.start_sync_run(provider=PROVIDER, dataset_key="kma_weather_bundle")
    repository.set_export_status_datasets(run.run_id, {DATASET})
    repository.finish_sync_run(run.run_id, status="failed")
    assert state(repository).last_status == "failed"
    with repository.engine.begin() as connection:
        table = ExportSourceStatuses(Base.metadata).table
        assert connection.scalar(select(table.c.dataset_key)) == DATASET


def test_status_real_api_auth_gzip_etag_missing_scopes_and_no_fact_reads(repository):
    run = repository.start_sync_run(provider=PROVIDER, dataset_key=DATASET)
    repository.ingest_batch(source_records=records(run, clock=datetime.now(UTC)), values=[])
    repository.finish_sync_run(run.run_id, status="success")
    settings = WeatherSettings(
        environment="development", admin_token="private-service-token",
        service_exports_enabled=True,
        enabled_providers=[PROVIDER],
    )
    client = TestClient(create_app(settings, repository))
    statements = []
    def capture(_conn, _cursor, statement, _params, _context, _executemany):
        statements.append(statement.lower())
    event.listen(repository.engine, "before_cursor_execute", capture)
    try:
        assert client.get("/v1/service/exports/status").status_code == 401
        headers = {"x-admin-token": "private-service-token", "accept-encoding": "gzip"}
        response = client.get("/v1/service/exports/status", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) == 4 and body["meta"]["source_status"] == "stale"
        assert body["meta"]["source_collected_at"] is None
        assert response.headers["content-encoding"] == "gzip"
        assert response.headers["etag"].startswith('W/"')
        repeat = client.get("/v1/service/exports/status", headers={
            **headers, "if-none-match": response.headers["etag"],
        })
        assert repeat.status_code == 304 and not repeat.content
        assert client.get("/v1/service/exports/status?limit=1", headers=headers).status_code == 422
        assert not any("weather_values" in query or "weather_source_records" in query
                       or "weather_sync_runs" in query for query in statements)
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture)
    assert repository.engine.pool.checkedout() == 0


def test_unknown_actual_clock_never_uses_finish_time(repository):
    run = repository.start_sync_run(provider=PROVIDER, dataset_key=DATASET)
    repository.finish_sync_run(run.run_id, status="success")
    assert state(repository).source_collected_at is None
    page, _ = read_status(repository, {PROVIDER}, as_of=datetime.now(UTC))
    assert page["meta"]["source_status"] == "stale"


def test_metadata_failure_does_not_rollback_native_run(repository, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("bad derived status")
    monkeypatch.setattr(ExportSourceStatuses, "record_terminal", fail)
    run = repository.start_sync_run(provider=PROVIDER, dataset_key=DATASET)
    assert repository.finish_sync_run(run.run_id, status="success").status == "success"
    assert repository.get_sync_run(run.run_id).status == "success"


def test_status_off_keeps_derived_tables_empty(repository):
    repository.export_status_enabled = False
    run = repository.start_sync_run(provider=PROVIDER, dataset_key=DATASET)
    repository.ingest_batch(source_records=records(run, clock=datetime.now(UTC)), values=[])
    repository.finish_sync_run(run.run_id, status="success")
    assert state(repository).last_status is None
