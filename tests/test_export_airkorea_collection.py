"""실제 AirKorea 수집 경로에서 HTTP 전 계획과 실패 시 승인 보존을 검증한다."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from airkorea import parse_measurement
from sqlalchemy import select
from test_airkorea_weather import _EntersAndCloses, _station
from test_export_external import external_repository as external_repository

from kortravelweather.export_artifacts import ExportArtifactStore
from kortravelweather.export_external_cohorts import external_cohort_table
from kortravelweather.export_heads import ExportHeads
from kortravelweather.export_receipts import ExportAttempt
from kortravelweather.export_snapshots import FrozenCurrentExports
from kortravelweather.providers.airkorea import AIRKOREA_MEASUREMENT_DATASET, AIRKOREA_PROVIDER


def test_actual_airkorea_predeclares_before_http_and_keeps_prior_on_native_failure(
    external_repository, tmp_path,
):
    from kortravelweather_dagster.airkorea_weather import run_airkorea_weather_sync

    repo = external_repository
    heads = ExportHeads(repo)
    catalog = heads.metadata.tables["weather_locations"]
    cohorts = external_cohort_table(heads.metadata)
    names = ["검증" + uuid.uuid4().hex[:8] for _ in range(2)]

    class Client(_EntersAndCloses):
        fail = False

        async def stations(self, *, page_no, num_of_rows):
            if page_no != 1:
                return []
            return [_station(name, "서울 중구", 37.56, 126.97) for name in names]

        async def sido_measurements(self, sido_name, *, page_no, num_of_rows):
            with repo.engine.connect() as c:
                cohort = c.execute(select(cohorts).where(
                    cohorts.c.provider == AIRKOREA_PROVIDER,
                    cohorts.c.dataset_key == AIRKOREA_MEASUREMENT_DATASET,
                )).mappings().one()
                assert len(cohort["descriptors"]) == 2
                plans = c.execute(select(heads.plans).where(
                    heads.plans.c.attempt_id.in_(select(heads.lifecycle.attempts.c.attempt_id).where(
                        heads.lifecycle.attempts.c.run_id == cohort["run_id"],
                    )),
                )).all()
                assert len(plans) == 2
            if self.fail:
                raise TimeoutError("fixture upstream timeout")
            stamp = (datetime.now(UTC) - timedelta(hours=1)).astimezone(
                ZoneInfo("Asia/Seoul")
            ).strftime("%Y-%m-%d %H:%M")
            return [parse_measurement({
                "stationName": name, "sidoName": "서울", "dataTime": stamp,
                "pm10Value": "20", "pm25Value": "10",
            }, station_name=name) for name in names] if page_no == 1 else []

    with repo.engine.begin() as c:
        prior = list(c.scalars(select(catalog.c.location_id).where(catalog.c.enabled.is_(True))))
        c.execute(catalog.update().values(enabled=False))
    try:
        result = run_airkorea_weather_sync(
            repository=repo, client=Client(), export_artifact_root=tmp_path,
        )
        assert result["status"] == "success" and result["values_loaded"] == 4
        with repo.engine.connect() as c:
            identities = c.execute(select(
                heads.lifecycle.attempts.c.attempt_id, heads.lifecycle.attempts.c.owner_id,
            ).where(heads.lifecycle.attempts.c.run_id == result["run_id"])).all()
        assert len(identities) == 2
        for identity in identities:
            assert heads.promote(
                ExportAttempt(*identity), ExportArtifactStore(tmp_path)
            ) == "PUBLISHED"
        reader = FrozenCurrentExports(
            repo, ExportArtifactStore(tmp_path), signing_key="air-fixture-cursor",
            providers=(AIRKOREA_PROVIDER,), source_inventory=True,
            expected_sources=((AIRKOREA_PROVIDER, AIRKOREA_MEASUREMENT_DATASET),),
        )
        healthy, etag = reader.page("current", limit=1, cursor=None)
        assert healthy["meta"]["source_status"] == "ok"
        old_cursor = healthy["meta"]["next_cursor"]
        assert old_cursor
        client = Client()
        client.fail = True
        failed = run_airkorea_weather_sync(
            repository=repo, client=client, export_artifact_root=tmp_path,
        )
        assert failed["stations_failed"] == 2 and failed["values_loaded"] == 0
        stale, changed = reader.page("current", limit=1, cursor=None)
        assert stale["meta"]["source_status"] == "stale" and changed != etag
        continued, _ = reader.page("current", limit=1, cursor=old_cursor)
        assert continued["meta"]["source_status"] == "stale"
    finally:
        with repo.engine.begin() as c:
            c.execute(catalog.update().values(enabled=False))
            if prior:
                c.execute(catalog.update().where(catalog.c.location_id.in_(prior)).values(enabled=True))
