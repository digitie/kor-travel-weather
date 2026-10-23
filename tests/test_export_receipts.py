from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, select, text

from kortravelweather.export_artifacts import (
    ExportArtifactError,
    ExportArtifactStore,
    prepare_export_chunk,
)
from kortravelweather.export_receipts import (
    ExportCapture,
    export_receipt_tables,
    source_scope_key,
)
from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.repository import (
    Base,
    WeatherCurrentValueRow,
    WeatherRepository,
    WeatherValueRow,
)


@pytest.fixture(scope="module")
def receipt_repository():
    repository = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    repository.create_schema()
    yield repository
    repository.engine.dispose()


def _input(repository):
    identity = uuid.uuid4().hex
    location_id = "receipt-" + identity
    provider = "receipt-provider-" + identity
    old = datetime.now(UTC) - timedelta(hours=2)
    repository.create_location(
        WeatherLocation(location_id=location_id, name="검증", latitude=37.5, longitude=127)
    )
    record = dict(
        source_record_key="source-" + identity,
        provider=provider,
        dataset_key="d",
        source_entity_type="weather_response",
        source_entity_id=location_id,
        payload={"rows": ["1"]},
        fetched_at=old,
    )
    value = WeatherValue(
        location_id=location_id,
        provider=provider,
        dataset_key="d",
        weather_domain="weather",
        forecast_style=ForecastStyle.OBSERVED,
        metric_key="temperature",
        target_at=old,
        value_number=Decimal("1"),
        payload={"obsr_value": "1"},
        source_record_key=record["source_record_key"],
    )
    return record, value


def _capture(repository, record, root, *, observed=None):
    run = repository.start_sync_run(provider=record["provider"], dataset_key=record["dataset_key"])
    record = {**record, "run_id": run.run_id}
    attempt = repository.begin_export_attempt(
        run_id=run.run_id,
        scope_key="all",
        execution_mode="native",
        planned_sources=[
            source_scope_key(
                record["provider"],
                record["dataset_key"],
                record["source_entity_type"],
                record["source_entity_id"],
            )
        ],
    )
    capture = ExportCapture(
        attempt=attempt,
        chunk_key="chunk-1",
        source_observed_at={record["source_record_key"]: observed or datetime.now(UTC)},
        artifacts=ExportArtifactStore(root),
    )
    return record, capture


def _receipt(repository, capture):
    _, chunks = export_receipt_tables(Base.metadata)
    with repository.engine.connect() as connection:
        return (
            connection.execute(
                select(chunks).where(chunks.c.attempt_id == capture.attempt.attempt_id)
            )
            .mappings()
            .one_or_none()
        )


def _read(capture, receipt):
    return list(
        capture.artifacts.read(
            receipt["artifact_digest"],
            byte_count=receipt["byte_count"],
            point_count=receipt["point_count"],
        )
    )


def test_replay_zero_inserts_still_has_canonical_receipt_and_new_observation(
    receipt_repository, tmp_path
):
    repository = receipt_repository
    record, value = _input(repository)
    assert repository.ingest_batch(source_records=[record], values=[value]) == 1
    observed = datetime.now(UTC)
    record, capture = _capture(repository, record, tmp_path, observed=observed)
    assert (
        repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
        == 0
    )
    receipt = _receipt(repository, capture)
    assert receipt["point_count"] == 1 and receipt["source_collected_at"] == observed
    (point,) = _read(capture, receipt)
    assert datetime.fromisoformat(point["known_at"]) == record["fetched_at"]
    assert point["source_record_key"] == record["source_record_key"]
    assert point["value_number"] == "1"
    attempts, _ = export_receipt_tables(Base.metadata)
    with repository.engine.connect() as connection:
        assert (
            connection.scalar(
                select(attempts.c.status).where(attempts.c.attempt_id == capture.attempt.attempt_id)
            )
            == "preparing"
        )


def test_source_alias_and_legacy_payload_use_persisted_row(receipt_repository, tmp_path):
    repository = receipt_repository
    record, value = _input(repository)
    repository.ingest_batch(source_records=[record], values=[value])
    original_key = record["source_record_key"]
    alias = "alias-" + uuid.uuid4().hex
    record = {**record, "source_record_key": alias}
    value = value.model_copy(
        update={"source_record_key": alias, "payload": {"obsr_value": "1", "category": "T1H"}}
    )
    record, capture = _capture(repository, record, tmp_path)
    assert (
        repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
        == 0
    )
    (point,) = _read(capture, _receipt(repository, capture))
    assert point["source_record_key"] == original_key
    assert point["observation_evidence"] == {"obsr_value": "1"}


