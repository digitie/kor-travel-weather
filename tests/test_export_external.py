"""원천 raw를 독립 재정규화한다. 캡처 values의 자기 hash를 증거로 쓰지 않는다."""

import copy
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from test_external_providers import FakeResponse, FixtureTransport

from kortravelweather.export_heads import semantic_point_digest
from kortravelweather.providers import OpenMeteoProvider, OpenWeatherMapProvider, WeatherApiProvider
from kortravelweather.providers.base import ProviderLocation

LOCATION = ProviderLocation("export-test", 37.5, 127, {})
AT = datetime(2026, 10, 23, 8, tzinfo=UTC)


@pytest.mark.parametrize(
    "provider,payload",
    [
        (
            WeatherApiProvider,
            {
                "location": {"tz_id": "Asia/Seoul", "lat": 37.5, "lon": 127},
                "current": {"last_updated_epoch": AT.timestamp(), "temp_c": 20, "humidity": 50},
            },
        ),
        (
            OpenWeatherMapProvider,
            {"coord": {"lat": 37.5, "lon": 127}, "dt": AT.timestamp(), "main": {"temp": 20}},
        ),
        (
            OpenMeteoProvider,
            {
                "latitude": 37.5,
                "longitude": 127,
                "current": {"time": AT.isoformat(), "temperature_2m": 20},
            },
        ),
    ],
)
def test_saved_raw_revalidation_uses_same_adapter_without_http_or_live_credential(
    provider, payload
):
    live = provider(api_key="fixture-secret", transport=FixtureTransport(FakeResponse(payload)))
    response = live.fetch(LOCATION, at=AT)

    class ForbiddenTransport:
        def request(self, *args, **kwargs):
            raise AssertionError("응답 정규화 중에는 HTTP를 호출하면 안 됩니다.")

    replay = provider(transport=ForbiddenTransport())
    raw = response.source_record["payload"]
    reconstructed = replay.normalize_response(
        LOCATION,
        dataset_key=response.dataset_key,
        payload=raw["rows"][0],
        metadata=raw["response_metadata"],
        at=response.source_record["fetched_at"],
    )
    wanted = sorted(semantic_point_digest(v.model_dump(mode="json")) for v in response.values)
    proven = sorted(semantic_point_digest(v.model_dump(mode="json")) for v in reconstructed.values)
    assert wanted == proven
    assert (
        reconstructed.source_record["source_record_key"]
        == response.source_record["source_record_key"]
    )
    forged = reconstructed.values[0].model_copy(update={"value_number": Decimal("999")})
    assert semantic_point_digest(forged.model_dump(mode="json")) not in proven
    with pytest.raises(ValueError, match="timezone"):
        replay.normalize_response(
            LOCATION,
            dataset_key=response.dataset_key,
            payload=raw["rows"][0],
            metadata=raw["response_metadata"],
            at=AT.replace(tzinfo=None),
        )


