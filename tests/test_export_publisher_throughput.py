"""publisher 처리량: 일괄 퇴역·잠금 없는 대기 판정·최신 우선·후보 단위 격리·검증 비용.

운영(odroid 2026-10-26) 실측: 3분 tick마다 8초만 쓰고 후보당 1~2초(Dagster 조회 2회
~0.4초씩, 수집 중인 run 행 잠금 대기)라 시간당 생성 ~2,000건 중 ~60건만 공개됐다.
"""

import hashlib
import json
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import test_export_publisher_recovery as fixtures
from sqlalchemy import select, text

from kortravelweather import export_artifacts, export_collector
from kortravelweather.export_collector import (
    TickStatusProbe,
    _publisher_terminal,
    publish_fresh_exports,
    publish_pending_exports,
    retire_settled_attempts,
)
from kortravelweather.export_heads import ExportHeads

publisher_repository = fixtures.publisher_repository
clear_exports = fixtures.clear_exports
candidate = fixtures.candidate
state = fixtures.state


def _same_run_candidates(repo, root, monkeypatch, count, *, orchestrator=None):
    """한 run 안에 서로 다른 scope의 완결 후보를 만든다. run은 호출자가 끝낸다."""
    from test_export_heads import _candidate, _descriptor

    with monkeypatch.context() as patch:
        if orchestrator:
            patch.setattr(repo, "orchestrator_run_id", orchestrator)
            original = repo.begin_export_attempt

            def begin(**kwargs):
                kwargs["execution_mode"] = "dagster"
                return original(**kwargs)

            patch.setattr(repo, "begin_export_attempt", begin)
        run = repo.start_sync_run(provider="python-kma-api",
                                  dataset_key="kma_ultra_short_nowcast")
        patch.setattr(repo, "start_sync_run", lambda **_: run)
        made = [_candidate(repo, root, _descriptor(repo), finish=None)[1] for _ in range(count)]
    return run, made


def _status(repo, identity):
    heads = ExportHeads(repo)
    with repo.engine.connect() as connection:
        return tuple(connection.execute(
            select(heads.lifecycle.attempts.c.status, heads.lifecycle.attempts.c.error_code)
            .where(heads.lifecycle.attempts.c.attempt_id == identity.attempt_id)
        ).one())


def _ownership_case(repo, ownership):
    """`test_superseded_requires_every_claimed_scope...`와 같은 owner 모양을 만든다."""
    heads = ExportHeads(repo)
    sources = ["a" * 64, "b" * 64]
    run = repo.start_sync_run(provider="test-publisher", dataset_key="multi-scope")
    old = repo.begin_export_attempt(run_id=run.run_id, scope_key="multi",
                                    planned_sources=sources, execution_mode="native")
    if ownership != "unclaimed":
        heads.lifecycle.claim(old)
    repo.finish_sync_run(run.run_id, status="success")
    newer_run = repo.start_sync_run(provider="test-publisher", dataset_key="newer")
    newer = repo.begin_export_attempt(
        run_id=newer_run.run_id, scope_key="newer",
        planned_sources=sources if ownership != "partial" else sources[:1],
        execution_mode="native",
    )
    heads.lifecycle.claim(newer)
    with repo.engine.begin() as connection:
        if ownership == "missing":
            connection.execute(heads.lifecycle.owners.delete().where(
                heads.lifecycle.owners.c.source_scope == sources[1],
            ))
        elif ownership == "same_epoch":
            connection.execute(heads.lifecycle.owners.update().where(
                heads.lifecycle.owners.c.source_scope == sources[1],
            ).values(owner_epoch=1))
    return old, newer, newer_run


@pytest.mark.parametrize("path", ["bulk", "single"])
@pytest.mark.parametrize("ownership,expected", [
    ("partial", ("preparing", None)),
    ("missing", ("preparing", None)),
    ("same_epoch", ("preparing", None)),
    ("unclaimed", ("preparing", None)),
    ("all_new", ("failed", "SUPERSEDED")),
])
def test_bulk_retirement_uses_the_single_candidate_supersede_predicate(
    publisher_repository, tmp_path, path, ownership, expected,
):
    repo = publisher_repository
    old, newer, newer_run = _ownership_case(repo, ownership)
    try:
        if path == "bulk":
            retired = retire_settled_attempts(repo)
            assert retired["SUPERSEDED"] == (expected[0] == "failed")
        else:
            _publisher_terminal(ExportHeads(repo), old, None)
        assert _status(repo, old) == expected
        assert _status(repo, newer) == ("preparing", None)
    finally:
        repo.finish_sync_run(newer_run.run_id, status="failed")


