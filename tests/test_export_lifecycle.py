from __future__ import annotations

import os
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from test_export_receipts import _capture, _input, _receipt

from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_lifecycle import ExportLifecycle
from kortravelweather.export_receipts import source_scope_key
from kortravelweather.repository import WeatherRepository


@pytest.fixture(scope="module")
def repository():
    result = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    result.create_schema()
    yield result
    result.engine.dispose()


def _scope(record):
    return source_scope_key(
        record["provider"],
        record["dataset_key"],
        record["source_entity_type"],
        record["source_entity_id"],
    )


def _ready(repository, tmp_path, *, mode="native", finish=True):
    record, value = _input(repository)
    if mode == "native":
        record, capture = _capture(repository, record, tmp_path)
    else:
        from kortravelweather.export_artifacts import ExportArtifactStore
        from kortravelweather.export_receipts import ExportCapture

        repository.orchestrator_run_id = "dagster-" + uuid.uuid4().hex
        run = repository.start_sync_run(
            provider=record["provider"], dataset_key=record["dataset_key"]
        )
        record = {**record, "run_id": run.run_id}
        identity = repository.begin_export_attempt(
            run_id=run.run_id,
            scope_key="all",
            planned_sources=[_scope(record)],
            execution_mode="dagster",
        )
        capture = ExportCapture(
            identity,
            "chunk-1",
            {record["source_record_key"]: datetime.now(UTC)},
            ExportArtifactStore(tmp_path),
        )
        repository.orchestrator_run_id = None
    lifecycle = ExportLifecycle(repository)
    lifecycle.claim(capture.attempt)
    lifecycle.expect_chunk(capture.attempt, capture.chunk_key, records=[record], values=[value])
    assert (
        repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
        == 1
    )
    lifecycle.close_source(capture.attempt, _scope(record), [capture.chunk_key])
    lifecycle.close_plan(capture.attempt)
    if finish:
        repository.finish_sync_run(record["run_id"], status="success")
    return lifecycle, record, value, capture


def test_native_evidence_waits_for_real_terminal_success(repository, tmp_path):
    lifecycle, record, _, capture = _ready(repository, tmp_path, finish=False)
    assert lifecycle.reconcile(capture.attempt, capture.artifacts) == "NOT_READY"
    repository.finish_sync_run(record["run_id"], status="success")
    assert lifecycle.reconcile(capture.attempt, capture.artifacts) == "ELIGIBLE"
    assert lifecycle.reconcile(capture.attempt, capture.artifacts) == "ELIGIBLE"
    with repository.engine.connect() as connection:
        assert (
            len(
                connection.execute(
                    select(lifecycle.evidence).where(
                        lifecycle.evidence.c.attempt_id == capture.attempt.attempt_id,
                    )
                ).all()
            )
            == 1
        )


@pytest.mark.parametrize("status", [None, True, "STARTED", "FAILURE", "CANCELED", "SUCCESS"])
def test_dagster_requires_exact_success_not_finished_bool(repository, tmp_path, status):
    lifecycle, _, _, capture = _ready(repository, tmp_path, mode="dagster")
    calls = []

    def lookup(run_id):
        assert repository.engine.pool.checkedout() == 0
        calls.append(run_id)
        return status

    result = lifecycle.reconcile(capture.attempt, capture.artifacts, dagster_status=lookup)
    expected = (
        "ELIGIBLE"
        if status == "SUCCESS"
        else "REJECTED"
        if status in {"FAILURE", "CANCELED"}
        else "NOT_READY"
    )
    assert result == expected and len(calls) == 1 and calls[0].startswith("dagster-")


def test_dagster_missing_or_failed_probe_does_not_fall_back_to_native(repository, tmp_path):
    lifecycle, _, _, capture = _ready(repository, tmp_path, mode="dagster")
    assert lifecycle.reconcile(capture.attempt, capture.artifacts) == "NOT_READY"

    def unavailable(_run_id):
        raise OSError("no control plane")

    assert (
        lifecycle.reconcile(capture.attempt, capture.artifacts, dagster_status=unavailable)
        == "NOT_READY"
    )


