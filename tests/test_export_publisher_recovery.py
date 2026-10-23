"""확정 terminal과 아직 불확실한 export 후보의 복구 경계를 검증한다."""

import os
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


def candidate(repository, root, monkeypatch, *, terminal="success", dagster=False, descriptor=None):
    from test_export_heads import _candidate, _descriptor

    if dagster:
        monkeypatch.setattr(repository, "orchestrator_run_id", str(uuid.uuid4()))
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
