"""An overlapping run whose predecessor is alive ends as a skip, not a failure.

Production saw three ``RuntimeError: 동일 provider/dataset 실행이 이미 진행
중입니다`` failures in 24h: a slow run still publishing when its next tick
fired.  Nothing is wrong in that case -- the live run is doing the work -- so
the new run records why it did nothing and finishes SUCCESS.  A predecessor
whose lease has expired is a different story and still fails (see the
repository tests).
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from dagster import build_asset_context
from kortravelweather_dagster import definitions

from kortravelweather.metrics import SYNC_OVERLAP_SKIPPED, dataset_label, provider_label
from kortravelweather.models import KST
from kortravelweather.repository import SyncRunAlreadyActive


def _overlap(provider: str, dataset: str) -> SyncRunAlreadyActive:
    return SyncRunAlreadyActive(
        provider=provider,
        dataset_key=dataset,
        active_run_id="run_live",
        heartbeat_at=datetime(2026, 10, 6, 3, 0, tzinfo=KST),
    )


def _skips(provider: str, dataset: str) -> float:
    return SYNC_OVERLAP_SKIPPED.labels(provider=provider, dataset=dataset)._value.get()


def _context():
    resource = SimpleNamespace(create_repository=lambda: SimpleNamespace())
    return build_asset_context(resources={"weather_repository": resource})


def test_a_regional_overlap_is_a_recorded_skip(monkeypatch) -> None:
    monkeypatch.setattr(definitions, "skipped_when_disabled", lambda *a: None)

    def overlapping(**_):
        raise _overlap("khoa", "beach_index")

    monkeypatch.setattr(definitions, "run_khoa_beach_index_sync", overlapping)
    labels = (provider_label("khoa"), dataset_label("beach_index"))
    before = _skips(*labels)
    resources = {
        "weather_repository": SimpleNamespace(create_repository=lambda: SimpleNamespace()),
        "khoa_client": SimpleNamespace(api_key=lambda **_: "key"),
    }
    with build_asset_context(resources=resources) as context:
        result = definitions.khoa_beach_index_sync(context)
    assert result["skipped"] is True
    assert result["reason"] == "already_running"
    assert result["active_run_id"] == "run_live"
    assert result["active_heartbeat_at"] == "2026-10-06T03:00:00+09:00"
    assert _skips(*labels) == before + 1


def test_an_external_dataset_overlap_is_skipped_not_failed(monkeypatch) -> None:
    provider = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(definitions, "skipped_when_disabled", lambda *a: None)
    monkeypatch.setattr(definitions, "_external_targets", lambda *a: [])
    monkeypatch.setattr(definitions, "create_configured_provider", lambda *a, **kw: provider)

    def overlapping(**kwargs):
        raise _overlap("open_meteo", kwargs["dataset_key"])

    monkeypatch.setattr(definitions, "run_external_weather_sync", overlapping)
    with _context() as context:
        result = definitions._make_external_provider_asset("open_meteo")(context)
    assert result["failed_datasets"] == []
    assert result["status"] == "skipped"
    # Each of the provider's datasets overlapped, and each is a skip.
    reasons = [entry["reason"] for entry in result["skipped_datasets"]]
    assert reasons and set(reasons) == {"already_running"}


def test_every_collector_asset_turns_an_overlap_into_a_skip() -> None:
    """One wrapper at the asset boundary, so a new collector cannot forget it."""
    collectors = [
        asset for asset in definitions._ASSETS if asset is not definitions.weather_retention_purge
    ]
    assert collectors
    for asset in collectors:
        compute = asset.op.compute_fn.decorated_fn
        assert getattr(compute, "skips_live_overlap", False), asset.key