def test_bulk_retirement_writes_the_same_audit_fields_and_spares_published(
    publisher_repository, tmp_path, monkeypatch,
):
    from test_export_heads import _descriptor

    repo = publisher_repository
    heads = ExportHeads(repo)
    descriptor = _descriptor(repo)
    _, published, capture, *_ = candidate(repo, tmp_path, monkeypatch, descriptor=descriptor)
    assert heads.promote(published, capture.artifacts) == "PUBLISHED"
    _, failed, *_ = candidate(repo, tmp_path, monkeypatch, terminal="partial")
    with repo.engine.begin() as connection:
        connection.execute(heads.pins.insert().values(
            attempt_id=failed.attempt_id, chunk_key="p" * 64, artifact_digest="0" * 64,
            created_at=datetime.now(UTC),
        ))
    assert retire_settled_attempts(repo) == {"SUPERSEDED": 0, "CONFIRMED_TERMINAL": 1}
    assert _status(repo, failed) == ("failed", "CONFIRMED_TERMINAL")
    assert _status(repo, published) == ("sealed", None)
    with repo.engine.connect() as connection:
        assert connection.scalar(select(heads.pins.c.attempt_id)) is None


def test_bulk_retirement_skips_locked_rows_and_stays_bounded(publisher_repository):
    repo = publisher_repository
    run = repo.start_sync_run(provider="test-publisher", dataset_key="bounded")
    made = [repo.begin_export_attempt(run_id=run.run_id, scope_key="bounded",
                                      planned_sources=["c" * 64], execution_mode="native")
            for _ in range(3)]
    repo.finish_sync_run(run.run_id, status="failed")
    first = min(made, key=lambda item: item.attempt_id)
    locked, release = threading.Event(), threading.Event()

    def hold():
        with repo.engine.connect() as connection, connection.begin():
            connection.execute(text(
                "SELECT 1 FROM weather_export_attempts WHERE attempt_id = :a FOR UPDATE"
            ), {"a": first.attempt_id})
            locked.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    assert locked.wait(10)
    try:
        started = time.monotonic()
        assert retire_settled_attempts(repo, limit=1)["CONFIRMED_TERMINAL"] == 1
        assert retire_settled_attempts(repo, limit=5)["CONFIRMED_TERMINAL"] == 1
        # 잠긴 행을 기다리지 않는다(SKIP LOCKED). lock_timeout 3초보다 훨씬 짧다.
        assert time.monotonic() - started < 2
        assert _status(repo, first) == ("preparing", None)
    finally:
        release.set()
        holder.join()
    assert retire_settled_attempts(repo)["CONFIRMED_TERMINAL"] == 1
    assert {_status(repo, item) for item in made} == {("failed", "CONFIRMED_TERMINAL")}


def test_running_run_candidates_take_no_row_lock_and_probe_once_per_run(
    publisher_repository, tmp_path, monkeypatch,
):
    repo = publisher_repository
    run, made = _same_run_candidates(repo, tmp_path, monkeypatch, 5, orchestrator="dagster-run")
    calls = []

    def lookup(run_id):
        calls.append(run_id)
        return "STARTED"

    locked, release = threading.Event(), threading.Event()

    def hold():
        # 같은 run의 ingest transaction이 run 행을 잡고 있는 상태다.
        with repo.engine.connect() as connection, connection.begin():
            connection.execute(text(
                "SELECT 1 FROM weather_sync_runs WHERE run_id = :r FOR UPDATE"
            ), {"r": run.run_id})
            locked.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    assert locked.wait(10)
    try:
        started = time.monotonic()
        result = publish_pending_exports(repo, tmp_path, limit=8,
                                         dagster_status=TickStatusProbe(lookup))
        assert time.monotonic() - started < 2
    finally:
        release.set()
        holder.join()
        repo.finish_sync_run(run.run_id, status="failed")
    assert result.results == {"NOT_READY": 5} and result.reasons == {}
    assert calls == ["dagster-run"]
    assert {_status(repo, item)[0] for item in made} == {"preparing"}


def test_fresh_phase_promotes_the_most_recently_finished_run_first(
    publisher_repository, tmp_path, monkeypatch,
):
    repo = publisher_repository
    heads, older, *_ = candidate(repo, tmp_path, monkeypatch)
    _, newer, *_ = candidate(repo, tmp_path, monkeypatch)
    result = publish_fresh_exports(repo, tmp_path, budget_seconds=30, max_candidates=1)
    assert result.results == {"PUBLISHED": 1}
    assert state(repo, heads, newer)[0]["published_at"] is not None
    assert state(repo, heads, older)[0]["published_at"] is None
    assert publish_fresh_exports(repo, tmp_path, budget_seconds=30).results == {"PUBLISHED": 1}


