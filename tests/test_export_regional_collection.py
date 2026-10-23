"""네 관측망의 실제 native runner와 HTTP 전 승인 계획을 연결한다."""

from datetime import datetime
from functools import partial
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_airkorea_weather import _EntersAndCloses
from test_export_external import external_repository as external_repository
from test_export_sdk import fixture

from kortravelweather.export_artifacts import ExportArtifactStore
from kortravelweather.export_external_cohorts import external_cohort_table
from kortravelweather.export_heads import ExportHeads
from kortravelweather.export_receipts import ExportAttempt
from kortravelweather.models import KST
from kortravelweather.providers import khoa, krex, krforest, krforest_dust
from kortravelweather.settings import WeatherSettings


@pytest.mark.parametrize("kind", ["mountain", "dust", "krex", "khoa"])
def test_actual_regional_runner_prepares_then_captures_full_sdk_response(
    external_repository, tmp_path, monkeypatch, kind,
):
    import test_export_sdk
    from kortravelweather_dagster import regional_sources as regional

    monkeypatch.setattr(test_export_sdk, "AT", datetime.now(KST))
    descriptor, _, parsed, _ = fixture(kind)
    repo = external_repository
    heads = ExportHeads(repo)
    cohorts = external_cohort_table(heads.metadata)

    def before_http():
        with repo.engine.connect() as c:
            declaration = c.execute(select(cohorts).where(
                cohorts.c.provider == descriptor.provider,
                cohorts.c.dataset_key == descriptor.dataset_key,
            )).mappings().one()
            assert len(declaration["descriptors"]) == 1
            assert c.scalar(select(heads.plans.c.attempt_id).join(
                heads.lifecycle.attempts,
                heads.lifecycle.attempts.c.attempt_id == heads.plans.c.attempt_id,
            ).where(heads.lifecycle.attempts.c.run_id == declaration["run_id"]))

    settings = WeatherSettings(
        enabled_providers=[descriptor.provider], service_exports_enabled=True,
        service_export_artifact_root=tmp_path,
    )
    common = {"repository": repo, "max_values": 10000, "settings": settings}
    if kind == "mountain":
        location = krforest.station_location(parsed)

        def fetch(**kwargs):
            before_http()
            return [parsed]

        monkeypatch.setattr(regional, "fetch_mountain_weather", fetch)
        invoke = partial(regional.run_krforest_mountain_sync,
            api_key="fixture", max_records=2000, **common,
        )
    elif kind == "dust":
        station = SimpleNamespace(
            station_code="001", description="청정넷", latitude=37.5, longitude=127,
            address="서울", elevation=None,
        )
        location = krforest_dust.station_location(station)

        def fetch(**kwargs):
            before_http()
            return {"001": station}, [parsed]

        monkeypatch.setattr(regional, "fetch_dust", fetch)
        invoke = partial(regional.run_krforest_dust_sync,
            api_key="fixture", hours=1, max_records=2000, **common,
        )
    elif kind == "krex":
        location = krex.restarea_location(parsed)

        class Client(_EntersAndCloses):
            partial = False
            @property
            def restarea(self):
                return self

            async def latest_weather(self, **kwargs):
                before_http()
                return SimpleNamespace(
                    items=[parsed], raw={"list": [parsed.raw] + ([{}] if self.partial else [])},
                    total_count=2 if self.partial else 1, page_no=1, num_of_rows=1000,
                )

        invoke = partial(regional.run_krex_restarea_sync,
            client=Client(), max_records=2000, **common,
        )
    else:
        location = khoa.beach_location(parsed)

        class Client(_EntersAndCloses):
            def __init__(self, **kwargs):
                pass

            async def abeach_index(self, **kwargs):
                before_http()
                return SimpleNamespace(
                    items=[parsed], total_count=len(parsed.raw["rows"]), page_no=1,
                    num_of_rows=kwargs["num_of_rows"], has_next_page=False,
                )

        monkeypatch.setattr(khoa, "KhoaClient", Client)
        invoke = partial(regional.run_khoa_beach_index_sync,
            api_key="fixture", max_places=400, **common,
        )
    repo.upsert_location(location)
    result = invoke()
    assert result["values_loaded"] > 0
    with repo.engine.connect() as c:
        ids = c.execute(select(
            heads.lifecycle.attempts.c.attempt_id, heads.lifecycle.attempts.c.owner_id,
        ).join(cohorts, cohorts.c.run_id == heads.lifecycle.attempts.c.run_id).where(
            cohorts.c.provider == descriptor.provider,
            cohorts.c.dataset_key == descriptor.dataset_key,
        )).all()
    assert len(ids) == 1
    assert heads.promote(ExportAttempt(*ids[0]), ExportArtifactStore(tmp_path)) == "PUBLISHED"
    if kind == "krex":
        from kortravelweather.export_inventory_selection import (
            approved_source_selection,
            catalog_source_columns,
        )

        Client.partial = True
        assert invoke()["source_records"] == 1  # 같은 원문 재수집은 기존 fact를 유지한다.
        with repo.engine.connect() as c:
            catalog = heads.metadata.tables["weather_locations"]
            locations = c.execute(select(
                catalog.c.location_id, catalog.c.latitude, catalog.c.longitude,
                catalog.c.nx, catalog.c.ny, *catalog_source_columns(catalog),
            ).where(catalog.c.enabled.is_(True))).mappings().all()
            selected = approved_source_selection(
                c, heads.metadata, locations,
                expected=[(descriptor.provider, descriptor.dataset_key)],
            )
            pending = c.scalars(select(heads.lifecycle.attempts.c.status).join(
                cohorts, cohorts.c.run_id == heads.lifecycle.attempts.c.run_id,
            ).where(cohorts.c.provider == descriptor.provider)).all()
        assert selected.unconfirmed and len(selected.scopes) == 1
        assert pending == ["failed"]  # 영구 preparing 재시도 queue로 남기지 않는다.


@pytest.mark.parametrize("export_complete", [None, False, True])
def test_late_bad_anchor_preserves_native_earlier_chunks_with_export_on_or_off(
    external_repository, monkeypatch, export_complete,
):
    from kortravelweather_dagster import chunked_publish
    from kortravelweather_dagster.regional_sources import publish_regional_records
    from pydantic import ValidationError
    from test_regional_sources import KNOWN_AT, _mountain

    from kortravelweather.export_sdk_collection import SdkCollectionContext

    repo = external_repository
    monkeypatch.setattr(chunked_publish, "PUBLISH_CHUNK_LOCATIONS", 2)
    records = [_mountain(obs_id=f"late{i}", latitude=99 if i == 3 else 37.5)
               for i in range(4)]
    context = None if export_complete is None else SdkCollectionContext(
        collector=None, complete=export_complete,
    )
    with pytest.raises(ValidationError):
        publish_regional_records(
            repository=repo, provider=krforest.KRFOREST_PROVIDER,
            dataset_key=krforest.KRFOREST_MOUNTAIN_DATASET, records=records,
            to_location=krforest.station_location,
            to_values=krforest.mountain_weather_to_weather_values,
            build_source_record=krforest.mountain_source_record, max_values=1000,
            source_export=context,
        )
    one = len(krforest.mountain_weather_to_weather_values(
        records[0], location_id="test", source_record_key="test", known_at=KNOWN_AT,
    ))
    failed = repo.list_sync_runs()[0]
    assert failed.status == "failed" and failed.values_loaded == one * 2
