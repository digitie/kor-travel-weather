"""모델 직렬화 앞 제외가 기존 semantic digest·원천 증거를 보존한다."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from pydantic_core import PydanticSerializationError

from kortravelweather import export_heads as heads
from kortravelweather.models import ForecastStyle, TimelineBucket, WeatherValue

DISCARDED = frozenset({
    "value_id", "source_record_key", "known_at", "collected_at",
    "payload", "observation_evidence",
})
AT = datetime(2026, 11, 19, 3, 0, 0, 123456, tzinfo=UTC)


def kma_fixture(style="nowcast", locations=("semantic-kma",)):
    category = "TMP" if style == "short" else "T1H"
    row = dict(category=category, nx=60, ny=127, baseDate="20261119", baseTime="0300")
    if style == "nowcast":
        row["obsrValue"] = "12.3"
    else:
        row.update(fcstValue="12.3", fcstDate="20261119", fcstTime="0400")
    dataset = {
        "nowcast": "kma_ultra_short_nowcast",
        "ultra_short": "kma_ultra_short_forecast",
        "short": "kma_short_forecast",
    }[style]
    descriptor = heads.ScopeDescriptor(
        dataset_key=dataset, forecast_style=style, entity_type="weather_response",
        entity_id="grid:60:127", nx=60, ny=127, location_ids=locations,
        normalizer_revision="kma-v1", winner_policy_revision="kma-base-v1",
        coverage_contract=("kma-grid-nonempty-v1" if style == "nowcast"
                           else "kma-grid-forecast-v1"),
    )
    record = dict(
        source_record_key="semantic-kma-source", provider="python-kma-api",
        dataset_key=dataset, source_entity_type="weather_response",
        source_entity_id="grid:60:127", fetched_at=AT, run_id="semantic-test",
        payload={"rows": [row], "response_metadata": {
            "base_date": "20261119", "base_time": "0300",
            "request_params": {"nx": 60, "ny": 127},
            "pagination": {"expected_total_count": 1, "received_item_count": 1,
                           "page_count": 1, "complete": True},
        }},
    )
    return descriptor, record


def model():
    descriptor, record = kma_fixture()
    return next(heads._normalize(descriptor, record, descriptor.location_ids))


def legacy(value):
    return heads.semantic_point_digest(value.model_dump(mode="json"))


@pytest.mark.parametrize("style", ["nowcast", "ultra_short", "short"])
def test_actual_kma_normalizer_shape_keeps_exact_old_digest(style):
    descriptor, record = kma_fixture(style)
    values = list(heads._normalize(descriptor, record, descriptor.location_ids))
    assert values and all(value.payload for value in values)
    for value in values:
        assert heads._semantic_model_point_digest(value) == legacy(value)


@pytest.mark.parametrize("number", [
    Decimal("0"), Decimal("-0.0000"), Decimal("12.3400"),
    Decimal("1.23445"), None,
])
def test_decimal_and_null_keep_exact_json_canonicalization(number):
    data = model().model_dump(mode="python")
    data.update(value_number=number, value_text="trace" if number is None else None)
    value = WeatherValue.model_validate(data)
    assert heads._semantic_model_point_digest(value) == legacy(value)


@pytest.mark.parametrize("offset", [-5, 0, 9])
def test_every_semantic_datetime_keeps_timezone_and_microseconds(offset):
    data = model().model_dump(mode="python")
    fields = ("issued_at", "valid_at", "valid_from", "valid_until", "observed_at", "target_at")
    for field in fields:
        data[field] = AT.astimezone(timezone(timedelta(hours=offset)))
    value = WeatherValue.model_validate(data)
    expected = legacy(WeatherValue.model_validate({**data, **dict.fromkeys(fields, AT)}))
    assert heads._semantic_model_point_digest(value) == legacy(value) == expected


MUTATIONS = {
    "location_id": "other-location", "provider": "another-provider",
    "dataset_key": "another-dataset", "weather_domain": "another-domain",
    "forecast_style": ForecastStyle.SHORT, "timeline_bucket": TimelineBucket.MID,
    "metric_key": "relative_humidity", "metric_name": "different metric",
    "source_metric_key": "another-source-key", "source_metric_name": "another-source-name",
    "value_number": Decimal("2.5"), "value_text": "different text", "unit": "K",
    "severity": "warning", "normalization_version": "kma-v2",
    **dict.fromkeys(
        ("issued_at", "valid_at", "valid_from", "valid_until", "observed_at", "target_at"),
        AT + timedelta(hours=2),
    ),
}


@pytest.mark.parametrize("field", sorted(MUTATIONS))
def test_each_semantic_field_tamper_is_detected(field):
    original = model()
    data = original.model_dump(mode="python")
    data[field] = MUTATIONS[field]
    changed = WeatherValue.model_validate(data)
    assert heads._semantic_model_point_digest(changed) == legacy(changed)
    assert heads._semantic_model_point_digest(changed) != legacy(original)


class DiscardSerializationProbe(WeatherValue):
    value_id: str = "discarded-id"
    observation_evidence: dict = {"raw": "discarded"}

    @field_serializer(*sorted(DISCARDED), when_used="json")
    def discarded_must_not_serialize(self, value):
        raise AssertionError("discarded field serializer was called")


def test_canonical_discarded_payload_is_not_serialized():
    original = model()
    expected = legacy(original)
    data = original.model_dump(mode="python")
    # Any payload는 저장할 수 있지만 JSON으로 직렬화할 수 없는 값을 넣는다.
    data["payload"] = {"must_not_convert": object()}
    canonical = WeatherValue.model_validate(data)
    assert type(canonical) is WeatherValue
    with pytest.raises(PydanticSerializationError):
        legacy(canonical)
    assert heads._semantic_model_point_digest(canonical) == expected


def test_subclass_keeps_legacy_discarded_serializer_behavior():
    probe = DiscardSerializationProbe.model_validate(model().model_dump(mode="python"))
    for digest in (legacy, heads._semantic_model_point_digest):
        with pytest.raises(PydanticSerializationError, match="discarded field"):
            digest(probe)


class AliasWeatherValue(WeatherValue):
    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)
    payload: dict = Field(default_factory=dict, serialization_alias="retained_payload_alias")


class OtherAliasModel(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)
    payload: dict = Field(default_factory=dict, serialization_alias="retained_payload_alias")


@pytest.mark.parametrize("model_type", [AliasWeatherValue, OtherAliasModel])
def test_aliased_payload_a_b_keeps_exact_legacy_identity(model_type):
    data = model().model_dump(mode="python")
    a = model_type.model_validate({**data, "payload": {"raw": "A"}})
    b = model_type.model_validate({**data, "payload": {"raw": "B"}})
    assert a.model_dump(mode="json")["retained_payload_alias"] == {"raw": "A"}
    assert heads._semantic_model_point_digest(a) == legacy(a)
    assert heads._semantic_model_point_digest(b) == legacy(b)
    assert heads._semantic_model_point_digest(a) != heads._semantic_model_point_digest(b)


def test_exclusion_is_exact_and_retrieval_metadata_is_ignored():
    assert heads._NON_SEMANTIC_POINT_FIELDS == DISCARDED
    original = model()
    data = original.model_dump(mode="python")
    data.update(payload={"different": ["raw", "response"]}, source_record_key="new-source",
                known_at=AT + timedelta(days=1), collected_at=AT + timedelta(days=2))
    changed = WeatherValue.model_validate(data)
    assert heads._semantic_model_point_digest(changed) == legacy(changed) == legacy(original)


def test_mapping_contract_keeps_unknown_semantic_keys_and_rejects_naive_time():
    point = model().model_dump(mode="json")
    point["future_semantic_field"] = {"nested": "kept"}
    assert heads.semantic_point_digest(point) != legacy(model())
    point["target_at"] = "2026-11-19T03:00:00"
    with pytest.raises(heads.ExportArtifactError, match="timezone"):
        heads.semantic_point_digest(point)


def test_source_payload_proof_is_still_sensitive():
    _, record = kma_fixture()
    changed = deepcopy(record)
    changed["payload"]["rows"][0]["obsrValue"] = "13.3"
    assert heads.source_input_digest(record) != heads.source_input_digest(changed)
