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
    assert all(row["temp_avg"] == 10 and row["precip_sum_mm"] == 1 for row in summaries)


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
