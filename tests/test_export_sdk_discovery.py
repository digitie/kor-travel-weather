"""원천의 새 station을 발견해도 사후 계획으로 승인하거나 기존 집합을 줄이지 않는다."""

from datetime import datetime, timedelta
from types import SimpleNamespace

from krex import parse_rest_area_weather
from sqlalchemy import select
from test_airkorea_weather import _EntersAndCloses
from test_export_external import external_repository as external_repository

from kortravelweather.export_artifacts import ExportArtifactStore
from kortravelweather.export_external_cohorts import external_cohort_table
from kortravelweather.export_heads import ExportHeads
from kortravelweather.export_receipts import ExportAttempt
from kortravelweather.export_snapshots import FrozenCurrentExports
from kortravelweather.models import KST
from kortravelweather.providers.krex import KREX_PROVIDER, KREX_RESTAREA_DATASET, restarea_location
from kortravelweather.settings import WeatherSettings


def test_discovered_station_keeps_prior_stale_then_next_predeclared_run_recovers(
    external_repository, tmp_path,
):
    from kortravelweather_dagster.regional_sources import run_krex_restarea_sync

    repo = external_repository
    observed = datetime.now(KST) - timedelta(hours=1)
    records = [parse_rest_area_weather({
        "sdate": observed.strftime("%Y%m%d"), "stdHour": str(observed.hour),
        "unitCode": f"discovery-{index}", "unitName": f"신규확인{index}",
        "xValue": "127", "yValue": "37.5", "tempValue": "-2", "humidityValue": "65",
    }) for index in range(2)]
    repo.create_location(restarea_location(records[0]))
    settings = WeatherSettings(
        enabled_providers=[KREX_PROVIDER], service_exports_enabled=True,
        service_export_artifact_root=tmp_path,
    )

    class Client(_EntersAndCloses):
        def __init__(self, rows):
            self.rows = rows
            self.restarea = self

        async def latest_weather(self, **kwargs):
            return SimpleNamespace(
                items=self.rows, raw={"list": [row.raw for row in self.rows]},
                page_no=1, num_of_rows=1000, total_count=len(self.rows),
            )

    heads = ExportHeads(repo)
    cohorts = external_cohort_table(heads.metadata)

    def collect(rows):
        run_krex_restarea_sync(
            repository=repo, client=Client(rows), max_records=2000, max_values=10000,
            settings=settings,
        )
        with repo.engine.connect() as c:
            identities = c.execute(select(
                heads.lifecycle.attempts.c.attempt_id, heads.lifecycle.attempts.c.owner_id,
            ).join(cohorts, cohorts.c.run_id == heads.lifecycle.attempts.c.run_id).where(
                cohorts.c.provider == KREX_PROVIDER,
            )).all()
        return [heads.promote(ExportAttempt(*identity), ExportArtifactStore(tmp_path))
                for identity in identities]

    assert collect(records[:1]) == ["PUBLISHED"]
    reader = FrozenCurrentExports(
        repo, ExportArtifactStore(tmp_path), signing_key="discovery-fixture", source_inventory=True,
        providers=[KREX_PROVIDER], expected_sources=[(KREX_PROVIDER, KREX_RESTAREA_DATASET)],
    )
    prior, _ = reader.page("current", limit=5000, cursor=None)
    assert prior["meta"]["source_status"] == "ok" and len(prior["items"]) == 2
    assert collect(records) == ["REJECTED"]
    stale, _ = reader.page("current", limit=5000, cursor=None)
    assert stale["meta"]["source_status"] == "stale" and stale["items"] == prior["items"]
    assert repo.get_location(restarea_location(records[1]).location_id) is not None
    assert collect(records) == ["PUBLISHED", "PUBLISHED"]
    recovered, _ = reader.page("current", limit=5000, cursor=None)
    assert recovered["meta"]["source_status"] == "ok" and len(recovered["items"]) == 4
