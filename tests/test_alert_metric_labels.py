from kortravelweather.alert_events import ALERT_DATASET
from kortravelweather.metrics import dataset_label, route_label


def test_regional_alert_telemetry_keeps_fixed_dataset_and_route_labels():
    assert dataset_label(ALERT_DATASET) == ALERT_DATASET
    assert route_label("/v1/weather/alert-events") == "/v1/weather/alert-events"
    assert dataset_label("untrusted-" + "x" * 200) == "other"


def test_cleanup_stages_and_cancellation_have_bounded_meaningful_labels():
    from kortravelweather import metrics
    from kortravelweather.alert_events import ALERT_PROVIDER

    before = metrics.ALERT_CLEANUP_FAILURES.labels(stage="other")._value.get()
    metrics.observe_alert_cleanup_failure("fixture-injected-stage/credential")
    assert metrics.ALERT_CLEANUP_FAILURES.labels(stage="other")._value.get() == before + 1
    finished = metrics.SYNC_FINISHED.labels(
        provider=ALERT_PROVIDER,
        dataset=ALERT_DATASET,
        status="cancelled",
    )
    old = finished._value.get()
    metrics.observe_sync_finished(ALERT_PROVIDER, ALERT_DATASET, status="cancelled")
    assert finished._value.get() == old + 1
    assert "fixture-injected-stage" not in metrics.metrics_payload().decode()
