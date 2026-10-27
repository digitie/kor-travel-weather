"""artifact GC는 공개·대기·frozen·inventory·일별 미처리 참조를 절대 지우지 않는다."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import test_export_publisher_recovery as fixtures
from sqlalchemy import select

from kortravelweather import export_gc
from kortravelweather.export_artifacts import ExportArtifactStore
from kortravelweather.export_collector import retire_settled_attempts
from kortravelweather.export_daily_store import daily_summary_tables
from kortravelweather.export_gc import collect_export_artifacts, live_artifact_digests
from kortravelweather.export_heads import ExportHeads
from kortravelweather.export_inventory import export_inventory_table
from kortravelweather.export_snapshots import export_snapshot_tables

publisher_repository = fixtures.publisher_repository
clear_exports = fixtures.clear_exports

HOUR = timedelta(hours=1)


def _publish(repo, root, descriptor, *, hours_ago, value):
    from test_export_heads import _candidate

    revision = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - hours_ago * HOUR
    heads, identity, capture, *_ = _candidate(repo, root, descriptor, revision=revision,
                                              value=value)
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    return identity, _generation(repo, identity)


def _generation(repo, identity):
    heads = ExportHeads(repo)
    with repo.engine.connect() as connection:
        return connection.scalar(select(heads.generations.c.generation_id).where(
            heads.generations.c.attempt_id == identity.attempt_id,
        ))


def _digests(repo, *, generation=None, attempt=None):
    heads = ExportHeads(repo)
    with repo.engine.connect() as connection:
        if generation is not None:
            return set(connection.scalars(select(heads.refs.c.artifact_digest).where(
                heads.refs.c.generation_id == generation,
            )))
        return set(connection.scalars(select(heads.lifecycle.chunks.c.artifact_digest).where(
            heads.lifecycle.chunks.c.attempt_id == attempt.attempt_id,
        )))


def _snapshot(repo, generation, *, expires_at):
    snapshots, refs = export_snapshot_tables(ExportHeads(repo).metadata)
    snapshot_id, now = str(uuid.uuid4()), datetime.now(UTC)
    with repo.engine.begin() as connection:
        connection.execute(snapshots.insert().values(
            snapshot_id=snapshot_id, manifest_digest="0" * 64, manifest={"scopes": []},
            data_revision_at=now, created_at=now, expires_at=expires_at,
        ))
        connection.execute(refs.insert().values(
            snapshot_id=snapshot_id, ordinal=0, generation_id=generation,
        ))


def _checkpoint(repo, generation):
    heads = ExportHeads(repo)
    _, progress = daily_summary_tables(heads.metadata)
    with repo.engine.begin() as connection:
        created = connection.scalar(select(heads.generations.c.created_at).where(
            heads.generations.c.generation_id == generation,
        ))
        connection.execute(progress.delete())
        connection.execute(progress.insert().values(
            progress_id=1, published_at=created, generation_id=generation,
            updated_at=datetime.now(UTC),
        ))


def _present(root, digests):
    return {digest for digest in digests if (root / f"{digest}.jsonl").exists()}


def _scenario(repo, root, monkeypatch):
    """모든 참조 종류를 하나씩 만든다. 반환: (지켜야 할 digest, 지워도 되는 digest)."""
    from test_export_heads import _candidate, _descriptor

    current = _descriptor(repo)
    _, frozen_generation = _publish(repo, root, current, hours_ago=4, value="1")
    _, head_generation = _publish(repo, root, current, hours_ago=3, value="2")
    stale = _descriptor(repo)
    _, dropped_generation = _publish(repo, root, stale, hours_ago=4, value="3")
    _, stale_head = _publish(repo, root, stale, hours_ago=3, value="4")
    inventory_scope = _descriptor(repo)
    _, inventory_generation = _publish(repo, root, inventory_scope, hours_ago=4, value="5")
    _, inventory_head = _publish(repo, root, inventory_scope, hours_ago=3, value="6")
    inventory = export_inventory_table(ExportHeads(repo).metadata)
    with repo.engine.begin() as connection:
        # 선택 밖 위치의 이전 승인처럼 inventory가 head보다 오래된 generation을 가리킨다.
        connection.execute(inventory.update().where(
            inventory.c.location_id.in_(inventory_scope.location_ids),
        ).values(generation_id=inventory_generation))
    # 이전 generation 하나는 오래 전에 만료된 snapshot만, 하나는 아직 유효한 frozen
    # snapshot이 가리킨다. 최근 snapshot 규칙은 끄고 유효기간 규칙만 본다.
    _snapshot(repo, dropped_generation, expires_at=datetime.now(UTC) - HOUR)
    _snapshot(repo, frozen_generation, expires_at=datetime.now(UTC) + 30 * HOUR)
    monkeypatch.setattr(export_gc, "LATEST_SNAPSHOTS", 0)
    _checkpoint(repo, inventory_head)
    unread_scope = _descriptor(repo)
    _, unread_generation = _publish(repo, root, unread_scope, hours_ago=4, value="7")
    _, unread_head = _publish(repo, root, unread_scope, hours_ago=3, value="8")
    raced = _descriptor(repo)
    superseded = _candidate(repo, root, raced, value="9")[1]
    pending = _candidate(repo, root, raced, value="10")[1]
    assert retire_settled_attempts(repo)["SUPERSEDED"] == 1
    pinned = "f" * 64
    (root / f"{pinned}.jsonl").write_bytes(b"{}\n")
    heads = ExportHeads(repo)
    with repo.engine.begin() as connection:
        connection.execute(heads.pins.insert().values(
            attempt_id=pending.attempt_id, chunk_key="p" * 64, artifact_digest=pinned,
            created_at=datetime.now(UTC),
        ))
    keep = (
        _digests(repo, generation=head_generation)
        | _digests(repo, generation=frozen_generation)
        | _digests(repo, generation=stale_head)
        | _digests(repo, generation=inventory_generation)
        | _digests(repo, generation=inventory_head)
        | _digests(repo, generation=unread_generation)
        | _digests(repo, generation=unread_head)
        | _digests(repo, attempt=pending)
        | {pinned}
    )
    drop = _digests(repo, generation=dropped_generation) | _digests(repo, attempt=superseded)
    assert keep and drop and not keep & drop
    return keep, drop


def _age_everything(root, hours):
    past = (datetime.now(UTC) - hours * HOUR).timestamp()
    for path in root.iterdir():
        os.utime(path, (past, past), follow_symlinks=False)


def test_gc_never_deletes_served_pending_inventory_or_pinned_artifacts(
    publisher_repository, tmp_path, monkeypatch,
):
    repo = publisher_repository
    keep, drop = _scenario(repo, tmp_path, monkeypatch)
    _age_everything(tmp_path, 100)
    # 보존 창(1시간)·유예(5분)를 가장 짧게 두고 시계도 6시간 앞당겨 모든 generation을
    # 보존 창 밖으로 보낸다. 그래도 참조가 있는 파일은 남아야 한다.
    report = collect_export_artifacts(
        repo, tmp_path, mode="delete", deadline=float("inf"),
        retention=HOUR, grace=timedelta(minutes=5), now=datetime.now(UTC) + 6 * HOUR,
    )
    assert _present(tmp_path, keep) == keep
    assert _present(tmp_path, drop) == set()
    assert report["deleted"] == len(drop) and report["complete"]
    assert report["deleted_bytes"] > 0
    store = ExportArtifactStore(tmp_path)
    heads = ExportHeads(repo)
    with repo.engine.connect() as connection:
        served = connection.execute(
            select(heads.refs).where(heads.refs.c.generation_id.in_(
                select(heads.heads.c.generation_id)
            ))
        ).mappings().all()
    for ref in served:
        # 현재 head는 hash까지 검증해 그대로 읽힌다.
        assert list(store.read(ref["artifact_digest"], byte_count=ref["byte_count"],
                               point_count=ref["point_count"]))


def test_gc_dry_run_reports_reclaimable_bytes_without_deleting(
    publisher_repository, tmp_path, monkeypatch,
):
    repo = publisher_repository
    keep, drop = _scenario(repo, tmp_path, monkeypatch)
    _age_everything(tmp_path, 100)
    report = collect_export_artifacts(
        repo, tmp_path, mode="dry_run", deadline=float("inf"),
        retention=HOUR, grace=timedelta(minutes=5), now=datetime.now(UTC) + 6 * HOUR,
    )
    assert report["eligible"] == len(drop) and report["eligible_bytes"] > 0
    assert report["deleted"] == 0
    assert _present(tmp_path, keep | drop) == keep | drop


def test_gc_respects_grace_rewrites_and_lock_files(publisher_repository, tmp_path, monkeypatch):
    from contextlib import contextmanager

    repo = publisher_repository
    store = ExportArtifactStore(tmp_path)
    with store.guard():
        pass  # lock 파일을 만든다.
    old, fresh, raced = "a" * 64, "b" * 64, "c" * 64
    for digest in (old, fresh, raced):
        (tmp_path / f"{digest}.jsonl").write_bytes(b"{}\n")
    (tmp_path / ".candidate-abc123").write_bytes(b"partial")
    for name in (f"{old}.jsonl", f"{raced}.jsonl", ".candidate-abc123"):
        past = (datetime.now(UTC) - 10 * HOUR).timestamp()
        os.utime(tmp_path / name, (past, past))
    original = ExportArtifactStore.guard

    @contextmanager
    def rewrite_then_guard(self, **kwargs):
        # 대상 목록을 만든 뒤, GC가 배타 guard를 얻기 전에 collector가 같은 digest를
        # 다시 썼다(새 mtime). guard 안의 재확인이 그 파일을 지키는지 본다.
        os.utime(tmp_path / f"{raced}.jsonl", None)
        with original(self, **kwargs):
            yield

    monkeypatch.setattr(ExportArtifactStore, "guard", rewrite_then_guard)
    report = collect_export_artifacts(repo, tmp_path, deadline=float("inf"))
    assert not (tmp_path / f"{old}.jsonl").exists()
    assert not (tmp_path / ".candidate-abc123").exists()
    assert (tmp_path / f"{fresh}.jsonl").exists() and (tmp_path / f"{raced}.jsonl").exists()
    assert (tmp_path / ".export.lock").exists() and (tmp_path / ".export-admission.lock").exists()
    assert report["deleted"] == 2


def test_live_set_without_daily_progress_keeps_every_generation(
    publisher_repository, tmp_path,
):
    from test_export_heads import _descriptor

    repo = publisher_repository
    descriptor = _descriptor(repo)
    _, first = _publish(repo, tmp_path, descriptor, hours_ago=4, value="1")
    _, second = _publish(repo, tmp_path, descriptor, hours_ago=3, value="2")
    heads = ExportHeads(repo)
    _, progress = daily_summary_tables(heads.metadata)
    with repo.engine.begin() as connection:
        connection.execute(progress.delete())
    live = live_artifact_digests(repo, now=datetime.now(UTC) + 72 * HOUR, retention=HOUR)
    assert _digests(repo, generation=first) | _digests(repo, generation=second) <= live


def test_latest_snapshots_keep_an_expired_fallback(publisher_repository, tmp_path):
    from test_export_heads import _descriptor

    repo = publisher_repository
    descriptor = _descriptor(repo)
    _, fallback = _publish(repo, tmp_path, descriptor, hours_ago=4, value="1")
    _, head = _publish(repo, tmp_path, descriptor, hours_ago=3, value="2")
    _checkpoint(repo, head)
    # 선택별 이전 승인 fallback은 만료된 최근 snapshot을 다시 쓴다.
    _snapshot(repo, fallback, expires_at=datetime.now(UTC) - 10 * HOUR)
    live = live_artifact_digests(repo, now=datetime.now(UTC) + 6 * HOUR, retention=HOUR)
    assert _digests(repo, generation=fallback) <= live


@pytest.mark.parametrize("mode,retention,grace", [
    ("delete-all", timedelta(hours=48), timedelta(hours=1)),
    ("delete", timedelta(minutes=30), timedelta(hours=1)),
    ("delete", timedelta(hours=48), timedelta(minutes=1)),
])
def test_gc_rejects_unsafe_arguments(publisher_repository, tmp_path, mode, retention, grace):
    with pytest.raises(ValueError):
        collect_export_artifacts(publisher_repository, tmp_path, mode=mode,
                                 deadline=float("inf"), retention=retention, grace=grace)
