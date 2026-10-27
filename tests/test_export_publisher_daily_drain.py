"""일별 파생 queue도 별도 예산 안에서 관측/예보 checkpoint를 유한하게 따라간다."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import test_export_publisher_recovery as fixtures
from sqlalchemy import select

from kortravelweather import export_collector as publisher
from kortravelweather.export_artifacts import ExportArtifactStore, prepare_export_chunk
from kortravelweather.export_daily import KST
from kortravelweather.export_daily_store import daily_coverage_table, daily_summary_tables
from kortravelweather.export_heads import ExportHeads, ScopeDescriptor
from kortravelweather.models import WeatherLocation

publisher_repository = fixtures.publisher_repository
clear_exports = fixtures.clear_exports


def seed_mixed_approved_metadata(repository, root, *, locations=300):
    """합성 승인 metadata+실제 artifact. 원천 권위/운영 처리율 시험은 아니다."""
    heads, store = ExportHeads(repository), ExportArtifactStore(root)
    clock = datetime.now(UTC) - timedelta(minutes=1)
    target = (datetime.now(KST) - timedelta(days=1)).replace(
        hour=6, minute=0, second=0, microsecond=0,
    )
    run = repository.start_sync_run(provider="test-daily-drain", dataset_key="synthetic")
    repository.finish_sync_run(run.run_id, status="success")
    attempts, plans, generations, refs = [], [], [], []
    for i in range(locations):
        location = f"daily-drain-{i:04}"
        repository.create_location(WeatherLocation(
            location_id=location, name=location, latitude=37.5, longitude=127, nx=60, ny=127,
        ))
        for forecast in (False, True):
            attempt, owner, generation = (str(uuid.uuid4()) for _ in range(3))
            descriptor = ScopeDescriptor(
                entity_type="weather_response", entity_id=location,
                forecast_style="ultra_short" if forecast else "nowcast",
                dataset_key="kma_ultra_short_forecast" if forecast else "kma_ultra_short_nowcast",
                nx=60, ny=127, location_ids=(location,), normalizer_revision="kma-v1",
                winner_policy_revision="kma-base-v1",
                coverage_contract="kma-grid-forecast-v1" if forecast else "kma-grid-nowcast-v1",
            )
            points = [dict(
                location_id=location, provider=descriptor.provider,
                dataset_key=descriptor.dataset_key, forecast_style=descriptor.forecast_style,
                target_at=target.isoformat(), known_at=clock.isoformat(), metric_key=metric,
                unit=unit, value_number=value,
            ) for metric, unit, value in [("T1H", "deg_c", 99 if forecast else 10),
                                         ("RN1", "mm", 99 if forecast else 1)]]
            artifact = prepare_export_chunk(points, source_collected_at=clock)
            store.publish(artifact)
            published = clock + timedelta(microseconds=2 * i + int(forecast))
            attempts.append(dict(
                attempt_id=attempt, owner_id=owner, run_id=run.run_id, scope_key="synthetic",
                execution_mode="native", planned_sources=[descriptor.source_scope],
                plan_digest="0" * 64, status="sealed", created_at=clock, published_at=published,
            ))
            plans.append(dict(
                attempt_id=attempt, source_scope=descriptor.source_scope,
                namespace=descriptor.namespace, descriptor=descriptor.model_dump(mode="json"),
                bound_at=clock,
            ))
            generations.append(dict(
                generation_id=generation, attempt_id=attempt, owner_id=owner,
                seal_digest="0" * 64, namespace=descriptor.namespace,
                data_revision_at=target, source_collected_at=clock,
                content_digest=artifact.digest, point_count=2, created_at=published,
            ))
            refs.append(dict(
                generation_id=generation, ordinal=0, artifact_digest=artifact.digest,
                byte_count=len(artifact.payload), point_count=2, location_ids=[location],
            ))
    with repository.engine.begin() as connection:
        connection.execute(heads.lifecycle.attempts.insert(), attempts)
        connection.execute(heads.plans.insert(), plans)
        connection.execute(heads.generations.insert(), generations)
        connection.execute(heads.refs.insert(), refs)
    return heads, generations, refs


def checkpoint(repository):
    from kortravelweather.repository import Base

    _, progress = daily_summary_tables(Base.metadata)
    with repository.engine.connect() as connection:
        row = connection.execute(select(progress)).mappings().one_or_none()
    return dict(row) if row else None


def test_three_hundred_observed_and_forecast_generations_catch_up_without_lost_coverage(
    publisher_repository, tmp_path, monkeypatch,
):
    from kortravelweather.repository import Base

    repo = publisher_repository
    _, generations, _ = seed_mixed_approved_metadata(repo, tmp_path)
    # 총량/선택/원자적 checkpoint의 용량 검증이다. 고정 clock은 ARM 속도 증거가 아니다.
    monkeypatch.setattr(publisher, "time", SimpleNamespace(monotonic=lambda: 0))
    results = [publisher.refresh_publisher_daily(repo, tmp_path, deadline=4) for _ in range(3)]
    assert [r["processed"] for r in results] == [256, 256, 88]
    assert results[-1]["has_more"] is False
    assert publisher.refresh_publisher_daily(repo, tmp_path, deadline=4)["processed"] == 0
    final = checkpoint(repo)
    assert (final["published_at"], final["generation_id"]) == (
        generations[-1]["created_at"], generations[-1]["generation_id"],
    )
    table, _ = daily_summary_tables(Base.metadata)
    with repo.engine.connect() as connection:
        coverage = connection.execute(select(daily_coverage_table(Base.metadata))).mappings().all()
        summaries = connection.execute(select(table.c.summary)).scalars().all()
    assert len(coverage) == len(summaries) == 300
    assert all(row["provider"] == "python-kma-api" for row in coverage)
    # 구간6시간 중 RN1 1시간만 있으므로 합계는 미확정이고 강수 발생만 확정이다.
    assert all(row["temp_avg"] == 10 and row["precip_sum_mm"] is None
               and row["precip_occurred"] is True for row in summaries)


def test_daily_missing_artifact_stops_without_advancing_checkpoint_and_repairs(
    publisher_repository, tmp_path, monkeypatch,
):
    repo = publisher_repository
    _, generations, refs = seed_mixed_approved_metadata(repo, tmp_path, locations=3)
    broken = tmp_path / (refs[0]["artifact_digest"] + ".jsonl")
    saved = broken.read_bytes()
    broken.unlink()
    monkeypatch.setattr(publisher, "time", SimpleNamespace(monotonic=lambda: 0))
    assert publisher.refresh_publisher_daily(repo, tmp_path, deadline=4) == {
        "processed": 0, "has_more": True,
    }
    assert checkpoint(repo) is None
    broken.write_bytes(saved)
    assert publisher.refresh_publisher_daily(repo, tmp_path, deadline=4)["processed"] == 6
    assert checkpoint(repo)["generation_id"] == generations[-1]["generation_id"]


@pytest.mark.parametrize("result", [
    {"processed": 0, "has_more": True, "race": True},
    {"processed": 0, "has_more": False},
])
def test_daily_race_or_finished_checkpoint_does_not_spin(publisher_repository, tmp_path,
                                                      monkeypatch, result):
    from kortravelweather import export_daily_store

    calls, prunes = [], []
    monkeypatch.setattr(publisher, "time", SimpleNamespace(monotonic=lambda: 0))
    monkeypatch.setattr(export_daily_store, "refresh_daily_summaries",
                        lambda *args, **kwargs: calls.append(kwargs) or result)
    monkeypatch.setattr(export_daily_store, "prune_daily_summaries",
                        lambda *args, **kwargs: prunes.append(1))
    assert publisher.refresh_publisher_daily(publisher_repository, tmp_path, deadline=4) == {
        "processed": 0, "has_more": result["has_more"],
    }
    assert calls == [{"limit": 4}] and prunes == [1]


def test_daily_deadline_stops_before_next_batch(publisher_repository, tmp_path, monkeypatch):
    from kortravelweather import export_daily_store

    moments, calls = iter([0, 4, 4]), []
    monkeypatch.setattr(publisher, "time", SimpleNamespace(monotonic=lambda: next(moments)))
    monkeypatch.setattr(export_daily_store, "refresh_daily_summaries", lambda *args, **kwargs: (
        calls.append(kwargs) or {"processed": 4, "has_more": True}
    ))
    assert publisher.refresh_publisher_daily(publisher_repository, tmp_path, deadline=4) == {
        "processed": 4, "has_more": True,
    }
    assert calls == [{"limit": 4}]


def test_publisher_tick_prunes_only_long_expired_snapshot_handles(
    publisher_repository, tmp_path, monkeypatch,
):
    """만료 뒤 6시간이 지난 handle과 refs만 지운다. 유효 cursor와 최근 만료 handle은 남는다."""
    from kortravelweather.export_locations import FrozenLocationExports
    from kortravelweather.export_snapshots import export_snapshot_tables, prune_export_snapshots

    repo = publisher_repository
    _, generations, _ = seed_mixed_approved_metadata(repo, tmp_path, locations=2)
    reader = FrozenLocationExports(repo, ExportArtifactStore(tmp_path), signing_key="prune-test")
    live, _ = reader.page("locations", limit=1, cursor=None)
    assert live["meta"]["has_more"]
    snapshots, refs = export_snapshot_tables(ExportHeads(repo).metadata)
    now = datetime.now(UTC)

    def expired_handle(hours):
        key = str(uuid.uuid4())
        with repo.engine.begin() as connection:
            connection.execute(snapshots.insert().values(
                snapshot_id=key, manifest_digest="0" * 64,
                manifest={"selection": {"kind": "prune-fixture"}, "scopes": []},
                source_collected_at=now, data_revision_at=now,
                created_at=now - timedelta(hours=hours, minutes=15),
                expires_at=now - timedelta(hours=hours),
            ))
            connection.execute(refs.insert(), [
                {"snapshot_id": key, "ordinal": index, "generation_id": row["generation_id"]}
                for index, row in enumerate(generations[:2])
            ])
        return key

    oldest, older, recent = expired_handle(8), expired_handle(7), expired_handle(1)

    def remaining():
        with repo.engine.connect() as connection:
            return (set(connection.scalars(select(snapshots.c.snapshot_id))),
                    set(connection.scalars(select(refs.c.snapshot_id))))

    # 상한을 지키고 가장 오래 만료된 handle부터 지운다.
    assert prune_export_snapshots(repo, limit=1) == 1
    handles, cited = remaining()
    assert oldest not in handles and oldest not in cited and older in handles
    monkeypatch.setattr(publisher, "time", SimpleNamespace(monotonic=lambda: 0))
    publisher.refresh_publisher_daily(repo, tmp_path, deadline=4)
    handles, cited = remaining()
    assert older not in handles and older not in cited
    assert {recent, live["meta"]["snapshot_id"]} <= handles and cited == {recent}
    # 아직 유효한 cursor는 그대로 다음 페이지를 읽는다.
    following, _ = reader.page("locations", limit=1, cursor=live["meta"]["next_cursor"])
    assert following["items"]


def test_snapshot_prune_backs_off_after_a_timeout_and_recovers(monkeypatch):
    """statement 상한에 걸린 개수를 다음 tick에 그대로 반복하지 않고 절반으로 줄인다."""
    from sqlalchemy.exc import OperationalError

    from kortravelweather import export_snapshots

    limits, budget = [], [5]
    monkeypatch.setattr(publisher, "_SNAPSHOT_PRUNE_NEXT_LIMIT", [None])

    def prune(repository, *, limit):
        limits.append(limit)
        if limit > budget[0]:
            raise OperationalError("DELETE", {}, Exception("canceling statement due to timeout"))
        return limit

    monkeypatch.setattr(export_snapshots, "prune_export_snapshots", prune)
    for _ in range(4):
        publisher._prune_snapshot_handles(object())
    assert limits == [20, 10, 5, 10]
    budget[0] = 100
    for _ in range(2):
        publisher._prune_snapshot_handles(object())
    assert limits[-2:] == [5, 10]
    # 한 handle도 상한 안에 못 지우면 1에서 멈춘다(0이나 음수로 가지 않는다).
    budget[0] = 0
    for _ in range(6):
        publisher._prune_snapshot_handles(object())
    assert limits[-1] == 1 and min(limits) == 1


def test_snapshot_prune_does_not_depend_on_daily_budget_or_daily_prune(
    tmp_path, monkeypatch,
):
    """일별 backlog로 drain 예산이 끝났거나 일별 정리가 실패해도 tick 상한 안이면 정리한다."""
    from kortravelweather import export_daily_store, export_snapshots

    pruned = []
    monkeypatch.setattr(publisher, "_SNAPSHOT_PRUNE_NEXT_LIMIT", [None])
    monkeypatch.setattr(export_snapshots, "prune_export_snapshots",
                        lambda repository, *, limit: pruned.append(limit) or 0)
    monkeypatch.setattr(export_daily_store, "refresh_daily_summaries", lambda *a, **k: {
        "processed": 4, "has_more": True,
    })

    def broken(*args, **kwargs):
        raise RuntimeError("daily prune failed")

    monkeypatch.setattr(export_daily_store, "prune_daily_summaries", broken)
    monkeypatch.setattr(publisher, "time", SimpleNamespace(monotonic=lambda: 10))
    # drain 예산(deadline)이 이미 지났다.
    assert publisher.refresh_publisher_daily(object(), tmp_path, deadline=5, hard_deadline=30)[
        "has_more"
    ]
    # drain 예산은 남았지만 일별 정리가 실패했다.
    publisher.refresh_publisher_daily(object(), tmp_path, deadline=20, hard_deadline=30)
    assert pruned == [20, 20]
    # tick 상한까지 한 문장의 최악 시간이 남지 않으면 미룬다.
    publisher.refresh_publisher_daily(object(), tmp_path, deadline=5, hard_deadline=12)
    assert pruned == [20, 20]