def test_fresh_phase_only_picks_untried_current_owners_of_successful_runs(
    publisher_repository, tmp_path, monkeypatch,
):
    from test_export_heads import _descriptor

    repo = publisher_repository
    descriptor = _descriptor(repo)
    heads, superseded, *_ = candidate(repo, tmp_path, monkeypatch, descriptor=descriptor)
    _, latest, *_ = candidate(repo, tmp_path, monkeypatch, descriptor=descriptor)
    _, sealed, *_ = candidate(repo, tmp_path, monkeypatch)
    with repo.engine.begin() as connection:
        connection.execute(heads.lifecycle.attempts.update().where(
            heads.lifecycle.attempts.c.attempt_id == sealed.attempt_id,
        ).values(status="sealed"))
    run, running = _same_run_candidates(repo, tmp_path, monkeypatch, 1)
    try:
        seen = []
        original = export_collector._process_candidate

        def spy(heads_, artifacts, row, dagster_status, **kwargs):
            seen.append(row.attempt_id)
            assert kwargs == {"current_owner": True}
            return original(heads_, artifacts, row, dagster_status, **kwargs)

        monkeypatch.setattr(export_collector, "_process_candidate", spy)
        publish_fresh_exports(repo, tmp_path, budget_seconds=30)
        assert seen == [latest.attempt_id]
    finally:
        repo.finish_sync_run(run.run_id, status="failed")
    assert superseded and running


def test_fresh_shortcut_rechecks_ownership_inside_promotion(
    publisher_repository, tmp_path, monkeypatch,
):
    """fresh는 퇴역 확인 transaction을 건너뛴다. 조회 뒤 소유권이 넘어가면 승격 안의
    소유권 확인이 거절하고(NOT_READY), 다음 일괄 퇴역이 SUPERSEDED로 정리한다."""
    from test_export_heads import _descriptor

    repo = publisher_repository
    descriptor = _descriptor(repo)
    heads, raced, *_ = candidate(repo, tmp_path, monkeypatch, descriptor=descriptor)
    original = ExportHeads.promote

    def promote(self, identity, artifacts, **kwargs):
        run = repo.start_sync_run(provider="python-kma-api",
                                  dataset_key="kma_ultra_short_nowcast")
        newer = repo.begin_export_attempt(
            run_id=run.run_id, scope_key="newer", planned_sources=[descriptor.source_scope],
            execution_mode="native",
        )
        heads.lifecycle.claim(newer)
        repo.finish_sync_run(run.run_id, status="failed")
        return original(self, identity, artifacts, **kwargs)

    monkeypatch.setattr(ExportHeads, "promote", promote)
    assert publish_fresh_exports(repo, tmp_path, budget_seconds=30).results == {"NOT_READY": 1}
    monkeypatch.setattr(ExportHeads, "promote", original)
    assert state(repo, heads, raced)[0]["published_at"] is None
    assert retire_settled_attempts(repo)["SUPERSEDED"] == 1
    assert _status(repo, raced) == ("failed", "SUPERSEDED")


def test_unexpected_error_in_one_candidate_is_isolated(
    publisher_repository, tmp_path, monkeypatch,
):
    repo = publisher_repository
    heads, broken, *_ = candidate(repo, tmp_path, monkeypatch)
    _, healthy, *_ = candidate(repo, tmp_path, monkeypatch)
    original = ExportHeads.promote

    def promote(self, identity, artifacts, **kwargs):
        if identity.attempt_id == broken.attempt_id:
            raise TypeError("private detail")
        return original(self, identity, artifacts, **kwargs)

    monkeypatch.setattr(ExportHeads, "promote", promote)
    result = publish_pending_exports(repo, tmp_path, limit=4)
    assert result.results == {"DEFERRED": 1, "PUBLISHED": 1}
    assert result.reasons == {"TypeError": 1}
    assert state(repo, heads, healthy)[0]["published_at"] is not None
    assert state(repo, heads, broken)[0]["status"] == "preparing"


