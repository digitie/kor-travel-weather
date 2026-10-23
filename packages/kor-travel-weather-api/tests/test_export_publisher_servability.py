"""native 성공 뒤 orchestration 실패도 옛 승인 자료를 stale로 제공한다."""

import pytest
from dagster import DagsterInstance, DagsterRunStatus, job
from sqlalchemy import select
from test_frozen_service_exports import HEADERS, _client, _collect, _collect_forecast, repository

from kortravelweather.export_artifacts import ExportArtifactStore
from kortravelweather.export_collector import dagster_status_lookup, publish_pending_exports
from kortravelweather.export_daily_store import refresh_daily_summaries
from kortravelweather.export_heads import ExportHeads, ScopeDescriptor

# sibling fixture를 명시 export한다. 본 시험은 같은 격리 DB fixture만 사용한다.
repository = repository


@job
def publisher_servability_metadata():
    pass


@pytest.mark.parametrize("resource", ["current", "forecasts"])
@pytest.mark.parametrize("policy", ["same", "different-namespace"])
def test_confirmed_dagster_failure_keeps_old_pages_stale_and_invalidates_healthy_etag(
    repository, tmp_path, resource, policy,
):
    collect = _collect if resource == "current" else _collect_forecast
    collect(repository, tmp_path)
    heads = ExportHeads(repository)
    with repository.engine.connect() as connection:
        descriptor = ScopeDescriptor.model_validate(connection.scalar(
            select(heads.plans.c.descriptor).order_by(heads.plans.c.namespace).limit(1)
        ))
    if policy != "same":
        original_namespace = descriptor.namespace
        descriptor = descriptor.model_copy(update={"normalizer_revision": "other-policy-v2"})
        assert descriptor.namespace != original_namespace
    client = _client(repository, tmp_path)
    checks = [(f"/v1/service/exports/{resource}",
               {"limit": 1, **({"style": "ultra_short"} if resource == "forecasts" else {})})]
    if resource == "current":
        refresh_daily_summaries(repository, ExportArtifactStore(tmp_path))
        checks.append(("/v1/service/exports/daily-summaries", {"limit": 1}))
    before = [client.get(url, params=params, headers=HEADERS) for url, params in checks]
    assert all(r.status_code == 200 and r.json()["meta"]["source_status"] == "ok"
               for r in before)
    with DagsterInstance.ephemeral() as instance:
        failed = instance.create_run_for_job(
            publisher_servability_metadata, status=DagsterRunStatus.FAILURE,
        )
        repository.orchestrator_run_id = failed.run_id
        try:
            run = repository.start_sync_run(provider=descriptor.provider,
                                             dataset_key=descriptor.dataset_key)
            attempt = repository.begin_export_attempt(
                run_id=run.run_id, scope_key="test-publisher-servability",
                planned_sources=[descriptor.source_scope], execution_mode="dagster",
            )
            heads.lifecycle.claim(attempt)
            heads.declare(attempt, descriptor)
            repository.finish_sync_run(run.run_id, status="success")
            assert publish_pending_exports(repository, tmp_path,
                dagster_status=dagster_status_lookup(instance)).results == {"REJECTED": 1}
        finally:
            repository.orchestrator_run_id = None
    for (url, params), healthy in zip(checks, before, strict=True):
        changed = client.get(url, params=params,
                             headers={**HEADERS, "if-none-match": healthy.headers["etag"]})
        assert changed.status_code == 200
        assert changed.json()["meta"]["source_status"] == "stale"
        assert changed.json()["items"] == healthy.json()["items"]
        assert changed.headers["etag"] != healthy.headers["etag"]
        cursor = healthy.json()["meta"]["next_cursor"]
        if cursor:
            continued = client.get(url, params={**params, "cursor": cursor}, headers=HEADERS)
            assert continued.status_code == 200
            assert continued.json()["meta"]["source_status"] == "stale"