def test_source_current_cas_loss_does_not_drop_valid_success_candidate(
    receipt_repository, tmp_path
):
    repository = receipt_repository
    old_record, old_value = _input(repository)
    repository.ingest_batch(source_records=[old_record], values=[old_value])
    newer = {
        **old_record,
        "source_record_key": "new-" + uuid.uuid4().hex,
        "payload": {"rows": ["2"]},
        "fetched_at": datetime.now(UTC),
    }
    newer_value = old_value.model_copy(
        update={"source_record_key": newer["source_record_key"], "value_number": Decimal("2")}
    )
    failed_run = repository.start_sync_run(provider=newer["provider"], dataset_key="d")
    newer["run_id"] = failed_run.run_id
    repository.ingest_batch(source_records=[newer], values=[newer_value])
    repository.finish_sync_run(
        failed_run.run_id, status="failed", error="injected after chunk commit"
    )
    old_record, capture = _capture(repository, old_record, tmp_path)
    assert (
        repository.ingest_batch(
            source_records=[old_record], values=[old_value], export_capture=capture
        )
        == 0
    )
    (point,) = _read(capture, _receipt(repository, capture))
    assert point["value_number"] == "1"
    assert point["source_record_key"] == old_record["source_record_key"]
    with repository.engine.connect() as connection:
        assert (
            connection.scalar(
                select(WeatherCurrentValueRow.source_record_key).where(
                    WeatherCurrentValueRow.location_id == old_value.location_id
                )
            )
            == newer["source_record_key"]
        )


def test_transaction_rollback_has_no_receipt_or_artifact(receipt_repository, tmp_path, monkeypatch):
    repository = receipt_repository
    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    original = repository._ingest_batch_session

    def abort(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("failure before commit")

    monkeypatch.setattr(repository, "_ingest_batch_session", abort)
    with pytest.raises(RuntimeError, match="before commit"):
        repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
    assert _receipt(repository, capture) is None
    assert not list(tmp_path.iterdir())
    with repository.engine.connect() as connection:
        assert (
            connection.execute(
                select(WeatherValueRow.value_id).where(
                    WeatherValueRow.location_id == value.location_id
                )
            ).first()
            is None
        )


def test_artifact_failure_preserves_committed_fact_and_marks_export_failed(
    receipt_repository, tmp_path, monkeypatch
):
    repository = receipt_repository
    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)

    def fail(_chunk):
        raise OSError("isolated storage failure")

    monkeypatch.setattr(capture.artifacts, "publish", fail)
    assert (
        repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
        == 1
    )
    assert _receipt(repository, capture)["point_count"] == 1
    attempts, _ = export_receipt_tables(Base.metadata)
    with repository.engine.connect() as connection:
        state = (
            connection.execute(
                select(attempts).where(attempts.c.attempt_id == capture.attempt.attempt_id)
            )
            .mappings()
            .one()
        )
        assert state["status"] == "failed" and state["error_code"] == "artifact_write_failed"
        assert (
            connection.execute(
                select(WeatherValueRow.value_id).where(
                    WeatherValueRow.location_id == value.location_id
                )
            ).first()
            is not None
        )
    assert not list(tmp_path.iterdir())


def test_lost_commit_response_can_replay_receipt_and_complete_file(
    receipt_repository, tmp_path, monkeypatch
):
    repository = receipt_repository
    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    original = repository._session_factory

    class LostResponse:
        @contextmanager
        def begin(self):
            with original.begin() as session:

                def lost(_session):
                    if not _session.in_nested_transaction():
                        raise RuntimeError("commit response lost")

                event.listen(session, "after_commit", lost)
                yield session

    monkeypatch.setattr(repository, "_session_factory", LostResponse())
    with pytest.raises(RuntimeError, match="response lost"):
        repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
    receipt = _receipt(repository, capture)
    assert receipt is not None and not list(tmp_path.iterdir())
    monkeypatch.setattr(repository, "_session_factory", original)
    assert (
        repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
        == 0
    )
    assert len(_read(capture, _receipt(repository, capture))) == 1


