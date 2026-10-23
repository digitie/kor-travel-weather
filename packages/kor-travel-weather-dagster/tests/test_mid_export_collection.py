"""실제 공개 SDK를 모의 HTTP로 실행하고 기존 native runner의 저장 경계를 확인한다."""

import copy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from kma import DataGoKrClient
from kortravelweather_dagster import kma_weather, mid_export
from kortravelweather_dagster.kma_weather import WeatherTarget, run_weather_sync

from kortravelweather import export_mid_collector
from kortravelweather.export_mid import prove_mid_template
from kortravelweather.models import WeatherLocation

AT = datetime(2026, 10, 23, 9, 15, tzinfo=UTC)


class Repository:
    def __init__(self):
        self.values, self.sources, self.start_kwargs, self.batches = [], [], [], []
        self.run = SimpleNamespace(run_id="mid-run", status="running")

    def start_sync_run(self, **kwargs):
        self.start_kwargs.append(kwargs)
        return self.run

    def ingest_batch(self, *, source_records, values):
        self.values.extend(values)
        self.sources.extend(source_records)
        self.batches.append((len({v.location_id for v in values}), len(values)))
        return len(values)

    def finish_sync_run(self, run_id, **kwargs):
        assert run_id == self.run.run_id
        self.run.status = kwargs["status"]
        self.finished = kwargs
        return self.run


class NoGridClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def now(self, **_):
        raise AssertionError("중기 전용 job은 격자 API를 호출하지 않습니다.")


def targets(count=3):
    return [
        WeatherTarget(
            WeatherLocation(
                location_id=f"loc-{i:04}",
                name="관측소",
                latitude=37.5,
                longitude=127,
                nx=60,
                ny=127,
            ),
            mid_land_region_code="11B00000",
            mid_temperature_region_code=f"11B1010{i % 2}",
        )
        for i in range(count)
    ]


def _raw(part, region):
    result = {"regId": region}
    if part == "land":
        for day in range(3, 11):
            for suffix in ("Am", "Pm") if day <= 7 else ("",):
                result[f"wf{day}{suffix}"] = "맑음"
                result[f"rnSt{day}{suffix}"] = 10
    else:
        for day in range(3, 11):
            result[f"taMin{day}"] = 5
            result[f"taMax{day}"] = 20
    return result


@pytest.fixture
def setup(monkeypatch):
    events = []
    monkeypatch.setattr(mid_export, "kst_now", lambda: AT)
    monkeypatch.setattr(kma_weather, "kst_now", lambda: AT)

    def cohort(repository, run_id, descriptors):
        events.append(("cohort", len(descriptors)))
        assert len({d.source_scope for d in descriptors}) == len(descriptors)

    class Collector:
        def __init__(self, repository, **_):
            self.repository = repository

        def prepare(self, *, descriptor):
            events.append(("prepare", descriptor.entity_id))
            return descriptor

        def ingest_fanout(self, descriptor, *, record, values, location_ids, heartbeat):
            assert prove_mid_template(descriptor, record).revision == AT.replace(minute=0)
            assert len(descriptor.location_ids) == len(location_ids)
            return mid_export._native_fanout(
                self.repository,
                record,
                values,
                location_ids,
                heartbeat,
            )

    monkeypatch.setattr(mid_export, "_declare_cohort", cohort)
    monkeypatch.setattr(export_mid_collector, "MidExportCollector", Collector)
    return events


