"""KMA replay template의 순서·집계·독립 capture 증명을 DB 없이 검증한다."""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import MetaData

from kortravelweather import export_heads
from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_heads import ScopeDescriptor, _normalize, semantic_point_digest
from kortravelweather.models import KST, WeatherValue
from kortravelweather.providers import kma


@pytest.fixture(autouse=True)
def clean_postgresql_database():
    """순수 정규화 시험은 저장소의 공통 PostgreSQL fixture를 사용하지 않는다."""


def descriptor(style="short", count=48):
    return ScopeDescriptor(
        dataset_key={
            "short": "kma_short_forecast",
            "ultra_short": "kma_ultra_short_forecast",
            "nowcast": "kma_ultra_short_nowcast",
        }[style],
        entity_type="grid",
        entity_id="55:81",
        forecast_style=style,
        nx=55,
        ny=81,
        location_ids=tuple(f"loc-{i:03d}" for i in range(count)),
        normalizer_revision="kma-v1",
        winner_policy_revision="kma-base-v1",
        coverage_contract=(
            "kma-grid-nowcast-v1" if style == "nowcast" else "kma-grid-forecast-v1"
        ),
    )


def record(style="short", hours=1):
    at = datetime(2026, 11, 19, 23, tzinfo=KST)
    metrics = {
        "TMP": "10.25", "REH": "65", "WSD": "2.5", "VEC": "180",
        "PCP": "0", "SNO": "0", "PTY": "0", "SKY": "1",
        "POP": "20", "WAV": "0.5", "UUU": "1", "VVV": "-1",
    }
    rows = []
    for hour in range(hours):
        target = at + timedelta(hours=hour + 1)
        for metric, value in metrics.items():
            if style == "nowcast":
                rows.append({
                    "baseDate": "20261119", "baseTime": "2300", "nx": 55, "ny": 81,
                    "category": metric, "obsrValue": value,
                })
            else:
                rows.append({
                    "baseDate": "20261119", "baseTime": "2300", "nx": 55, "ny": 81,
                    "fcstDate": target.strftime("%Y%m%d"),
                    "fcstTime": target.strftime("%H%M"),
                    "category": metric, "fcstValue": value,
                })
    return {
        "source_record_key": "synthetic-source-anchor",
        "provider": "python-kma-api",
        "dataset_key": descriptor(style).dataset_key,
        "source_entity_type": "grid",
        "source_entity_id": "55:81",
        "fetched_at": at,
        "payload": {"rows": rows},
    }


def normalizer(style):
    return {
        "short": kma.short_forecast_to_weather_values,
        "ultra_short": kma.ultra_short_forecast_to_weather_values,
        "nowcast": kma.ultra_short_nowcast_to_weather_values,
    }[style]


def naive(desc, source, locations, skipped=None):
    for location in locations:
        yield from normalizer(desc.forecast_style)(
            source["payload"]["rows"],
            location_id=location,
            source_record_key=source["source_record_key"],
            known_at=source["fetched_at"],
            skipped=skipped,
        )


def comparable(values):
    # 독립 replay 호출은 원래 collected_at 생성 시각이 서로 다르다.
    return [v.model_dump(mode="json", exclude={"collected_at"}) for v in values]


@pytest.mark.parametrize("style", ["short", "ultra_short", "nowcast"])
def test_same_input_matches_naive_all_semantic_fields_and_types(style):
    desc, source = descriptor(style), record(style)
    # 동일 grid/time의 중복 행도 삭제하거나 재정렬하지 않는다.
    source["payload"]["rows"].append(deepcopy(source["payload"]["rows"][0]))
    locations = [desc.location_ids[3], desc.location_ids[0], desc.location_ids[3]]
    actual = list(_normalize(desc, source, iter(locations)))
    expected = list(naive(desc, source, locations))
    assert comparable(actual) == comparable(expected)
    assert [semantic_point_digest(v.model_dump(mode="json")) for v in actual] == [
        semantic_point_digest(v.model_dump(mode="json")) for v in expected
    ]
    assert all(type(v) is WeatherValue for v in actual)
    assert all(isinstance(v.value_number, Decimal) for v in actual)
    assert all(v.target_at.tzinfo is not None for v in actual)
    assert all(v.source_record_key == source["source_record_key"] for v in actual)
    assert source == record_with_duplicate(style)


def record_with_duplicate(style):
    source = record(style)
    source["payload"]["rows"].append(deepcopy(source["payload"]["rows"][0]))
    return source


@pytest.mark.parametrize("style", ["short", "ultra_short", "nowcast"])
@pytest.mark.parametrize("all_invalid", [False, True])
def test_skipped_counts_match_naive_per_location_even_empty_template(style, all_invalid):
    desc, source = descriptor(style), record(style)
    value_key = "obsrValue" if style == "nowcast" else "fcstValue"
    source["payload"]["rows"] = [
        dict(source["payload"]["rows"][0], **{value_key: "-999"}),
        dict(source["payload"]["rows"][1], **{value_key: "101"}),  # REH 범위 오류
    ] + ([] if all_invalid else [source["payload"]["rows"][2]])
    expected_skips = Counter({"prior": 7})
    actual_skips = expected_skips.copy()
    expected = list(naive(desc, source, desc.location_ids, expected_skips))
    actual = list(_normalize(desc, source, iter(desc.location_ids), skipped=actual_skips))
    assert comparable(actual) == comparable(expected)
    assert actual_skips == expected_skips
    assert actual_skips[f"{desc.dataset_key}:missing:TMP"] == 48
    assert actual_skips[f"{desc.dataset_key}:invalid:REH"] == 48
    assert bool(actual) is not all_invalid


