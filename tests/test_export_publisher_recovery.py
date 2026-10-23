"""확정 terminal과 아직 불확실한 export 후보의 복구 경계를 검증한다."""

import os
import threading
import uuid

import pytest
from sqlalchemy import select, text

from kortravelweather.export_collector import publish_pending_exports
from kortravelweather.repository import WeatherRepository


@pytest.fixture(scope="module")
def publisher_repository():
    repository = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    repository.create_schema()
    yield repository
    repository.engine.dispose()


@pytest.fixture(autouse=True)
def clear_exports(publisher_repository):
    from kortravelweather.repository import Base

    names = [table.name for table in Base.metadata.tables.values()
             if table.name.startswith(("weather_export_", "weather_daily_"))]
    with publisher_repository.engine.begin() as connection:
        connection.execute(text("TRUNCATE " + ",".join(names) + " CASCADE"))


def candidate(repository, root, monkeypatch, *, terminal="success", dagster=False, descriptor=None,
              orchestrator=None):
    from test_export_heads import _candidate, _descriptor

    if dagster:
        monkeypatch.setattr(repository, "orchestrator_run_id", orchestrator or str(uuid.uuid4()))
        original = repository.begin_export_attempt

        def begin(**kwargs):
            kwargs["execution_mode"] = "dagster"
            return original(**kwargs)

        monkeypatch.setattr(repository, "begin_export_attempt", begin)
    return _candidate(repository, root, descriptor or _descriptor(repository), finish=terminal)


def state(repository, heads, identity):
    with repository.engine.connect() as connection:
        attempt = connection.execute(select(heads.lifecycle.attempts).where(
            heads.lifecycle.attempts.c.attempt_id == identity.attempt_id
        )).mappings().one()
        runs = heads.metadata.tables["weather_sync_runs"]
        run = connection.execute(select(runs).where(
            runs.c.run_id == attempt["run_id"]
        )).mappings().one()
    return attempt, run


@pytest.mark.parametrize("terminal", ["failed", "partial", "cancelled"])
def test_promotion_keeps_confirmed_rejection_distinct_from_pending(
    publisher_repository, tmp_path, terminal
):
    from test_export_heads import _candidate, _descriptor

    heads, identity, capture, *_ = _candidate(
        publisher_repository, tmp_path, _descriptor(publisher_repository), finish=terminal
    )
    assert heads.promote(identity, capture.artifacts) == "REJECTED"


@pytest.mark.parametrize("terminal", ["failed", "partial", "cancelled", "abandoned"])
def test_native_terminal_is_retired_without_losing_fact(publisher_repository, tmp_path,
                                                       monkeypatch, terminal):
    heads, identity, _, _, _, loaded = candidate(
        publisher_repository, tmp_path, monkeypatch, terminal=terminal,
    )
    assert publish_pending_exports(publisher_repository, tmp_path).results == {"REJECTED": 1}
    attempt, run = state(publisher_repository, heads, identity)
    assert attempt["status"] == "failed"
    assert run["status"] == terminal and run["values_loaded"] == loaded > 0
    assert publish_pending_exports(publisher_repository, tmp_path).processed == 0


@pytest.mark.parametrize("terminal", ["FAILURE", "CANCELED"])
def test_dead_worker_exact_terminal_closes_running_native(publisher_repository, tmp_path,
                                                        monkeypatch, terminal):
    heads, identity, *_ = candidate(publisher_repository, tmp_path, monkeypatch,
                                    terminal=None, dagster=True)

    def lookup(_):
        assert publisher_repository.engine.pool.checkedout() == 0
        return terminal

    assert publish_pending_exports(publisher_repository, tmp_path,
                                   dagster_status=lookup).results == {"REJECTED": 1}
    attempt, run = state(publisher_repository, heads, identity)
    assert attempt["status"] == run["status"] == "failed"
    assert run["values_loaded"] > 0


@pytest.mark.parametrize("terminal", [None, "STARTED", "SUCCESS"])
def test_unknown_or_early_success_never_infers_native_failure(publisher_repository, tmp_path,
                                                           monkeypatch, terminal):
    heads, identity, *_ = candidate(publisher_repository, tmp_path, monkeypatch,
                                    terminal=None, dagster=True)
    assert publish_pending_exports(publisher_repository, tmp_path,
                                   dagster_status=lambda _: terminal).results == {"NOT_READY": 1}
    attempt, run = state(publisher_repository, heads, identity)
    assert attempt["status"] == "preparing" and run["status"] == "running"


def test_exact_success_publishes_once(publisher_repository, tmp_path, monkeypatch):
    candidate(publisher_repository, tmp_path, monkeypatch, dagster=True)
    assert publish_pending_exports(publisher_repository, tmp_path,
                                   dagster_status=lambda _: "SUCCESS").results == {"PUBLISHED": 1}
    assert publish_pending_exports(publisher_repository, tmp_path,
                                   dagster_status=lambda _: "SUCCESS").processed == 0


