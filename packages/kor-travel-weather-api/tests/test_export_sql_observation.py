"""서비스 export SQL 계측은 public read deadline 활성화와 독립이다."""

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