def _external_payload(provider, *, forecast, at):
    hour = at.replace(minute=0, second=0, microsecond=0)
    if provider == "weatherapi":
        current = {
            "last_updated_epoch": hour.timestamp(),
            "temp_c": 20,
            "humidity": 50,
            "wind_kph": 18,
        }
        result = {"location": {"tz_id": "UTC", "lat": 37.5, "lon": 127}, "current": current}
        if forecast:
            start = hour.replace(hour=0)
            result["forecast"] = {
                "forecastday": [
                    {
                        "hour": [
                            {
                                **current,
                                "time_epoch": (start + timedelta(hours=i)).timestamp(),
                                "time": (start + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M"),
                            }
                            for i in range(day * 24, (day + 1) * 24)
                        ]
                    }
                    for day in range(2)
                ]
            }
        return result
    if provider == "openweathermap":
        base = {"main": {"temp": 20, "humidity": 50}, "wind": {"speed": 5}}
        if forecast:
            start = hour.replace(hour=hour.hour - hour.hour % 3) + timedelta(hours=3)
            return {
                "cnt": 40,
                "city": {"coord": {"lat": 37.5, "lon": 127}},
                "list": [
                    {**base, "dt": (start + timedelta(hours=i * 3)).timestamp()} for i in range(40)
                ],
            }
        return {**base, "dt": hour.timestamp(), "coord": {"lat": 37.5, "lon": 127}}
    result = {"latitude": 37.5, "longitude": 127}
    if forecast:
        start = hour.replace(hour=0)
        result["hourly"] = {
            "time": [(start + timedelta(hours=i)).isoformat() for i in range(168)],
            "temperature_2m": [20] * 168,
            "relative_humidity_2m": [50] * 168,
            "apparent_temperature": [19] * 168,
            "precipitation_probability": [0] * 168,
            "precipitation": [0] * 168,
            "wind_speed_10m": [18] * 168,
            "wind_direction_10m": [180] * 168,
            "weather_code": [0] * 168,
        }
    else:
        result["current"] = {
            "time": hour.isoformat(),
            "temperature_2m": 20,
            "relative_humidity_2m": 50,
            "wind_speed_10m": 18,
        }
    return result


@pytest.mark.parametrize("provider", ["open_meteo", "weatherapi", "openweathermap"])
@pytest.mark.parametrize("forecast", [False, True])
def test_external_source_contract_rechecks_raw_location_times_lengths_and_core(provider, forecast):
    from kortravelweather.export_artifacts import ExportArtifactError
    from kortravelweather.export_external import (
        ADAPTERS,
        RetrievalScopeDescriptor,
        normalize_external_source,
    )

    now = datetime.now(UTC)
    dataset = f"{provider}_{'forecast' if forecast else 'current'}"
    descriptor = RetrievalScopeDescriptor(
        provider=provider,
        dataset_key=dataset,
        entity_id=LOCATION.location_id,
        forecast_style="short" if forecast else "observed",
        location_ids=(LOCATION.location_id,),
        latitude=37.5,
        longitude=127,
        normalizer_revision=f"{provider}-v1",
    )
    response = ADAPTERS[provider](
        api_key="fixture",
        transport=FixtureTransport(
            FakeResponse(_external_payload(provider, forecast=forecast, at=now))
        ),
    ).fetch(LOCATION, dataset_key=dataset, at=now)
    values = normalize_external_source(descriptor, response.source_record)
    assert values and all(v.provider == provider for v in values)
    wrong = descriptor.model_copy(update={"latitude": 38.5})
    with pytest.raises(ExportArtifactError):
        normalize_external_source(wrong, response.source_record)
    broken = copy.deepcopy(response.source_record)
    if forecast:
        raw = broken["payload"]["rows"][0]
        if provider == "open_meteo":
            raw["hourly"]["temperature_2m"].pop()
        elif provider == "weatherapi":
            raw["forecast"]["forecastday"][0]["hour"].pop()
        else:
            raw["list"].pop()
    else:
        broken["fetched_at"] = now + timedelta(days=1)
    with pytest.raises(ExportArtifactError):
        normalize_external_source(descriptor, broken)


@pytest.fixture
def external_repository():
    from kortravelweather.repository import WeatherRepository

    repo = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    repo.create_schema()
    yield repo
    repo.engine.dispose()


@pytest.mark.parametrize("provider", ["open_meteo", "weatherapi", "openweathermap"])
@pytest.mark.parametrize("forecast", [False, True])
def test_actual_external_capture_native_success_promotes_without_changing_fact_identity(
    external_repository, tmp_path, provider, forecast
):
    from sqlalchemy import select

    from kortravelweather.export_external import ADAPTERS
    from kortravelweather.export_external_collector import ExternalExportCollector
    from kortravelweather.models import WeatherLocation

    repo = external_repository
    location = ProviderLocation("ext-" + uuid.uuid4().hex, 37.5, 127, {})
    repo.create_location(
        WeatherLocation(
            location_id=location.location_id,
            name="외부 검증",
            latitude=37.5,
            longitude=127,
        )
    )
    dataset = f"{provider}_{'forecast' if forecast else 'current'}"
    run = repo.start_sync_run(provider=provider, dataset_key=dataset)
    collector = ExternalExportCollector(repo, run_id=run.run_id, artifact_root=tmp_path)
    prepared = collector.prepare(location=location, provider=provider, dataset_key=dataset)
    assert prepared is not None
    now = datetime.now(UTC)
    response = ADAPTERS[provider](
        api_key="fixture",
        transport=FixtureTransport(
            FakeResponse(_external_payload(provider, forecast=forecast, at=now))
        ),
    ).fetch(location, dataset_key=dataset, at=now)
    record = {**response.source_record, "run_id": run.run_id}
    assert collector.ingest(prepared, record=record, values=response.values) == len(response.values)
    repo.finish_sync_run(run.run_id, status="success")
    assert collector.heads.promote(prepared.identity, collector.artifacts) == "PUBLISHED"
    with repo.engine.connect() as c:
        head = (
            c.execute(
                select(collector.heads.heads).where(
                    collector.heads.heads.c.namespace == prepared.descriptor.namespace
                )
            )
            .mappings()
            .one()
        )
        assert head["data_revision_at"] == record["fetched_at"]
        assert head["source_collected_at"] == record["fetched_at"]


def test_actual_external_job_declares_whole_selection_before_http_and_publishes_each_source(
    external_repository, tmp_path
):
    from kortravelweather_dagster.external_weather import run_external_weather_sync
    from sqlalchemy import select

    from kortravelweather.export_artifacts import ExportArtifactStore
    from kortravelweather.export_external_cohorts import external_cohort_table
    from kortravelweather.export_heads import ExportHeads
    from kortravelweather.export_receipts import ExportAttempt
    from kortravelweather.models import WeatherLocation

    repo = external_repository
    locations = [ProviderLocation("job-" + uuid.uuid4().hex, 37.5, 127, {}) for _ in range(2)]
    for target in locations:
        repo.create_location(
            WeatherLocation(
                location_id=target.location_id,
                name="실제 외부 job",
                latitude=37.5,
                longitude=127,
            )
        )
    table = external_cohort_table(ExportHeads(repo).metadata)

    class CohortCheckedTransport(FixtureTransport):
        def request(self, *args, **kwargs):
            with repo.engine.connect() as c:
                row = c.execute(
                    select(table.c.targets).where(
                        table.c.provider == "open_meteo",
                        table.c.dataset_key == "open_meteo_current",
                    )
                ).scalar_one()
            assert {v["location_id"] for v in row} == {v.location_id for v in locations}
            return super().request(*args, **kwargs)

    transport = CohortCheckedTransport(
        *[
            FakeResponse(_external_payload("open_meteo", forecast=False, at=datetime.now(UTC)))
            for _ in locations
        ]
    )
    result = run_external_weather_sync(
        repository=repo,
        provider=OpenMeteoProvider(transport=transport),
        targets=locations,
        dataset_key="open_meteo_current",
        export_artifact_root=tmp_path,
    )
    assert result["status"] == "success" and result["requests_fetched"] == 2
    heads = ExportHeads(repo)
    with repo.engine.connect() as c:
        attempts = list(
            c.execute(
                select(
                    heads.lifecycle.attempts.c.attempt_id, heads.lifecycle.attempts.c.owner_id
                ).where(heads.lifecycle.attempts.c.run_id == result["run_id"])
            )
        )
    assert len(attempts) == 2
    assert all(
        heads.promote(ExportAttempt(*a), ExportArtifactStore(tmp_path)) == "PUBLISHED"
        for a in attempts
    )
