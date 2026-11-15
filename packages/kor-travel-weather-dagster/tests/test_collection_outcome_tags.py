"""수집 결과의 경고 태그는 선택적 관측이며 정상 후속 작업이 지우지 않는다."""

from types import SimpleNamespace

import pytest
from kortravelweather_dagster.definitions import _record_collection_outcome

TAG = "kortravel/collection-outcome"


@pytest.mark.parametrize("previous,outcome,expected,writes", [
    (None, "success", None, 0), ("partial", "success", "partial", 0),
    ("deferred", "success", "deferred", 0), (None, "deferred", "deferred", 1),
    (None, "partial", "partial", 1), ("deferred", "partial", "partial", 1),
    ("partial", "deferred", "partial", 0), ("partial", "partial", "partial", 0),
    ("success", "deferred", "deferred", 1), (None, "failed", None, 0),
])
def test_optional_warning_tag_never_downgrades_observed_warning(
    previous, outcome, expected, writes,
):
    tags = {TAG: previous} if previous else {}
    calls = []

    def update(run_id, new_tags):
        calls.append((run_id, new_tags))
        tags.update(new_tags)

    context = SimpleNamespace(run_id="own-run", instance=SimpleNamespace(
        get_run_by_id=lambda run_id: SimpleNamespace(tags=tags), add_run_tags=update,
    ))
    _record_collection_outcome(context, outcome)
    assert tags.get(TAG) == expected and len(calls) == writes
    assert all(run_id == "own-run" and set(values) == {TAG} for run_id, values in calls)


@pytest.mark.parametrize("stage", ["read", "write", "missing_instance"])
def test_tag_storage_failure_cannot_fail_collection_or_log_private_error(stage):
    messages = []

    def fail(*args):
        raise RuntimeError("private database connection detail")

    instance = SimpleNamespace(
        get_run_by_id=fail if stage == "read" else lambda _: SimpleNamespace(tags={}),
        add_run_tags=fail,
    )
    context = SimpleNamespace(
        run_id="own-run", instance=None if stage == "missing_instance" else instance,
        log=SimpleNamespace(warning=messages.append),
    )
    _record_collection_outcome(context, "partial")
    assert messages == ([] if stage == "missing_instance" else ["COLLECTION_OUTCOME_TAG_DEFERRED"])


@pytest.mark.parametrize("status", ["success", "partial"])
def test_kma_normal_return_exposes_matching_output_outcome_without_changing_status(
    monkeypatch, status,
):
    from dagster import build_asset_context
    from kortravelweather_dagster import definitions
    from test_kma_weather import FakeClient, FakeRepository, _target

    repository, recorded = FakeRepository(), []
    monkeypatch.setattr(definitions, "_run_repository", lambda _: repository)
    monkeypatch.setattr(definitions, "_kma_grid_targets", lambda *args, **kwargs: (
        [_target()], {}, set(), None,
    ))
    monkeypatch.setattr(definitions, "run_weather_sync", lambda **kwargs: {
        "status": status, "values_loaded": 1,
    })
    monkeypatch.setattr(
        definitions, "_record_collection_outcome", lambda _, outcome: recorded.append(outcome),
    )
    resource = SimpleNamespace(create_client=lambda **kwargs: FakeClient())
    with build_asset_context(resources={
        "kma_client": resource, "weather_repository": repository,
    }) as context:
        result = definitions.kma_ultra_short_nowcast_sync(context)
    assert result["status"] == result["collection_outcome"] == status
    assert recorded == [status]


def test_regional_alert_success_calls_outcome_helper_and_preserves_result(monkeypatch):
    from dagster import build_asset_context
    from kortravelweather_dagster import definitions
    from test_kma_weather import FakeRepository

    repository, recorded = FakeRepository(), []
    repository.engine = SimpleNamespace(dispose=lambda: None)
    monkeypatch.setattr(definitions, "_run_repository", lambda _: repository)
    monkeypatch.setattr(definitions, "run_alert_event_sync", lambda **kwargs: {
        "status": "success", "events_loaded": 0,
    })
    monkeypatch.setattr(
        definitions, "_record_collection_outcome", lambda _, outcome: recorded.append(outcome),
    )
    resource = SimpleNamespace(create_data_client=lambda **kwargs: object())
    with build_asset_context(resources={
        "kma_client": resource, "weather_repository": repository,
    }) as context:
        result = definitions.kma_regional_alert_events_sync(context)
    assert result["events_loaded"] == 0 and result["collection_outcome"] == "success"
    assert recorded == ["success"]
