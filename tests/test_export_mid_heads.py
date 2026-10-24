"""지역별 중기 source가 일반 head/capture와 inventory에 정확히 연결된다."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from test_export_external import external_repository as external_repository
from test_export_mid import _source

from kortravelweather.export_artifacts import ExportArtifactError, ExportArtifactStore
from kortravelweather.export_collector import KmaNowcastExportCollector
from kortravelweather.export_heads import ExportHeads, parse_scope_descriptor
from kortravelweather.export_inventory import export_inventory_table
from kortravelweather.export_mid import normalize_mid_source
from kortravelweather.export_source_cohorts import declare_source_cohort
from kortravelweather.models import WeatherLocation


@pytest.mark.parametrize("part", ["land", "temperature"])
def test_mid_native_capture_publishes_issued_revision_and_role_inventory(
    external_repository, tmp_path, part,
):
    repo = external_repository
    heads = ExportHeads(repo)
    # 실제 시각의 lifecycle에 연결하므로 발표도 현재 유효한 fixture로 만든다.
    # 고정 2026-10-23 발표와 now()를 섞으면 18시간 뒤부터 이 테스트가 실패한다.
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    issued = now.replace(
        hour=18 if now.hour >= 18 or now.hour < 6 else 6,
        minute=0, second=0, microsecond=0,
    )
    if issued > now:
        issued -= timedelta(days=1)
    prototype, source = _source(part, tm_fc=issued.strftime("%Y%m%d%H%M"))
    keys = tuple(sorted("mid-head-" + uuid.uuid4().hex for _ in range(2)))
    descriptor = prototype.model_copy(update={"location_ids": keys})
    for key in keys:
        repo.create_location(WeatherLocation(
            location_id=key, name="중기 검증", latitude=37.5, longitude=127,
            metadata={f"mid_{part}_region_code": descriptor.region_code},
        ))
    run = repo.start_sync_run(
        provider=descriptor.provider, dataset_key=descriptor.dataset_key,
        export_cohort_required=True,
    )
    declare_source_cohort(
        repo, run_id=run.run_id, provider=descriptor.provider,
        dataset_key=descriptor.dataset_key, descriptors=[descriptor],
    )
    identity = repo.begin_export_attempt(
        run_id=run.run_id, scope_key=descriptor.namespace,
        planned_sources=[descriptor.source_scope], execution_mode="native",
    )
    heads.lifecycle.claim(identity)
    heads.declare(identity, descriptor)
    assert parse_scope_descriptor(descriptor.model_dump()) == descriptor
    source.update(run_id=run.run_id, fetched_at=datetime.now(UTC))

    class Collector(KmaNowcastExportCollector):
        def _bind_source(self, prepared, record):
            self.heads.bind_mid_response(prepared.identity, record)

    collector = Collector(repo, run_id=run.run_id, artifact_root=tmp_path)
    values = list(normalize_mid_source(descriptor, source))
    count = collector.ingest(
        SimpleNamespace(identity=identity, descriptor=descriptor), record=source, values=values,
    )
    assert count == len(values)
    repo.finish_sync_run(run.run_id, status="success", values_loaded=count)
    assert heads.promote(identity, ExportArtifactStore(tmp_path)) == "PUBLISHED"
    table = export_inventory_table(heads.metadata)
    with repo.engine.connect() as c:
        plan = c.execute(select(heads.plans).where(
            heads.plans.c.attempt_id == identity.attempt_id,
        )).mappings().one()
        assert plan["data_revision_at"] < plan["source_collected_at"]
        approved = c.execute(select(table).where(table.c.namespace == descriptor.namespace)).all()
    assert len(approved) == 2 and {r.source_part for r in approved} == {part}


def test_mid_canonical_region_mismatch_rejects_declaration(external_repository):
    repo = external_repository
    prototype, _ = _source("land")
    key = "mid-wrong-" + uuid.uuid4().hex
    descriptor = prototype.model_copy(update={"location_ids": (key,)})
    repo.create_location(WeatherLocation(
        location_id=key, name="다른 지역", latitude=37.5, longitude=127,
        metadata={"mid_land_region_code": "wrong"},
    ))
    run = repo.start_sync_run(
        provider=descriptor.provider, dataset_key=descriptor.dataset_key,
        export_cohort_required=True,
    )
    with pytest.raises(ExportArtifactError, match="좌표/grid"):
        declare_source_cohort(
            repo, run_id=run.run_id, provider=descriptor.provider,
            dataset_key=descriptor.dataset_key, descriptors=[descriptor],
        )
    repo.finish_sync_run(run.run_id, status="failed")