def test_expired_fact_is_not_an_accepted_point(receipt_repository, tmp_path):
    repository = receipt_repository
    record, value = _input(repository)
    expired_at = datetime.now(UTC) - timedelta(hours=8)
    record.update(provider="python-kma-api", dataset_key="kma_short_forecast")
    value = value.model_copy(
        update={
            "provider": "python-kma-api",
            "dataset_key": "kma_short_forecast",
            "weather_domain": "kma_short_forecast",
            "forecast_style": ForecastStyle.SHORT,
            "target_at": expired_at,
            "valid_at": expired_at,
        }
    )
    record, capture = _capture(repository, record, tmp_path)
    assert (
        repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
        == 0
    )
    assert _read(capture, _receipt(repository, capture)) == []
    repository.finish_sync_run(record["run_id"], status="success")


@pytest.mark.parametrize("mode", ["native", "dagster"])
def test_dagster_missing_identity_never_falls_back_to_native(receipt_repository, mode):
    repository = receipt_repository
    record, _ = _input(repository)
    repository.orchestrator_run_id = "dagster-" + uuid.uuid4().hex
    run = repository.start_sync_run(provider=record["provider"], dataset_key="d")
    repository.orchestrator_run_id = None
    with pytest.raises(ExportArtifactError):
        repository.begin_export_attempt(
            run_id=run.run_id, scope_key="all", execution_mode=mode, planned_sources=["a" * 64]
        )


def test_corrupted_or_replaced_artifact_is_rejected(tmp_path):
    store = ExportArtifactStore(tmp_path)
    chunk = prepare_export_chunk([{"value": 1}], source_collected_at=datetime.now(UTC))
    store.publish(chunk)
    path = tmp_path / (chunk.digest + ".jsonl")
    path.chmod(0o600)
    path.write_bytes(b'{"value":2}\n')
    with pytest.raises(ExportArtifactError):
        list(store.read(chunk.digest, byte_count=len(chunk.payload), point_count=1))


def test_artifact_bounds_stop_an_unbounded_generator():
    def forever():
        while True:
            yield {"value": 1}

    with pytest.raises(ExportArtifactError, match="point 상한"):
        prepare_export_chunk(forever(), source_collected_at=datetime.now(UTC))


def test_artifact_rejects_naive_clock_and_oversized_bytes():
    with pytest.raises(ExportArtifactError, match="timezone"):
        prepare_export_chunk([], source_collected_at=datetime(2026, 1, 1))
    with pytest.raises(ExportArtifactError, match="byte 상한"):
        prepare_export_chunk(
            [{"text": "a" * (8 * 1024 * 1024)}], source_collected_at=datetime.now(UTC)
        )


@pytest.mark.parametrize("problem", ["lost_owner", "missing_clock", "outside_scope"])
def test_bad_export_context_preserves_raw_without_creating_receipt(
    receipt_repository,
    tmp_path,
    problem,
):
    from dataclasses import replace

    from kortravelweather.export_receipts import ExportAttempt

    repository = receipt_repository
    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    if problem == "lost_owner":
        capture = replace(
            capture, attempt=ExportAttempt(capture.attempt.attempt_id, str(uuid.uuid4()))
        )
    elif problem == "missing_clock":
        capture = replace(capture, source_observed_at={})
    else:
        attempts, _ = export_receipt_tables(Base.metadata)
        with repository.engine.begin() as connection:
            connection.execute(
                attempts.update()
                .where(attempts.c.attempt_id == capture.attempt.attempt_id)
                .values(planned_sources=["f" * 64])
            )
    assert (
        repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
        == 1
    )
    assert _receipt(repository, capture) is None and not list(tmp_path.iterdir())
    with repository.engine.connect() as connection:
        assert (
            connection.execute(
                select(WeatherValueRow.value_id).where(
                    WeatherValueRow.location_id == value.location_id
                )
            ).first()
            is not None
        )


def test_changed_chunk_retry_never_overwrites_original_receipt(receipt_repository, tmp_path):
    from dataclasses import replace

    repository = receipt_repository
    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    assert (
        repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
        == 1
    )
    original = dict(_receipt(repository, capture))
    other = {
        **record,
        "source_record_key": "changed-" + uuid.uuid4().hex,
        "payload": {"rows": ["2"]},
        "fetched_at": datetime.now(UTC),
    }
    other_value = value.model_copy(
        update={"source_record_key": other["source_record_key"], "value_number": Decimal("2")}
    )
    changed = replace(capture, source_observed_at={other["source_record_key"]: datetime.now(UTC)})
    assert (
        repository.ingest_batch(
            source_records=[other], values=[other_value], export_capture=changed
        )
        == 1
    )
    assert dict(_receipt(repository, capture)) == original
    assert _read(capture, original)[0]["value_number"] == "1"
    attempts, _ = export_receipt_tables(Base.metadata)
    with repository.engine.connect() as connection:
        assert (
            connection.scalar(
                select(attempts.c.status).where(attempts.c.attempt_id == capture.attempt.attempt_id)
            )
            == "failed"
        )


