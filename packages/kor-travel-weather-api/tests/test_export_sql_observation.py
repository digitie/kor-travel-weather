"""서비스 export SQL 계측은 public read deadline 활성화와 독립이다."""

import pytest
from prometheus_client.parser import text_string_to_metric_families
from test_frozen_service_exports import HEADERS, _client, _collect

from kortravelweather.metrics import metrics_payload


def _sql_count():
    return sum(sample.value
               for family in text_string_to_metric_families(metrics_payload().decode())
               for sample in family.samples
               if sample.name == "ktw_read_stage_duration_seconds_count"
               and sample.labels.get("resource") == "current"
               and sample.labels.get("page") == "first"
               and sample.labels.get("stage") == "sql")


def test_service_export_sql_is_observed_without_public_deadline(repository, tmp_path):
    _collect(repository, tmp_path)
    before = _sql_count()
    response = _client(repository, tmp_path).get(
        "/v1/service/exports/current", params={"limit": 200}, headers=HEADERS,
    )
    assert response.status_code == 200
    assert _sql_count() > before
    assert repository.engine.pool.checkedout() == 0


@pytest.mark.parametrize("forecast", [False, True])
def test_batched_reader_preserves_full_page_and_cursor_wire_contract(
    repository, tmp_path, monkeypatch, forecast,
):
    from datetime import UTC, datetime

    from kortravelweather_api.routers import service_exports
    from test_frozen_service_exports import _collect_forecast

    from kortravelweather import export_forecasts, export_snapshots
    from kortravelweather.export_database_artifacts import export_artifact_store

    (_collect_forecast if forecast else _collect)(repository, tmp_path)
    moment = datetime.now(UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    for module in (export_forecasts, export_snapshots, service_exports):
        monkeypatch.setattr(module, "datetime", Clock)

    class Single:
        def __init__(self, store):
            self.store = store

        def __getattr__(self, name):
            if name == "read_many":
                raise AttributeError(name)
            return getattr(self.store, name)

    path = "/v1/service/exports/forecasts" if forecast else "/v1/service/exports/current"
    params = {"limit": 1, **({"style": "ultra_short"} if forecast else {})}
    baseline = _client(repository, tmp_path)
    with monkeypatch.context() as context:
        context.setattr(service_exports, "export_artifact_store",
                        lambda repo, root: Single(export_artifact_store(repo, root)))
        first = baseline.get(path, params=params, headers=HEADERS)
        assert first.status_code == 200
        cursor = first.json()["meta"]["next_cursor"]
        following = baseline.get(path, params={**params, "cursor": cursor}, headers=HEADERS)
    batched = _client(repository, tmp_path)
    result = batched.get(path, params=params, headers=HEADERS)
    assert result.status_code == first.status_code
    assert result.json() == first.json()
    assert result.headers["etag"] == first.headers["etag"]
    next_page = batched.get(path, params={**params, "cursor": cursor}, headers=HEADERS)
    assert next_page.json() == following.json()
    assert next_page.headers["etag"] == following.headers["etag"]