def test_systemic_db_errors_stop_the_phase_after_three_in_a_row(
    publisher_repository, tmp_path, monkeypatch,
):
    from sqlalchemy.exc import OperationalError

    repo = publisher_repository
    run = repo.start_sync_run(provider="test-publisher", dataset_key="systemic")
    for _ in range(6):
        repo.begin_export_attempt(run_id=run.run_id, scope_key="s",
                                  planned_sources=["d" * 64], execution_mode="native")
    repo.finish_sync_run(run.run_id, status="success")

    class Disconnect(Exception):
        sqlstate = "08006"

    def broken(*args, **kwargs):
        raise OperationalError("SELECT", {}, Disconnect())

    monkeypatch.setattr(export_collector, "_publisher_terminal", broken)
    result = publish_pending_exports(repo, tmp_path, limit=8)
    assert result.results == {"DEFERRED": 3} and result.has_more
    assert result.reasons == {"sqlstate_08006": 3}


def test_probe_is_per_tick_and_falls_back_when_the_batch_fails():
    calls = []

    def one(run_id):
        calls.append(("one", run_id))
        return "SUCCESS"

    def broken_batch(run_ids):
        calls.append(("batch", tuple(run_ids)))
        raise OSError("control plane unavailable")

    probe = TickStatusProbe(one, broken_batch)
    probe.prefetch(["a", "a", "b", None])
    assert export_collector._probe(probe, "a") == "SUCCESS"
    assert export_collector._probe(probe, "a") == "SUCCESS"
    assert calls == [("batch", ("a", "b")), ("one", "a")]
    fresh = TickStatusProbe(one, lambda ids: {"b": "FAILURE"})
    fresh.prefetch(["b", "c"])
    assert fresh.cached("b") == (True, "FAILURE") and fresh.cached("c") == (True, None)


def test_promotion_hashes_each_chunk_once_and_parses_each_point_once(
    publisher_repository, tmp_path, monkeypatch,
):
    """reconcile은 hash·크기·줄 수만 확인하고 승격 단계는 같은 guard 안에서 hash를
    다시 계산하지 않는다(운영 A55에서 예보 1건당 검증 CPU의 대부분)."""
    repo = publisher_repository
    heads, identity, capture, *_ = candidate(repo, tmp_path, monkeypatch)
    with repo.engine.connect() as connection:
        chunk = connection.execute(select(heads.lifecycle.chunks)).mappings().one()
    counts = {"sha": 0, "loads": 0}

    def sha256(data=b""):
        counts["sha"] += 1
        return hashlib.sha256(data)

    def loads(line):
        counts["loads"] += 1
        return json.loads(line)

    monkeypatch.setattr(export_artifacts, "hashlib", SimpleNamespace(sha256=sha256))
    monkeypatch.setattr(export_artifacts, "json", SimpleNamespace(loads=loads, dumps=json.dumps))
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    assert counts == {"sha": 1, "loads": chunk["point_count"]}
    # guard 밖 read는 매번 hash를 확인한다.
    list(capture.artifacts.read(chunk["artifact_digest"], byte_count=chunk["byte_count"],
                                point_count=chunk["point_count"]))
    assert counts["sha"] == 2


def test_corrupt_artifact_is_still_rejected_inside_the_guard(
    publisher_repository, tmp_path, monkeypatch,
):
    repo = publisher_repository
    heads, identity, capture, *_ = candidate(repo, tmp_path, monkeypatch)
    path = next(tmp_path.glob("*.jsonl"))
    payload = bytearray(path.read_bytes())
    payload[5] = ord("X") if payload[5] != ord("X") else ord("Y")
    path.chmod(0o600)
    path.write_bytes(bytes(payload))
    assert publish_pending_exports(repo, tmp_path).results == {"NOT_READY": 1}
    assert state(repo, heads, identity)[0]["status"] == "preparing"


