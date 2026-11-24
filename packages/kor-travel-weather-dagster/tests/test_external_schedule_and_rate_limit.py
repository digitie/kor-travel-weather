"""외부 provider: sweep하지 않는 provider는 schedule이 없고, 429는 다음 주기로 넘긴다.

수집 실패 감사 §2.5 P1 #12·P2, §4 #12: 키 없는 외부 job 여섯 개가 3시간마다 3~4초 skip run을
만들었고(14일 약 635 run), 429는 ``Retry-After``를 무시하고 같은 요청을 세 번 더 보냈다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from dagster import build_asset_context
from kortravelweather_dagster import definitions
from kortravelweather_dagster.definitions import (
    EXTERNAL_COLLECTIONS,
    SCHEDULED_EXTERNAL_COLLECTIONS,
    SLOW_EXTERNAL_PROVIDER_KEYS,
    defs,
    external_weather_jobs,
    external_weather_schedules,
)

from kortravelweather.providers import ProviderError

UNSCHEDULED = {
    "visual_crossing", "tomorrow_io", "weatherbit", "weatherstack", "accuweather", "wttr_in",
}


def test_only_sweeping_providers_have_schedules() -> None:
    scheduled = {collection.provider_key for collection in SCHEDULED_EXTERNAL_COLLECTIONS}
    assert scheduled == SLOW_EXTERNAL_PROVIDER_KEYS
    assert {schedule.job_name for schedule in external_weather_schedules} == {
        collection.job_name for collection in SCHEDULED_EXTERNAL_COLLECTIONS
    }
    repository = defs.get_repository_def()
    names = {schedule.job_name for schedule in repository.schedule_defs}
    for collection in EXTERNAL_COLLECTIONS:
        # job은 남겨 관리자 수동 실행은 그대로 가능하다.
        assert repository.has_job(collection.job_name)
        assert collection.slug in external_weather_jobs
        if collection.provider_key in UNSCHEDULED:
            assert collection.job_name not in names, collection.job_name
    providers = {collection.provider_key for collection in EXTERNAL_COLLECTIONS}
    assert providers - SLOW_EXTERNAL_PROVIDER_KEYS == UNSCHEDULED


def test_wttr_in_needs_no_key_but_stays_disabled_by_default() -> None:
    from kortravelweather.providers import provider_spec
    from kortravelweather.settings import WeatherSettings

    assert provider_spec("wttr_in").auth_required is False
    settings = WeatherSettings(_env_file=None, environment="development")
    assert "wttr_in" not in settings.enabled_providers


class _Outcome:
    def __init__(self) -> None:
        self.tags: dict[str, str] = {}

    def get_run_by_id(self, _run_id):
        return SimpleNamespace(tags=dict(self.tags))

    def add_run_tags(self, _run_id, tags):
        self.tags.update(tags)


def _rate_limited(*, retry_after=None) -> ProviderError:
    return ProviderError(
        "provider rate limit입니다.", code="rate_limit", retryable=True, status_code=429,
        retry_after_seconds=retry_after,
    )


def _run_asset(monkeypatch, collection, sync):
    provider = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(definitions, "skipped_when_disabled", lambda *a: None)
    monkeypatch.setattr(definitions, "_external_targets", lambda *a: [])
    monkeypatch.setattr(definitions, "create_configured_provider", lambda *a, **kw: provider)
    monkeypatch.setattr(definitions, "run_external_weather_sync", sync)
    outcome = _Outcome()
    monkeypatch.setattr(
        definitions, "_record_collection_outcome",
        lambda _context, value: outcome.add_run_tags(None, {
            "kortravel/collection-outcome": value,
        }),
    )
    resource = SimpleNamespace(create_repository=lambda: SimpleNamespace())
    with build_asset_context(resources={"weather_repository": resource}) as context:
        result = definitions._make_external_provider_asset(collection)(context)
    return result, outcome


def test_rate_limit_defers_the_rest_of_the_cycle_instead_of_failing(monkeypatch) -> None:
    calls = []

    def sync(**kwargs):
        calls.append(kwargs["dataset_key"])
        raise _rate_limited(retry_after=3600)

    # 분리하지 않은 provider(두 dataset): 첫 dataset의 429 뒤 둘째는 묻지 않는다.
    result, outcome = _run_asset(monkeypatch, definitions.ExternalCollection("open_meteo"), sync)
    assert len(calls) == 1
    assert result["status"] == "deferred"
    assert result["failed_datasets"] == []
    assert result["rate_limited_datasets"] == [{
        "provider": "open_meteo", "dataset_key": calls[0], "retry_after_seconds": 3600,
    }]
    assert [entry["reason"] for entry in result["skipped_datasets"]] == ["rate_limited"]
    assert outcome.tags == {"kortravel/collection-outcome": "deferred"}


def test_other_provider_errors_still_fail_the_run(monkeypatch) -> None:
    def sync(**kwargs):
        raise ProviderError("provider server 오류입니다.", code="server", retryable=True)

    with pytest.raises(RuntimeError, match="모든 dataset 수집이 실패"):
        _run_asset(
            monkeypatch, definitions.ExternalCollection("openweathermap", "current"), sync
        )


def test_partial_success_is_tagged_partial(monkeypatch) -> None:
    seen = []

    def sync(**kwargs):
        seen.append(kwargs["dataset_key"])
        if len(seen) == 1:
            return {"dataset_key": kwargs["dataset_key"], "status": "success"}
        raise ProviderError("provider client 오류입니다.", code="client", status_code=400)

    result, outcome = _run_asset(monkeypatch, definitions.ExternalCollection("open_meteo"), sync)
    assert result["status"] == "partial"
    assert outcome.tags == {"kortravel/collection-outcome": "partial"}