def test_terminal_missing_artifact_stays_unready_and_cannot_ingest_repair(repository, tmp_path):
    lifecycle, record, value, capture = _ready(repository, tmp_path)
    receipt = _receipt(repository, capture)
    (tmp_path / (receipt["artifact_digest"] + ".jsonl")).unlink()
    assert lifecycle.reconcile(capture.attempt, capture.artifacts) == "NOT_READY"
    with pytest.raises(ValueError):
        repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)


def test_unplanned_receipt_cannot_be_retroactively_claimed(repository, tmp_path):
    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
    with pytest.raises(ExportArtifactError):
        ExportLifecycle(repository).claim(capture.attempt)


def test_missing_segment_or_source_never_closes_barrier(repository, tmp_path):
    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    lifecycle = ExportLifecycle(repository)
    lifecycle.claim(capture.attempt)
    lifecycle.expect_chunk(capture.attempt, "chunk-1", records=[record], values=[value])
    lifecycle.expect_chunk(capture.attempt, "chunk-2", records=[record], values=[value])
    repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
    lifecycle.close_source(capture.attempt, _scope(record), ["chunk-1", "chunk-2"])
    with pytest.raises(ExportArtifactError, match="barrier"):
        lifecycle.close_plan(capture.attempt)


def test_changed_normalization_input_keeps_raw_but_rejects_receipt(repository, tmp_path):
    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    lifecycle = ExportLifecycle(repository)
    lifecycle.claim(capture.attempt)
    lifecycle.expect_chunk(capture.attempt, capture.chunk_key, records=[record], values=[value])
    assert (
        repository.ingest_batch(
            source_records=[record],
            values=[value.model_copy(update={"value_number": Decimal("2")})],
            export_capture=capture,
        )
        == 1
    )
    assert _receipt(repository, capture) is None


def test_two_chunk_validation_is_bounded_and_advances_only_when_all_ready(repository, tmp_path):
    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    lifecycle = ExportLifecycle(repository)
    lifecycle.claim(capture.attempt)
    for key in ("chunk-1", "chunk-2"):
        current_value = value.model_copy(update={"metric_key": key})
        current_capture = replace(capture, chunk_key=key)
        lifecycle.expect_chunk(capture.attempt, key, records=[record], values=[current_value])
        repository.ingest_batch(
            source_records=[record], values=[current_value], export_capture=current_capture
        )
    lifecycle.close_source(capture.attempt, _scope(record), ["chunk-1", "chunk-2"])
    lifecycle.close_plan(capture.attempt)
    repository.finish_sync_run(record["run_id"], status="success")
    assert lifecycle.reconcile(capture.attempt, capture.artifacts, max_chunks=1) == "NOT_READY"
    assert lifecycle.reconcile(capture.attempt, capture.artifacts, max_chunks=1) == "ELIGIBLE"


def test_scope_epoch_blocks_old_attempt_but_new_replay_can_succeed(repository, tmp_path):
    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    lifecycle = ExportLifecycle(repository)
    lifecycle.claim(capture.attempt)
    lifecycle.expect_chunk(capture.attempt, capture.chunk_key, records=[record], values=[value])
    newer = repository.begin_export_attempt(
        run_id=record["run_id"],
        scope_key="all",
        planned_sources=[_scope(record)],
        execution_mode="native",
    )
    lifecycle.claim(newer)
    assert (
        repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
        == 1
    )
    assert _receipt(repository, capture) is None
    fresh = replace(capture, attempt=newer)
    lifecycle.expect_chunk(newer, fresh.chunk_key, records=[record], values=[value])
    assert (
        repository.ingest_batch(source_records=[record], values=[value], export_capture=fresh) == 0
    )
    lifecycle.close_source(newer, _scope(record), [fresh.chunk_key])
    lifecycle.close_plan(newer)
    repository.finish_sync_run(record["run_id"], status="success")
    assert lifecycle.reconcile(newer, fresh.artifacts) == "ELIGIBLE"
    with pytest.raises(ExportArtifactError, match="소유권"):
        lifecycle.reconcile(capture.attempt, capture.artifacts)


