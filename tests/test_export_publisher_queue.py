"""pending 인덱스와 기존 성공 publication 시각의 migration 정합성."""

import json
import uuid
from datetime import UTC, datetime

import pytest
import test_export_publisher_recovery as fixtures
from alembic.config import Config
from sqlalchemy import event, inspect, select, text

from alembic import command
from kortravelweather.export_collector import publish_pending_exports
from kortravelweather.export_heads import ExportHeads
from kortravelweather.settings import get_settings

publisher_repository = fixtures.publisher_repository
clear_exports = fixtures.clear_exports


def test_five_thousand_published_attempts_do_not_enter_pending_history_scan(
    publisher_repository, tmp_path,
):
    repo, clock = publisher_repository, datetime.now(UTC)
    heads = ExportHeads(repo)
    run = repo.start_sync_run(provider="test-index", dataset_key="synthetic")
    repo.finish_sync_run(run.run_id, status="success")
    attempts, generations = [], []
    pending = str(uuid.uuid4())
    for i in range(5001):
        attempt_id, owner = (pending if i == 5000 else str(uuid.uuid4())), str(uuid.uuid4())
        attempts.append(dict(
            attempt_id=attempt_id, owner_id=owner, run_id=run.run_id,
            scope_key="synthetic", execution_mode="native", planned_sources=["0" * 64],
            plan_digest="0" * 64, status="sealed" if i < 5000 else "preparing",
            created_at=clock, published_at=clock if i < 5000 else None,
        ))
        if i < 5000:
            generations.append(dict(
                generation_id=str(uuid.uuid4()), attempt_id=attempt_id, owner_id=owner,
                seal_digest="0" * 64, namespace="0" * 64, data_revision_at=clock,
                source_collected_at=clock, content_digest="0" * 64, point_count=1,
                created_at=clock,
            ))
    with repo.engine.begin() as connection:
        connection.execute(heads.lifecycle.attempts.insert(), attempts)
        connection.execute(heads.generations.insert(), generations)
        for table in (heads.lifecycle.attempts, heads.generations, heads.plans):
            connection.execute(text(f"ANALYZE {table.name}"))
    captured = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if "plan_id" in statement and "published_at IS NULL" in statement:
            captured.append((statement, parameters))

    event.listen(repo.engine, "before_cursor_execute", capture)
    try:
        result = publish_pending_exports(repo, tmp_path, limit=2)
    finally:
        event.remove(repo.engine, "before_cursor_execute", capture)
    assert result.processed == 1 and result.results == {"NOT_READY": 1}
    assert len(captured) == 1
    with repo.engine.connect() as connection:
        plan = connection.exec_driver_sql(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + captured[0][0], captured[0][1],
        ).scalar_one()[0]["Plan"]

    def indexes(node):
        return ([node["Index Name"]] if "Index Name" in node else []) + [
            name for child in node.get("Plans", []) for name in indexes(child)
        ]

    assert "ix_weather_export_attempts_pending" in indexes(plan)
    assert plan["Actual Rows"] == 1
    (tmp_path / "publisher-pending-explain.json").write_text(
        json.dumps(plan, indent=2), encoding="utf-8",
    )


def test_publication_marker_is_atomic_and_migration_backfills_exact_success_clock(
    publisher_repository, tmp_path, monkeypatch,
):
    repo = publisher_repository
    heads, identity, capture, *_ = fixtures.candidate(repo, tmp_path, monkeypatch)
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    with repo.engine.connect() as connection:
        published = connection.scalar(select(heads.generations.c.created_at).where(
            heads.generations.c.attempt_id == identity.attempt_id,
        ))
    assert fixtures.state(repo, heads, identity)[0]["published_at"] == published
    monkeypatch.setenv("KOR_TRAVEL_WEATHER_DATABASE_URL",
                       repo.engine.url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "0031_external_export_cohorts")
        assert "published_at" not in {
            column["name"] for column in inspect(repo.engine).get_columns("weather_export_attempts")
        }
        command.upgrade(config, "head")
        assert fixtures.state(repo, heads, identity)[0]["published_at"] == published
        assert fixtures.state(repo, heads, identity)[0]["status"] == "sealed"
        index = next(item for item in inspect(repo.engine).get_indexes("weather_export_attempts")
                     if item["name"] == "ix_weather_export_attempts_pending")
        assert index["column_names"] == ["attempt_id"]
        assert "published_at IS NULL" in index["dialect_options"]["postgresql_where"]
        command.upgrade(config, "head")  # 이미 수용한 revision의 재실행은 no-op이다.
    finally:
        get_settings.cache_clear()


def test_publication_marker_failure_rolls_back_generation_and_head(
    publisher_repository, tmp_path, monkeypatch,
):
    repo = publisher_repository
    heads, identity, capture, *_ = fixtures.candidate(repo, tmp_path, monkeypatch)

    def reject_marker(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE weather_export_attempts SET published_at"):
            raise RuntimeError("synthetic marker failure")

    event.listen(repo.engine, "before_cursor_execute", reject_marker)
    try:
        with pytest.raises(RuntimeError, match="synthetic marker failure"):
            heads.promote(identity, capture.artifacts)
    finally:
        event.remove(repo.engine, "before_cursor_execute", reject_marker)
    with repo.engine.connect() as connection:
        assert connection.scalar(select(heads.generations.c.generation_id)) is None
        assert connection.scalar(select(heads.heads.c.generation_id)) is None
    assert fixtures.state(repo, heads, identity)[0]["published_at"] is None
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