def test_promotion_burst_yields_the_artifact_guard_to_readers_and_writers(
    publisher_repository, tmp_path, monkeypatch,
):
    """승격은 배타 guard를 잡은 채 여러 transaction을 이어 간다. 연속 승격 사이에 guard를
    놓고 쉬지 않으면 10ms마다 다시 시도하는 API reader(2초)와 collector 쓰기(5초)가 상한까지
    들어오지 못한다(검토 H1). 승격 하나가 guard를 잡는 시간을 늘려 연속 승격이 2초를 넘게
    하고, 그동안 다른 thread의 shared 읽기와 publish()가 모두 성공해야 한다."""
    from kortravelweather.export_artifacts import (
        ExportArtifactError,
        ExportArtifactStore,
        prepare_export_chunk,
    )

    repo = publisher_repository
    count = 15
    for _ in range(count):
        candidate(repo, tmp_path, monkeypatch)
    original_lines = ExportArtifactStore._lines

    def slow_lines(self, *args, **kwargs):
        time.sleep(0.08)  # 운영 A55의 검증·digest 한 번에 해당하는 시간 동안 guard를 잡는다.
        return original_lines(self, *args, **kwargs)

    monkeypatch.setattr(ExportArtifactStore, "_lines", slow_lines)
    store = ExportArtifactStore(tmp_path)
    chunk = prepare_export_chunk([{"probe": "contention"}], source_collected_at=datetime.now(UTC))
    stop, failures, done = threading.Event(), [], {"read": 0, "write": 0}

    def contender():
        while not stop.is_set():
            try:
                with store.guard(timeout_seconds=2, shared=True):
                    done["read"] += 1
                store.publish(chunk)
                done["write"] += 1
            except ExportArtifactError as exc:
                failures.append(str(exc))
            time.sleep(0.005)

    thread = threading.Thread(target=contender)
    started = time.monotonic()
    thread.start()
    try:
        result = publish_fresh_exports(repo, tmp_path, budget_seconds=60)
    finally:
        stop.set()
        thread.join(15)
    assert time.monotonic() - started > 2.4  # 연속 승격이 reader 상한(2초)보다 길었다.
    assert result.results == {"PUBLISHED": count}
    assert failures == []
    assert done["read"] >= count // 2 and done["write"] >= count // 2


@pytest.mark.parametrize("epoch", [None, "abc", 1.5])
def test_malformed_claimed_epoch_is_a_violation_not_supersession(publisher_repository, epoch):
    """owner_epochs 값이 정수가 아니면 비교가 NULL이 된다. 그것을 위반으로 세야 소유권이
    실제로 넘어갔는지 모르는 시도를 SUPERSEDED로 퇴역하거나 현재 소유자로 보지 않는다."""
    repo = publisher_repository
    heads = ExportHeads(repo)
    scope = "e" * 64
    run = repo.start_sync_run(provider="test-publisher", dataset_key="epoch-shape")
    old = repo.begin_export_attempt(run_id=run.run_id, scope_key="old",
                                    planned_sources=[scope], execution_mode="native")
    heads.lifecycle.claim(old)
    repo.finish_sync_run(run.run_id, status="success")
    newer_run = repo.start_sync_run(provider="test-publisher", dataset_key="epoch-newer")
    newer = repo.begin_export_attempt(run_id=newer_run.run_id, scope_key="newer",
                                      planned_sources=[scope], execution_mode="native")
    heads.lifecycle.claim(newer)
    try:
        with repo.engine.begin() as connection:
            connection.execute(heads.lifecycle.attempts.update().where(
                heads.lifecycle.attempts.c.attempt_id == old.attempt_id,
            ).values(owner_epochs={scope: epoch}))
        assert retire_settled_attempts(repo)["SUPERSEDED"] == 0
        assert _status(repo, old) == ("preparing", None)
        with repo.engine.connect() as connection:
            assert connection.scalar(
                select(heads.lifecycle.attempts.c.attempt_id).where(
                    heads.lifecycle.attempts.c.attempt_id == old.attempt_id,
                    export_collector._CURRENT_OWNERSHIP,
                )
            ) is None
    finally:
        repo.finish_sync_run(newer_run.run_id, status="failed")


def test_daily_prune_waits_for_the_hard_tick_deadline(publisher_repository, tmp_path,
                                                      monkeypatch):
    from kortravelweather import export_daily_store
    from kortravelweather.export_collector import refresh_publisher_daily

    calls = []
    monkeypatch.setattr(export_daily_store, "prune_daily_summaries",
                        lambda *a, **k: calls.append("prune") or 0)
    now = time.monotonic()
    refresh_publisher_daily(publisher_repository, tmp_path, deadline=now + 5,
                            hard_deadline=now + 1)
    assert calls == []
    refresh_publisher_daily(publisher_repository, tmp_path, deadline=time.monotonic() + 5,
                            hard_deadline=time.monotonic() + 30)
    assert calls == ["prune"]


def test_publisher_transactions_wait_briefly_for_row_locks(publisher_repository):
    heads = export_collector._publisher_heads(publisher_repository)
    with heads._transaction() as session:
        assert session.scalar(text("SHOW lock_timeout")) == (
            f"{export_collector.PUBLISHER_LOCK_TIMEOUT_MS}ms"
        )
        assert session.scalar(text("SHOW statement_timeout")) == "5s"