def test_artifact_is_written_after_commit_and_pool_release(
    receipt_repository, tmp_path, monkeypatch
):
    repository = receipt_repository
    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    publish = capture.artifacts.publish
    observed = []

    def after_commit(chunk):
        assert repository.engine.pool.checkedout() == 0
        assert _receipt(repository, capture) is not None
        observed.append(True)
        publish(chunk)

    monkeypatch.setattr(capture.artifacts, "publish", after_commit)
    assert (
        repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
        == 1
    )
    assert observed == [True]


def test_artifact_number_matches_actual_numeric_column(receipt_repository, tmp_path):
    repository = receipt_repository
    record, value = _input(repository)
    value = value.model_copy(update={"value_number": Decimal("1.23445")})
    record, capture = _capture(repository, record, tmp_path)
    assert (
        repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
        == 1
    )
    (point,) = _read(capture, _receipt(repository, capture))
    with repository.engine.connect() as connection:
        actual = connection.scalar(
            select(WeatherValueRow.value_number).where(
                WeatherValueRow.location_id == value.location_id
            )
        )
    assert Decimal(point["value_number"]) == actual == Decimal("1.2345")


@pytest.mark.parametrize("failure_stage", ["receipt", "artifact"])
def test_export_row_lock_never_rolls_back_raw_or_blocks_forever(
    receipt_repository, tmp_path, monkeypatch, failure_stage
):
    import time

    repository = receipt_repository
    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    attempts, _ = export_receipt_tables(Base.metadata)

    def short_timeout(session):
        session.execute(text("SET LOCAL lock_timeout = '50ms'"))

    monkeypatch.setattr(repository, "_set_ingest_lock_timeout", short_timeout)
    with repository.engine.connect() as blocker:
        transaction = blocker.begin()

        def hold_attempt():
            blocker.execute(
                select(attempts)
                .where(attempts.c.attempt_id == capture.attempt.attempt_id)
                .with_for_update()
            ).one()

        if failure_stage == "receipt":
            hold_attempt()
        else:

            def disk_failure(_chunk):
                # 원본 commit 이후 다른 actor가 attempt 행을 잡은 실제 경합이다.
                hold_attempt()
                raise OSError("disk unavailable")

            monkeypatch.setattr(capture.artifacts, "publish", disk_failure)
        started = time.monotonic()
        try:
            assert (
                repository.ingest_batch(
                    source_records=[record], values=[value], export_capture=capture
                )
                == 1
            )
            assert time.monotonic() - started < 5
        finally:
            transaction.rollback()
    with repository.engine.connect() as connection:
        assert (
            connection.scalar(
                select(WeatherValueRow.value_id).where(
                    WeatherValueRow.location_id == value.location_id
                )
            )
            is not None
        )
    receipt = _receipt(repository, capture)
    assert (receipt is None) == (failure_stage == "receipt")
    assert not list(tmp_path.iterdir())


def test_source_without_run_cannot_be_admitted_under_another_export_run(
    receipt_repository, tmp_path
):
    from dataclasses import replace

    repository = receipt_repository
    record, value = _input(repository)
    other, other_value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    attempts, _ = export_receipt_tables(Base.metadata)
    with repository.engine.begin() as connection:
        connection.execute(
            attempts.update()
            .where(attempts.c.attempt_id == capture.attempt.attempt_id)
            .values(
                planned_sources=[
                    source_scope_key(
                        item["provider"],
                        item["dataset_key"],
                        item["source_entity_type"],
                        item["source_entity_id"],
                    )
                    for item in (record, other)
                ]
            )
        )
    capture = replace(
        capture,
        source_observed_at={
            item["source_record_key"]: datetime.now(UTC) for item in (record, other)
        },
    )
    assert (
        repository.ingest_batch(
            source_records=[record, other], values=[value, other_value], export_capture=capture
        )
        == 2
    )
    assert _receipt(repository, capture) is None
    assert not list(tmp_path.iterdir())
    with repository.engine.connect() as connection:
        assert (
            connection.scalar(
                select(attempts.c.status).where(attempts.c.attempt_id == capture.attempt.attempt_id)
            )
            == "failed"
        )