@pytest.mark.parametrize("style", ["short", "ultra_short", "nowcast"])
def test_normalizes_once_for_48_locations_and_empty_template(monkeypatch, style):
    desc, source = descriptor(style), record(style)
    value_key = "obsrValue" if style == "nowcast" else "fcstValue"
    for row in source["payload"]["rows"]:
        row[value_key] = "-999"
    function_name = {
        "short": "short_forecast_to_weather_values",
        "ultra_short": "ultra_short_forecast_to_weather_values",
        "nowcast": "ultra_short_nowcast_to_weather_values",
    }[style]
    original = getattr(kma, function_name)
    calls = []

    def tracked(*args, **kwargs):
        calls.append(kwargs["location_id"])
        return original(*args, **kwargs)

    monkeypatch.setattr(kma, function_name, tracked)
    skipped = Counter()
    assert list(_normalize(desc, source, iter(desc.location_ids), skipped=skipped)) == []
    assert calls == [desc.location_ids[0]]
    assert skipped.total() == 12 * 48


def test_empty_locations_do_not_read_or_normalize_invalid_source():
    assert list(_normalize(descriptor(), {}, iter(()), skipped=Counter())) == []


def test_locations_are_consumed_lazily_in_order():
    desc, source = descriptor(), record()
    events = []
    def locations():
        events.append("first")
        yield "loc-003"
        events.append("second")
        yield "loc-001"
    values = _normalize(desc, source, locations())
    assert events == []
    assert next(values).location_id == "loc-003"
    assert events == ["first"]
    for _ in range(11):
        assert next(values).location_id == "loc-003"
    assert events == ["first"]
    assert next(values).location_id == "loc-001"
    assert events == ["first", "second"]


@pytest.mark.parametrize("fault", ["category", "date", "grid_type", "value_type"])
def test_invalid_source_exception_and_partial_skip_counts_match_naive(fault):
    desc, source = descriptor(), record()
    source["payload"]["rows"][0]["fcstValue"] = "-999"
    row = source["payload"]["rows"][2]
    row.update({
        "category": {"category": "UNSUPPORTED"},
        "date": {"fcstTime": "2561"},
        "grid_type": {"nx": "not-a-grid"},
        "value_type": {"fcstValue": None},
    }[fault])
    errors, counters = [], []
    for function in (naive, _normalize):
        skipped = Counter({"prior": 7})
        with pytest.raises((ValueError, TypeError)) as error:
            list(function(desc, source, desc.location_ids, skipped=skipped))
        errors.append((type(error.value), str(error.value)))
        counters.append(skipped)
    assert errors[0] == errors[1]
    assert counters[0] == counters[1]


def test_changed_source_is_not_reused_across_invocations():
    desc, source = descriptor(), record()
    first = list(_normalize(desc, source, desc.location_ids[:2]))
    source["payload"]["rows"][0]["fcstValue"] = "11.5"
    second = list(_normalize(desc, source, desc.location_ids[:2]))
    assert first[0].value_number == Decimal("10.25")
    assert second[0].value_number == Decimal("11.5")
    # 원본과 fanout fact의 payload는 읽기용이며 다른 invocation으로 전달되지 않는다.
    assert first[0].payload is not second[0].payload


class PlanSession:
    def __init__(self, plan):
        self.plan = plan

    def execute(self, _statement):
        return self

    def mappings(self):
        return self

    def one_or_none(self):
        return self.plan


def test_actual_two_location_chunk_independent_replay_rejects_semantic_tamper():
    desc, source = descriptor(), record(hours=80)
    locations = desc.location_ids[:2]
    # wanted는 최적화 replay와 별개인 기존 공개 normalizer로 만든다.
    values = list(naive(desc, source, locations))
    assert len(source["payload"]["rows"]) == 960
    assert len(values) == 1920 < 2500
    plan = {
        "descriptor": desc.model_dump(mode="json"),
        "data_revision_at": source["fetched_at"],
        "source_input_digest": export_heads.source_input_digest(source),
        "source_collected_at": source["fetched_at"],
    }
    attempt = SimpleNamespace(attempt_id="synthetic-attempt")
    observed = {source["source_record_key"]: source["fetched_at"]}

    def validate(wanted, records):
        export_heads.validate_head_capture(
            PlanSession(plan), MetaData(), attempt, records, wanted, observed
        )

    validate(values, [source])
    tampered = [values[0].model_copy(update={"value_number": Decimal("11.5")}), *values[1:]]
    with pytest.raises(ExportArtifactError):
        validate(tampered, [source])
    with pytest.raises(ExportArtifactError):
        validate([values[0], values[0]], [source])
    changed_source = deepcopy(source)
    changed_source["payload"]["rows"][0]["fcstValue"] = "11.5"
    with pytest.raises(ExportArtifactError):
        validate(values, [changed_source])


def test_valid_48_location_fanout_matches_naive_and_normalizes_once(monkeypatch):
    desc, source = descriptor(), record()
    expected = list(naive(desc, source, desc.location_ids))
    original = kma.short_forecast_to_weather_values
    calls = []

    def tracked(*args, **kwargs):
        calls.append(kwargs["location_id"])
        return original(*args, **kwargs)

    monkeypatch.setattr(kma, "short_forecast_to_weather_values", tracked)
    actual = list(_normalize(desc, source, iter(desc.location_ids)))
    assert calls == [desc.location_ids[0]]
    assert len(actual) == 48 * 12
    assert comparable(actual) == comparable(expected)