def test_expected_plan_is_immutable_and_source_close_prevents_append(repository, tmp_path):
    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    lifecycle = ExportLifecycle(repository)
    lifecycle.claim(capture.attempt)
    lifecycle.claim(capture.attempt)
    lifecycle.expect_chunk(capture.attempt, capture.chunk_key, records=[record], values=[value])
    lifecycle.expect_chunk(capture.attempt, capture.chunk_key, records=[record], values=[value])
    with pytest.raises(ExportArtifactError):
        lifecycle.expect_chunk(
            capture.attempt,
            capture.chunk_key,
            records=[record],
            values=[value.model_copy(update={"value_number": Decimal("2")})],
        )
    lifecycle.close_source(capture.attempt, _scope(record), [capture.chunk_key])
    with pytest.raises(ExportArtifactError):
        lifecycle.expect_chunk(capture.attempt, "late", records=[record], values=[value])


def test_planned_scope_lock_failure_isolated_from_raw_ingest(repository, tmp_path, monkeypatch):
    import time

    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    lifecycle = ExportLifecycle(repository)
    lifecycle.claim(capture.attempt)
    lifecycle.expect_chunk(capture.attempt, capture.chunk_key, records=[record], values=[value])

    def short_timeout(session):
        session.execute(text("SET LOCAL lock_timeout='50ms'"))

    monkeypatch.setattr(repository, "_set_ingest_lock_timeout", short_timeout)
    with repository.engine.begin() as blocker:
        blocker.execute(
            select(lifecycle.owners)
            .where(lifecycle.owners.c.source_scope == _scope(record))
            .with_for_update()
        ).one()
        started = time.monotonic()
        assert (
            repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
            == 1
        )
        assert time.monotonic() - started < 5
    assert _receipt(repository, capture) is None


def test_missing_selected_source_never_closes_even_when_all_written_chunks_exist(
    repository, tmp_path
):
    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    lifecycle = ExportLifecycle(repository)
    missing = "f" * 64
    with repository.engine.begin() as connection:
        connection.execute(
            lifecycle.attempts.update()
            .where(
                lifecycle.attempts.c.attempt_id == capture.attempt.attempt_id,
            )
            .values(planned_sources=[_scope(record), missing])
        )
    lifecycle.claim(capture.attempt)
    lifecycle.expect_chunk(capture.attempt, capture.chunk_key, records=[record], values=[value])
    repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
    lifecycle.close_source(capture.attempt, _scope(record), [capture.chunk_key])
    with pytest.raises(ExportArtifactError, match="barrier"):
        lifecycle.close_plan(capture.attempt)


def test_zero_admitted_points_without_positive_empty_proof_are_not_eligible(repository, tmp_path):
    record, _value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    lifecycle = ExportLifecycle(repository)
    lifecycle.claim(capture.attempt)
    lifecycle.expect_chunk(capture.attempt, capture.chunk_key, records=[record], values=[])
    assert repository.ingest_batch(source_records=[record], values=[], export_capture=capture) == 0
    lifecycle.close_source(capture.attempt, _scope(record), [capture.chunk_key])
    with pytest.raises(ExportArtifactError, match="barrier"):
        lifecycle.close_plan(capture.attempt)


def test_validation_file_io_has_no_database_connection(repository, tmp_path, monkeypatch):
    lifecycle, _, _, capture = _ready(repository, tmp_path)
    # reconcile은 hash·크기·줄 수만 확인한다(verify). read와 같은 파일 경로(_lines)를 쓴다.
    original = capture.artifacts._lines
    observed = []

    def lines(*args, **kwargs):
        assert repository.engine.pool.checkedout() == 0
        observed.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(capture.artifacts, "_lines", lines)
    assert lifecycle.reconcile(capture.attempt, capture.artifacts) == "ELIGIBLE"
    assert observed == [True]


def test_normalization_plan_has_byte_bound(repository):
    from kortravelweather.export_lifecycle import normalization_input_digest

    _record, value = _input(repository)
    with pytest.raises(ExportArtifactError, match="상한"):
        normalization_input_digest(
            [value.model_copy(update={"value_text": "x" * (8 * 1024 * 1024)})]
        )


