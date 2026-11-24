"""실제 AirKorea 수집 경로에서 HTTP 전 계획과 실패 시 승인 보존을 검증한다."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from airkorea import parse_measurement
from sqlalchemy import select
from test_airkorea_weather import _EntersAndCloses, _station
from test_export_external import external_repository as external_repository

from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore
from kortravelweather.export_external_cohorts import external_cohort_table
from kortravelweather.export_heads import ExportHeads
from kortravelweather.export_receipts import ExportAttempt
from kortravelweather.export_snapshots import FrozenCurrentExports
from kortravelweather.providers.airkorea import AIRKOREA_MEASUREMENT_DATASET, AIRKOREA_PROVIDER


@pytest.mark.parametrize("empty_first", [False, True])
def test_actual_airkorea_predeclares_before_http_and_keeps_prior_on_native_failure(
    external_repository, tmp_path, empty_first, monkeypatch,
):
    from kortravelweather_dagster.airkorea_weather import run_airkorea_weather_sync

    from kortravelweather.export_sdk_collector import SdkExportCollector

    # 준비는 잠금 경합을 막히는 sleep으로 재시도한다. event loop 밖(thread)에서 불려야 한다.
    prepared_on_loop = []
    prepare = SdkExportCollector.prepare

    def prepare_off_loop(self, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            prepared_on_loop.append(False)
        else:
            prepared_on_loop.append(True)
        return prepare(self, **kwargs)

    monkeypatch.setattr(SdkExportCollector, "prepare", prepare_off_loop)
    repo = external_repository
    heads = ExportHeads(repo)
    catalog = heads.metadata.tables["weather_locations"]
    cohorts = external_cohort_table(heads.metadata)
    names = ["검증" + uuid.uuid4().hex[:8] for _ in range(2)]

    class Client(_EntersAndCloses):
        fail = False
        empty_once = False

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
            if self.empty_once:
                self.empty_once = False
                return []
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
        initial_client = Client()
        initial_client.empty_once = empty_first
        result = run_airkorea_weather_sync(
            repository=repo, client=initial_client, export_artifact_root=tmp_path,
        )
        assert result["status"] == "success" and result["values_loaded"] == 4
        assert result["requests_fetched"] == (3 if empty_first else 2)
        assert prepared_on_loop == [False, False]
        with repo.engine.connect() as c:
            identities = c.execute(select(
                heads.lifecycle.attempts.c.attempt_id, heads.lifecycle.attempts.c.owner_id,
            ).where(heads.lifecycle.attempts.c.run_id == result["run_id"])).all()
        assert len(identities) == 2
        for identity in identities:
            assert heads.promote(
                ExportAttempt(*identity), DatabaseExportArtifactStore(repo)
            ) == "PUBLISHED"
        reader = FrozenCurrentExports(
            repo, DatabaseExportArtifactStore(repo), signing_key="air-fixture-cursor",
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
        assert failed["station_failure_reasons"] == {"request_error": 2}
        with repo.engine.connect() as c:
            rejected = c.execute(select(
                heads.lifecycle.attempts.c.status, heads.lifecycle.attempts.c.error_code,
            ).where(heads.lifecycle.attempts.c.run_id == failed["run_id"])).all()
        # 거절 attempt는 측정소의 실제 실패 사유를 남긴다(고정값 not_in_response가 아니다).
        assert sorted(map(tuple, rejected)) == [
            ("failed", "SOURCE_COVERAGE_UNCONFIRMED:request_error"),
        ] * 2
        stale, changed = reader.page("current", limit=1, cursor=None)
        assert stale["meta"]["source_status"] == "stale" and changed != etag
        continued, _ = reader.page("current", limit=1, cursor=old_cursor)
        assert continued["meta"]["source_status"] == "stale"
    finally:
        with repo.engine.begin() as c:
            c.execute(catalog.update().values(enabled=False))
            if prior:
                c.execute(catalog.update().where(catalog.c.location_id.in_(prior)).values(enabled=True))


def test_actual_airkorea_rejected_attempt_keeps_station_failure_reason(
    external_repository, tmp_path, monkeypatch,
):
    """측정소마다 거절 attempt의 ``error_code``가 그 측정소의 실패 사유를 남긴다.

    광주로 가려지는 통합 주소 측정소의 같은 이름 행이 전남 묶음에만 오면 ``sido_mismatch``,
    어느 응답에도 없으면 ``not_in_response``다. 받은 측정소의 attempt는 거절하지 않는다.
    """
    from kortravelweather_dagster.airkorea_weather import run_airkorea_weather_sync

    from kortravelweather.export_sdk_collector import SdkExportCollector

    repo = external_repository
    heads = ExportHeads(repo)
    catalog = heads.metadata.tables["weather_locations"]
    attempts = heads.lifecycle.attempts
    suffix = uuid.uuid4().hex[:8]
    bound, missing, mismatch = (label + suffix for label in ("수집", "누락", "불일치"))
    attempt_by_name: dict[str, str] = {}
    prepare = SdkExportCollector.prepare

    def recording_prepare(self, **kwargs):
        prepared = prepare(self, **kwargs)
        if prepared is not None:
            attempt_by_name[kwargs["location"].name] = prepared.identity.attempt_id
        return prepared

    monkeypatch.setattr(SdkExportCollector, "prepare", recording_prepare)

    class Client(_EntersAndCloses):
        async def stations(self, *, page_no, num_of_rows):
            if page_no != 1:
                return []
            return [
                _station(bound, "서울 중구", 37.56, 126.97),
                _station(missing, "서울 종로구", 37.57, 126.98),
                _station(mismatch, "전남광주통합특별시 북구 군왕로 141번길 6", 35.18, 126.91),
            ]

        async def sido_measurements(self, sido_name, *, page_no, num_of_rows):
            if page_no != 1:
                return []
            stamp = (datetime.now(UTC) - timedelta(hours=1)).astimezone(
                ZoneInfo("Asia/Seoul")
            ).strftime("%Y-%m-%d %H:%M")
            rows = {"서울": [(bound, "서울")], "전남": [(mismatch, "전남")]}.get(sido_name, [])
            return [parse_measurement({
                "stationName": name, "sidoName": sido, "dataTime": stamp,
                "pm10Value": "20", "pm25Value": "10",
            }, station_name=name) for name, sido in rows]

    with repo.engine.begin() as c:
        prior = list(c.scalars(select(catalog.c.location_id).where(catalog.c.enabled.is_(True))))
        c.execute(catalog.update().values(enabled=False))
    try:
        result = run_airkorea_weather_sync(
            repository=repo, client=Client(), export_artifact_root=tmp_path,
        )
        assert result["station_failure_reasons"] == {"not_in_response": 1, "sido_mismatch": 1}
        assert result["values_loaded"] == 2
        assert set(attempt_by_name) == {bound, missing, mismatch}
        with repo.engine.connect() as c:
            by_attempt = {
                row.attempt_id: (row.status, row.error_code)
                for row in c.execute(select(
                    attempts.c.attempt_id, attempts.c.status, attempts.c.error_code,
                ).where(attempts.c.attempt_id.in_(attempt_by_name.values())))
            }
        assert by_attempt[attempt_by_name[mismatch]] == (
            "failed", "SOURCE_COVERAGE_UNCONFIRMED:sido_mismatch",
        )
        assert by_attempt[attempt_by_name[missing]] == (
            "failed", "SOURCE_COVERAGE_UNCONFIRMED:not_in_response",
        )
        status, error_code = by_attempt[attempt_by_name[bound]]
        assert status != "failed" and error_code is None
    finally:
        with repo.engine.begin() as c:
            c.execute(catalog.update().values(enabled=False))
            if prior:
                c.execute(catalog.update().where(catalog.c.location_id.in_(prior)).values(enabled=True))