def test_partial_preserves_old_head_and_new_epoch_can_retry(publisher_repository, tmp_path,
                                                          monkeypatch):
    from test_export_heads import _descriptor, _head

    descriptor = _descriptor(publisher_repository)
    heads, first, capture, *_ = candidate(publisher_repository, tmp_path, monkeypatch,
                                         descriptor=descriptor)
    assert heads.promote(first, capture.artifacts) == "PUBLISHED"
    before = _head(heads, descriptor)
    heads, failed, *_ = candidate(publisher_repository, tmp_path, monkeypatch,
                                  descriptor=descriptor, terminal="partial")
    assert publish_pending_exports(publisher_repository, tmp_path).results == {"REJECTED": 1}
    assert _head(heads, descriptor) == before
    heads, retry, *_ = candidate(publisher_repository, tmp_path, monkeypatch,
                                 descriptor=descriptor)
    assert publish_pending_exports(publisher_repository, tmp_path).results == {"PUBLISHED": 1}
    assert _head(heads, descriptor)["version"] == before["version"] + 1
    assert state(publisher_repository, heads, retry)[0]["owner_epochs"] != state(
        publisher_repository, heads, failed)[0]["owner_epochs"]


def test_success_native_is_not_rewritten_when_orchestrator_fails(publisher_repository, tmp_path,
                                                               monkeypatch):
    heads, identity, *_ = candidate(publisher_repository, tmp_path, monkeypatch, dagster=True)
    assert publish_pending_exports(publisher_repository, tmp_path,
                                   dagster_status=lambda _: "FAILURE").results == {"REJECTED": 1}
    attempt, run = state(publisher_repository, heads, identity)
    assert attempt["status"] == "failed" and run["status"] == "success"
    assert run["values_loaded"] > 0


def test_probe_timeout_is_unknown_and_connection_is_released(publisher_repository, tmp_path,
                                                           monkeypatch):
    heads, identity, *_ = candidate(publisher_repository, tmp_path, monkeypatch,
                                    dagster=True, terminal=None)
    release = threading.Event()

    def stalled(_):
        release.wait(10)
        return "FAILURE"

    try:
        assert publish_pending_exports(publisher_repository, tmp_path,
                                       dagster_status=stalled).results == {"NOT_READY": 1}
        assert publisher_repository.engine.pool.checkedout() == 0
        assert state(publisher_repository, heads, identity)[0]["status"] == "preparing"
    finally:
        release.set()


def test_missing_file_is_retryable_and_original_bytes_can_recover(publisher_repository, tmp_path,
                                                              monkeypatch):
    heads, identity, *_ = candidate(publisher_repository, tmp_path, monkeypatch)
    artifact = next(tmp_path.glob("*.jsonl"))
    saved = artifact.read_bytes()
    artifact.unlink()
    assert publish_pending_exports(publisher_repository, tmp_path).results == {"NOT_READY": 1}
    assert state(publisher_repository, heads, identity)[0]["status"] == "preparing"
    artifact.write_bytes(saved)
    assert publish_pending_exports(publisher_repository, tmp_path).results == {"PUBLISHED": 1}


def test_keyset_microbatch_finishes_and_wraps_for_new_candidates(publisher_repository, tmp_path,
                                                             monkeypatch):
    for _ in range(3):
        candidate(publisher_repository, tmp_path, monkeypatch, terminal="partial")
    first = publish_pending_exports(publisher_repository, tmp_path, limit=2)
    assert first.processed == 2 and first.has_more and first.next_cursor
    second = publish_pending_exports(publisher_repository, tmp_path, limit=2,
                                     cursor=first.next_cursor)
    assert second.processed == 1 and not second.has_more and second.next_cursor is None
    candidate(publisher_repository, tmp_path, monkeypatch)
    assert publish_pending_exports(publisher_repository, tmp_path, limit=2).results == {
        "PUBLISHED": 1,
    }


def test_dead_worker_marks_w4_pending_and_existing_repair_preserves_last_success(
    publisher_repository, tmp_path, monkeypatch
):
    from kortravelweather.export_status import ExportSourceStatuses
    from kortravelweather.repository import Base

    repo = publisher_repository
    monkeypatch.setattr(repo, "export_status_enabled", True)
    heads, first, capture, *_ = candidate(repo, tmp_path, monkeypatch)
    assert heads.promote(first, capture.artifacts) == "PUBLISHED"
    table = ExportSourceStatuses(Base.metadata).table
    with repo.engine.connect() as connection:
        before = connection.execute(select(table)).mappings().one()
    heads, failed, *_ = candidate(repo, tmp_path, monkeypatch, dagster=True, terminal=None)
    assert publish_pending_exports(repo, tmp_path,
                                   dagster_status=lambda _: "FAILURE").results == {"REJECTED": 1}
    assert state(repo, heads, failed)[1]["export_status_pending"] is True
    assert repo.reconcile_export_status({"python-kma-api"}) == 1
    with repo.engine.connect() as connection:
        after = connection.execute(select(table)).mappings().one()
    assert after["last_status"] == "failed"
    assert after["last_success_at"] == before["last_success_at"]
    assert after["source_collected_at"] == before["source_collected_at"]
    assert state(repo, heads, failed)[1]["export_status_pending"] is False
