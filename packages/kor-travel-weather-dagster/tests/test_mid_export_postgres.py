"""실제 native 중기 수집→capture→publisher를 PostgreSQL에서 검증한다."""

import asyncio
import uuid

import httpx
import pytest
from kma import DataGoKrClient, KmaRequestError
from kortravelweather_dagster.kma_weather import run_weather_sync
from sqlalchemy import func, select
from test_export_external import external_repository as external_repository
from test_mid_export_collection import NoGridClient, _raw, targets

from kortravelweather.export_collector import publish_pending_exports
from kortravelweather.export_external_cohorts import external_cohort_table
from kortravelweather.export_heads import ExportHeads
from kortravelweather.export_inventory import export_inventory_table


def test_actual_mid_native_capture_promotes_once_and_later_failure_preserves_heads(
    external_repository,
    tmp_path,
):
    repo = external_repository
    selected = targets()
    prefix = "mid-native-" + uuid.uuid4().hex
    for i, target in enumerate(selected):
        selected[i] = type(target)(
            target.location.model_copy(update={"location_id": f"{prefix}-{i}"}),
            mid_land_region_code=target.land_region_code,
            mid_temperature_region_code=target.temperature_region_code,
        )
    heads = ExportHeads(repo)
    seen = []
    fail = False

    def response(request):
        part = "land" if request.url.path.endswith("getMidLandFcst") else "temperature"
        region = request.url.params["regId"]
        if not seen:
            # 첫 요청 시각에 실제 SQL plan 3개와 source roster가 이미 존재한다.
            with repo.engine.connect() as connection:
                cohort = (
                    connection.execute(
                        select(external_cohort_table(heads.metadata)).where(
                            external_cohort_table(heads.metadata).c.provider == "python-kma-api",
                            external_cohort_table(heads.metadata).c.dataset_key
                            == "kma_mid_forecast",
                        )
                    )
                    .mappings()
                    .one()
                )
                attempts = heads.lifecycle.attempts
                assert (
                    connection.scalar(
                        select(func.count())
                        .select_from(
                            heads.plans.join(
                                attempts, heads.plans.c.attempt_id == attempts.c.attempt_id
                            )
                        )
                        .where(attempts.c.run_id == cohort["run_id"])
                    )
                    == 3
                )
        seen.append((part, region, request.url.params["tmFc"]))
        if fail and part == "temperature":
            raise httpx.ConnectError("injected offline failure", request=request)
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                    "body": {
                        "items": {"item": [_raw(part, region)]},
                        "totalCount": 1,
                        "pageNo": 1,
                        "numOfRows": 10,
                    },
                }
            },
        )

    def collect():
        session = httpx.AsyncClient(transport=httpx.MockTransport(response))
        client = DataGoKrClient("offline", session=session, retries=0)
        try:
            return run_weather_sync(
                repository=repo,
                client=NoGridClient(),
                data_client=client,
                targets=selected,
                include_base=False,
                include_mid=True,
                export_artifact_root=tmp_path,
            )
        finally:
            asyncio.run(session.aclose())

    result = collect()
    assert result["status"] == "success"
    assert result["requests_fetched"] == 3 and result["values_loaded"] == 126
    assert len({issue for _, _, issue in seen}) == 1
    outcome = publish_pending_exports(repo, tmp_path, limit=10)
    assert outcome.results.get("PUBLISHED") == 3
    inventory = export_inventory_table(heads.metadata)
    with repo.engine.connect() as connection:
        before = {
            (row.location_id, row.source_part): row.generation_id
            for row in connection.execute(
                select(
                    inventory.c.location_id,
                    inventory.c.source_part,
                    inventory.c.generation_id,
                ).where(inventory.c.location_id.in_([t.location.location_id for t in selected]))
            ).all()
        }
        assert (
            connection.scalar(
                select(func.count())
                .select_from(inventory)
                .where(
                    inventory.c.location_id.in_([t.location.location_id for t in selected]),
                )
            )
            == 6
        )
    fail = True
    seen.clear()
    with pytest.raises(KmaRequestError, match="request failed"):
        collect()
    failed_outcome = publish_pending_exports(repo, tmp_path, limit=10)
    assert failed_outcome.results.get("REJECTED") == 3
    with repo.engine.connect() as connection:
        after = {
            (row.location_id, row.source_part): row.generation_id
            for row in connection.execute(
                select(
                    inventory.c.location_id,
                    inventory.c.source_part,
                    inventory.c.generation_id,
                ).where(inventory.c.location_id.in_([t.location.location_id for t in selected]))
            ).all()
        }
    assert before == after
    with repo.engine.connect() as connection:
        run_id = connection.scalar(
            select(heads.metadata.tables["weather_sync_runs"].c.run_id)
            .where(
                heads.metadata.tables["weather_sync_runs"].c.provider == "python-kma-api",
                heads.metadata.tables["weather_sync_runs"].c.dataset_key == "kma_mid_forecast",
            )
            .order_by(heads.metadata.tables["weather_sync_runs"].c.started_at.desc())
            .limit(1)
        )
        assert (
            connection.execute(
                select(heads.lifecycle.attempts.c.status).where(
                    heads.lifecycle.attempts.c.run_id == run_id,
                )
            )
            .scalars()
            .all()
            == ["failed"] * 3
        )
