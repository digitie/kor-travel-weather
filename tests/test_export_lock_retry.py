"""같은 run의 원본 ingest가 run 행을 잡고 있어도 export 준비가 버려지지 않는다.

운영 실측(2026-10-26, kma_short_forecast run): export의 ``SELECT weather_sync_runs ...
FOR UPDATE``가 같은 run의 ingest transaction(INSERT weather_values) 뒤에서 2.7~2.8초
기다리다 3초 lock_timeout으로 취소됐고 attempt는 사유 없이 PREPARE_FAILED가 됐다.
"""

import logging
import os
import threading
import time
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from kortravelweather import export_collector
from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_collector import KmaNowcastExportCollector, failure_reason
from kortravelweather.models import WeatherLocation
from kortravelweather.repository import WeatherRepository


@pytest.fixture(scope="module")
def repository():
    result = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    result.create_schema()
    yield result
    result.engine.dispose()


@pytest.fixture
def run_and_location(repository):
    key = "lock-" + uuid.uuid4().hex
    repository.create_location(
        WeatherLocation(location_id=key, name="잠금", latitude=37.5, longitude=127, nx=61, ny=128)
    )
    run = repository.start_sync_run(
        provider="python-kma-api", dataset_key="kma_ultra_short_nowcast"
    )
    yield run, key
    repository.finish_sync_run(run.run_id, status="failed")


def _hold_run_row(repository, run_id, seconds, locked):
    with repository.engine.connect() as connection, connection.begin():
        connection.execute(
            text("SELECT run_id FROM weather_sync_runs WHERE run_id = :run_id FOR UPDATE"),
            {"run_id": run_id},
        )
        locked.set()
        time.sleep(seconds)


def test_prepare_outlasts_the_same_runs_ingest_lock(repository, tmp_path, run_and_location):
    run, key = run_and_location
    collector = KmaNowcastExportCollector(repository, run_id=run.run_id, artifact_root=tmp_path)
    locked = threading.Event()
    # lock_timeout(3초)보다 오래 잡아 첫 시도는 반드시 취소되게 한다.
    holder = threading.Thread(target=_hold_run_row, args=(repository, run.run_id, 4.5, locked))
    holder.start()
    assert locked.wait(10)
    try:
        prepared = collector.prepare(
            entity_id="grid:61:128", nx=61, ny=128, location_ids=[key],
        )
    finally:
        holder.join()
    assert prepared is not None
    attempts = collector.heads.lifecycle.attempts
    with repository.engine.connect() as connection:
        status = connection.scalar(
            select(attempts.c.status).where(attempts.c.attempt_id == prepared.identity.attempt_id)
        )
    assert status == "preparing"


def test_rejection_records_only_a_sanitized_reason(
    repository, tmp_path, run_and_location, caplog, monkeypatch
):
    run, _ = run_and_location
    collector = KmaNowcastExportCollector(repository, run_id=run.run_id, artifact_root=tmp_path)
    identity = repository.begin_export_attempt(
        run_id=run.run_id, scope_key="lock-test", planned_sources=["a" * 64],
        execution_mode="native",
    )

    class LockNotAvailable(Exception):
        sqlstate = "55P03"

    error = OperationalError(
        "SELECT secret_table FOR UPDATE", {"password": "secret-value"}, LockNotAvailable()
    )
    # Alembic의 fileConfig(앞선 migration 시험)가 이미 있는 logger를 끈다.
    # 이 시험은 우리 호출이 남기는 문장만 본다(tests/test_kma_missing_values.py와 같은 방식).
    monkeypatch.setattr(export_collector.logger, "disabled", False)
    with caplog.at_level(logging.WARNING, logger=export_collector.logger.name):
        collector._reject(identity, "PREPARE_FAILED", error)
    attempts = collector.heads.lifecycle.attempts
    with repository.engine.connect() as connection:
        row = connection.execute(
            select(attempts.c.status, attempts.c.error_code).where(
                attempts.c.attempt_id == identity.attempt_id
            )
        ).one()
    assert tuple(row) == ("failed", "PREPARE_FAILED:lock_timeout")
    assert "lock_timeout" in caplog.text and "secret" not in caplog.text


@pytest.mark.parametrize(
    "error,reason",
    [
        (ExportArtifactError("증명 실패"), "proof"),
        (ValueError("raw payload"), "ValueError"),
    ],
)
def test_failure_reason_is_a_closed_classification(error, reason):
    assert failure_reason(error) == reason
