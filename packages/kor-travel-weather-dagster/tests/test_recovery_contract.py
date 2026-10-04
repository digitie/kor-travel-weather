"""timeout·instance 제한·worker 동시성의 실제 Dagster 경계를 확인한다."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from dagster import DagsterInstance, build_asset_context
from kortravelcommon.deadline import DeadlineExceeded
from kortravelweather_dagster import definitions


def test_abandoned_fetch_never_reuses_or_closes_the_provider(monkeypatch):
    provider = SimpleNamespace(close=lambda: calls.append("close"))
    calls = []
    monkeypatch.setattr(definitions, "skipped_when_disabled", lambda *a: None)
    monkeypatch.setattr(definitions, "_external_targets", lambda *a: [])
    monkeypatch.setattr(definitions, "create_configured_provider", lambda *a, **kw: provider)

    def timeout(**kwargs):
        calls.append(kwargs["dataset_key"])
        raise DeadlineExceeded("wedged")

    monkeypatch.setattr(definitions, "run_external_weather_sync", timeout)
    resource = SimpleNamespace(create_repository=lambda: SimpleNamespace())
    with (
        build_asset_context(resources={"weather_repository": resource}) as context,
        pytest.raises(DeadlineExceeded),
    ):
        definitions._make_external_provider_asset("open_meteo")(context)
    assert len(calls) == 1
    assert "close" not in calls


def test_actual_dagster_instance_accepts_recovery_and_slot_limits():
    path = Path(__file__).resolve().parents[3] / "deploy/dagster.yaml"
    config = yaml.safe_load(path.read_text())
    overrides = {key: config[key] for key in ("run_monitoring", "run_retries", "run_coordinator")}
    with DagsterInstance.local_temp(overrides=overrides) as instance:
        assert instance.run_monitoring_enabled
        assert instance.run_monitoring_cancel_timeout_seconds == 300
        assert instance.run_retries_enabled


def test_asset_jobs_use_a_single_step_worker():
    job = definitions.regional_weather_job
    result = job.executor_def.apply_config_mapping({"config": {}})
    assert result.success
    assert result.value["config"]["max_concurrent"] == 1