def test_planned_canonical_alias_replay_is_eligible_with_zero_new_rows(repository, tmp_path):
    record, value = _input(repository)
    repository.ingest_batch(source_records=[record], values=[value])
    alias = {**record, "source_record_key": "alias-" + uuid.uuid4().hex}
    alias_value = value.model_copy(update={"source_record_key": alias["source_record_key"]})
    alias, capture = _capture(repository, alias, tmp_path)
    lifecycle = ExportLifecycle(repository)
    lifecycle.claim(capture.attempt)
    lifecycle.expect_chunk(
        capture.attempt, capture.chunk_key, records=[alias], values=[alias_value]
    )
    assert (
        repository.ingest_batch(
            source_records=[alias], values=[alias_value], export_capture=capture
        )
        == 0
    )
    receipt = _receipt(repository, capture)
    assert receipt is not None and receipt["point_count"] == 1
    point = next(
        capture.artifacts.read(
            receipt["artifact_digest"], byte_count=receipt["byte_count"], point_count=1
        )
    )
    assert point["source_record_key"] == record["source_record_key"]
    lifecycle.close_source(capture.attempt, _scope(alias), [capture.chunk_key])
    lifecycle.close_plan(capture.attempt)
    repository.finish_sync_run(alias["run_id"], status="success")
    assert lifecycle.reconcile(capture.attempt, capture.artifacts) == "ELIGIBLE"


def test_positive_source_cannot_hide_empty_peer_in_shared_chunk(repository, tmp_path):
    from kortravelweather.export_artifacts import ExportArtifactStore
    from kortravelweather.export_receipts import ExportCapture

    first, value = _input(repository)
    second, _unused = _input(repository)
    second["provider"] = first["provider"]
    run = repository.start_sync_run(provider=first["provider"], dataset_key=first["dataset_key"])
    records = [{**item, "run_id": run.run_id} for item in (first, second)]
    identity = repository.begin_export_attempt(
        run_id=run.run_id,
        scope_key="all",
        execution_mode="native",
        planned_sources=[_scope(item) for item in records],
    )
    capture = ExportCapture(
        identity,
        "mixed",
        {item["source_record_key"]: datetime.now(UTC) for item in records},
        ExportArtifactStore(tmp_path),
    )
    lifecycle = ExportLifecycle(repository)
    lifecycle.claim(identity)
    lifecycle.expect_chunk(identity, "mixed", records=records, values=[value])
    assert (
        repository.ingest_batch(source_records=records, values=[value], export_capture=capture) == 1
    )
    assert _receipt(repository, capture)["source_point_counts"] == {
        _scope(first): 1,
        _scope(second): 0,
    }
    for item in records:
        lifecycle.close_source(identity, _scope(item), ["mixed"])
    with pytest.raises(ExportArtifactError, match="barrier"):
        lifecycle.close_plan(identity)


def test_chunk_capture_does_not_lock_unrelated_planned_source(repository, tmp_path, monkeypatch):
    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    lifecycle = ExportLifecycle(repository)
    other = source_scope_key(
        record["provider"], record["dataset_key"], record["source_entity_type"], "other-grid"
    )
    with repository.engine.begin() as connection:
        connection.execute(
            lifecycle.attempts.update()
            .where(
                lifecycle.attempts.c.attempt_id == capture.attempt.attempt_id,
            )
            .values(planned_sources=[_scope(record), other])
        )
    lifecycle.claim(capture.attempt)
    lifecycle.expect_chunk(capture.attempt, capture.chunk_key, records=[record], values=[value])

    def short_timeout(session):
        session.execute(text("SET LOCAL lock_timeout='50ms'"))

    monkeypatch.setattr(repository, "_set_ingest_lock_timeout", short_timeout)
    with repository.engine.begin() as blocker:
        blocker.execute(
            select(lifecycle.owners)
            .where(
                lifecycle.owners.c.source_scope == other,
            )
            .with_for_update()
        ).one()
        assert (
            repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
            == 1
        )
    assert _receipt(repository, capture)["point_count"] == 1