def execute(
    events,
    *,
    count=3,
    fail=False,
    max_values=500000,
    max_response_rows=1000,
    empty_first=False,
    empty_always=False,
):
    repository = Repository()

    def handler(request):
        assert request.url.params["tmFc"] == "202610231800"
        part = "land" if request.url.path.endswith("getMidLandFcst") else "temperature"
        region = request.url.params["regId"]
        events.append(("request", part, region))
        if fail and part == "temperature":
            raise httpx.ConnectError("offline fixture", request=request)
        first_land = len([e for e in events if e[0] == "request" and e[1] == "land"]) == 1
        items = (
            []
            if part == "land" and (empty_always or (empty_first and first_land))
            else [_raw(part, region)]
        )
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                    "body": {
                        "items": {"item": items},
                        "totalCount": len(items),
                        "pageNo": 1,
                        "numOfRows": 10,
                    },
                }
            },
        )

    session = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    data = DataGoKrClient("offline", session=session, retries=0)
    try:
        result = run_weather_sync(
            repository=repository,
            client=NoGridClient(),
            data_client=data,
            targets=targets(count),
            include_base=False,
            include_mid=True,
            export_artifact_root=Path("/tmp/mid-offline"),
            max_values=max_values,
            max_response_rows=max_response_rows,
        )
    finally:
        # SDK가 외부에서 받은 session 소유권은 호출자에게 둔다.
        import asyncio

        asyncio.run(session.aclose())
    return repository, result


def test_unique_regions_all_planned_before_http_and_same_sdk_issue(setup):
    repository, result = execute(setup)
    assert setup[:4] == [
        ("cohort", 3),
        ("prepare", "mid-land:11B00000"),
        ("prepare", "mid-temperature:11B10100"),
        ("prepare", "mid-temperature:11B10101"),
    ]
    assert len([e for e in setup if e[0] == "request"]) == 3
    assert result["requests_fetched"] == 3
    assert result["mid_groups_fetched"] == 2
    assert result["values_loaded"] == len(repository.values) == 42 * 3
    assert repository.start_kwargs[0]["export_cohort_required"] is True
    assert repository.start_kwargs[0]["dataset_key"] == "kma_mid_forecast"
    assert repository.run.status == "success"


def test_cohort_failure_preserves_native_bounded_store(setup, monkeypatch):
    def failed(*_):
        raise RuntimeError("injected cohort write failure")

    monkeypatch.setattr(mid_export, "_declare_cohort", failed)
    repository, result = execute(setup, count=120)
    assert result["values_loaded"] == 42 * 120
    assert not any(e[0] == "prepare" for e in setup)
    assert max(locations for locations, _ in repository.batches) <= 50
    assert max(values for _, values in repository.batches) <= 2500


def test_large_shared_region_keeps_one_claim_and_small_batches(setup):
    repository, result = execute(setup, count=120)
    assert len([e for e in setup if e[0] == "prepare"]) == 3
    assert result["values_loaded"] == 42 * 120
    assert max(locations for locations, _ in repository.batches) <= 50
    assert max(values for _, values in repository.batches) <= 2500


@pytest.mark.parametrize("budget", [{"max_values": 10}, {"max_response_rows": 1}])
def test_global_budgets_stop_before_unbounded_fanout(setup, budget):
    with pytest.raises(ValueError):
        execute(setup, **budget)


def test_duplicate_location_role_rejected_before_first_http(setup):
    selected = targets(1)
    with pytest.raises(ValueError):
        mid_export.mid_descriptors(selected + copy.deepcopy(selected))
    assert not setup


def test_one_empty_success_is_reconfirmed_with_same_issue_and_plan(setup):
    repository, result = execute(setup, empty_first=True)
    assert result["requests_fetched"] == 4
    assert result["values_loaded"] == 126 and repository.run.status == "success"
    assert len([e for e in setup if e[0] == "prepare"]) == 3


def test_persistent_empty_success_stops_after_one_reconfirmation(setup):
    with pytest.raises(ValueError, match="재확인"):
        execute(setup, empty_always=True)
    assert len([e for e in setup if e[0] == "request"]) == 2


def test_export_descriptor_limit_does_not_block_larger_native_region(setup):
    repository, result = execute(setup, count=1001)
    assert result["values_loaded"] == 42 * 1001
    assert not any(e[0] in {"cohort", "prepare"} for e in setup)
    assert max(locations for locations, _ in repository.batches) <= 50
    assert max(values for _, values in repository.batches) <= 2500
