from __future__ import annotations

import os
from types import SimpleNamespace

import httpx
import pytest
from kma import KmaClient
from kortravelweather_dagster.kma_weather import (
    KMA_ULTRA_SHORT_NOWCAST,
    WeatherTarget,
    run_weather_sync,
)
from sqlalchemy import func, select, text

from kortravelweather.export_collector import dagster_status_lookup, publish_pending_exports
from kortravelweather.export_heads import ExportHeads
from kortravelweather.models import WeatherLocation
from kortravelweather.repository import WeatherRepository


@pytest.fixture
def repository():
    repository = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    repository.create_schema()

    def clean():
        with repository.engine.begin() as connection:
            connection.execute(
                text(
                    "TRUNCATE weather_export_scope_plans, weather_export_heads, "
                    "weather_export_generations, weather_export_generation_chunks, "
                    "weather_export_pending_refs, "
                    "weather_export_attempts, weather_locations, weather_sync_runs "
                    "RESTART IDENTITY CASCADE"
                )
            )

    clean()
    yield repository
    clean()
    repository.engine.dispose()


def _targets():
    return [
        WeatherTarget(
            WeatherLocation(location_id=key, name=key, latitude=37.5, longitude=127, nx=nx, ny=127)
        )
        for key, nx in [("one", 60), ("two", 60), ("three", 61)]
    ]


def _client():
    def respond(request):
        q = request.url.params
        row = dict(
            baseDate=q["base_date"],
            baseTime=q["base_time"],
            nx=int(q["nx"]),
            ny=int(q["ny"]),
            category="T1H",
            obsrValue="18.4",
        )
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                    "body": {
                        "pageNo": 1,
                        "numOfRows": 1000,
                        "totalCount": 1,
                        "items": {"item": [row]},
                    },
                }
            },
        )

    return KmaClient(
        "private-key", session=httpx.AsyncClient(transport=httpx.MockTransport(respond)), retries=0
    )


def _run(repository, tmp_path, *, enabled=True):
    client = _client()
    return run_weather_sync(
        repository=repository,
        client=client,
        targets=_targets(),
        base_datasets=frozenset({KMA_ULTRA_SHORT_NOWCAST}),
        export_artifact_root=tmp_path if enabled else None,
    )


def _counts(repository):
    heads = ExportHeads(repository)
    with repository.engine.connect() as connection:
        return tuple(
            connection.scalar(select(func.count()).select_from(table))
            for table in [heads.lifecycle.attempts, heads.heads, heads.generations]
        )


def test_actual_collector_fanout_then_separate_native_publisher(repository, tmp_path):
    result = _run(repository, tmp_path)
    assert result["status"] == "success" and result["values_loaded"] == 3
    assert _counts(repository) == (2, 0, 0)
    assert len(list(tmp_path.glob("*.jsonl"))) == 2
    first = publish_pending_exports(repository, tmp_path, limit=1)
    assert first.processed == 1 and first.results == {"PUBLISHED": 1} and first.has_more
    second = publish_pending_exports(repository, tmp_path, limit=1, cursor=first.next_cursor)
    assert second.results == {"PUBLISHED": 1} and not second.has_more
    assert _counts(repository) == (2, 2, 2)
    assert publish_pending_exports(repository, tmp_path).processed == 0


def test_off_preserves_existing_collector_without_export_work(repository, tmp_path):
    result = _run(repository, tmp_path, enabled=False)
    assert result["status"] == "success" and result["values_loaded"] == 3
    assert _counts(repository) == (0, 0, 0)
    assert not list(tmp_path.iterdir())


def test_exact_dagster_instance_success_required_after_sync_success(repository, tmp_path):
    repository.orchestrator_run_id = "exact-dagster-run"
    assert _run(repository, tmp_path)["status"] == "success"

    class Instance:
        state = None

        def get_run_by_id(self, run_id):
            assert run_id == "exact-dagster-run" and repository.engine.pool.checkedout() == 0
            return SimpleNamespace(status=SimpleNamespace(name=self.state)) if self.state else None

    instance = Instance()
    probe = dagster_status_lookup(instance)
    assert publish_pending_exports(repository, tmp_path, dagster_status=probe).results == {
        "NOT_READY": 2
    }
    assert _counts(repository) == (2, 0, 0)
    instance.state = "STARTED"
    assert publish_pending_exports(repository, tmp_path, dagster_status=probe).results == {
        "NOT_READY": 2
    }
    instance.state = "SUCCESS"
    assert publish_pending_exports(repository, tmp_path, dagster_status=probe).results == {
        "PUBLISHED": 2
    }
    assert _counts(repository) == (2, 2, 2)


def test_capture_file_failure_keeps_original_success_and_cannot_publish(
    repository, tmp_path, monkeypatch
):
    from kortravelweather.export_artifacts import ExportArtifactStore

    def fail(*args, **kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr(ExportArtifactStore, "publish", fail)
    result = _run(repository, tmp_path)
    assert result["status"] == "success" and result["values_loaded"] == 3
    progress = publish_pending_exports(repository, tmp_path)
    assert progress.results == {} and progress.processed == 0
    assert _counts(repository) == (2, 0, 0)


def test_optin_rejects_unsupported_mixed_datasets(repository, tmp_path):
    with pytest.raises(ValueError, match="단일 KMA"):
        run_weather_sync(
            repository=repository,
            client=_client(),
            targets=_targets(),
            export_artifact_root=tmp_path,
        )
    assert _counts(repository) == (0, 0, 0)


def test_missing_provider_proof_preserves_facts_and_fails_export_closed(
    repository, tmp_path, monkeypatch
):
    from kortravelweather_dagster import kma_weather

    original = kma_weather._metadata_dict

    def strip(value):
        result = original(value)
        result.pop("pagination", None)
        return result

    monkeypatch.setattr(kma_weather, "_metadata_dict", strip)
    result = _run(repository, tmp_path)
    assert result["status"] == "success" and result["values_loaded"] == 3
    assert _counts(repository) == (2, 0, 0)
    assert publish_pending_exports(repository, tmp_path).processed == 0
    assert not list(tmp_path.glob("*.jsonl"))


def test_whole_grid_optin_preserves_fifty_location_lock_bound(repository, tmp_path):
    from kortravelweather_dagster import kma_weather

    targets = [
        WeatherTarget(
            WeatherLocation(
                location_id=f"bounded-{i}",
                name=f"bounded-{i}",
                latitude=37.5,
                longitude=127,
                nx=60,
                ny=127,
            )
        )
        for i in range(51)
    ]
    original, locked = repository.ingest_batch, []

    def ingest(**kwargs):
        locations = {v.location_id for v in kwargs.get("values", [])}
        if locations:
            locked.append(len(locations))
        assert len(locations) <= kma_weather.GRID_PUBLISH_LOCATIONS
        return original(**kwargs)

    repository.ingest_batch = ingest
    result = run_weather_sync(
        repository=repository,
        client=_client(),
        targets=targets,
        base_datasets=frozenset({KMA_ULTRA_SHORT_NOWCAST}),
        export_artifact_root=tmp_path,
    )
    assert result["status"] == "success" and result["values_loaded"] == 51
    assert locked == [50, 1]
    assert _counts(repository) == (0, 0, 0)
    assert not list(tmp_path.glob("*.jsonl"))
