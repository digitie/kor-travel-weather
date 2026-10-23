"""지역 공유 원문은 한 번 검증하고 위치 fan-out은 bounded iterator로 수행한다."""

import tracemalloc

import pytest
from test_export_mid import _source

from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_heads import semantic_point_digest
from kortravelweather.export_mid import (
    MidScopeDescriptor,
    normalize_mid_source,
    prove_mid_source,
    prove_mid_template,
)


@pytest.mark.parametrize("part", ["land", "temperature"])
def test_streaming_matches_existing_normalizer_for_small_scope(part):
    descriptor, record = _source(part)
    expected = prove_mid_source(descriptor, record)
    actual = normalize_mid_source(descriptor, record)
    assert iter(actual) is actual
    assert [semantic_point_digest(v.model_dump(mode="json")) for v in actual] == [
        semantic_point_digest(v.model_dump(mode="json")) for v in expected.values
    ]


@pytest.mark.parametrize("part,per_location", [("land", 26), ("temperature", 16)])
def test_thousand_locations_share_one_template_with_bounded_peak_memory(part, per_location):
    small, record = _source(part)
    descriptor = MidScopeDescriptor.model_validate(
        {
            **small.model_dump(),
            "location_ids": tuple(f"loc-{i:04}" for i in range(1000)),
        }
    )
    assert len(prove_mid_template(descriptor, record).values) == per_location
    # 잘못된 사용으로 전체 WeatherValue 목록을 만드는 경로는 큰 지역에서 거절한다.
    with pytest.raises(ExportArtifactError):
        prove_mid_source(descriptor, record)
    tracemalloc.start()
    try:
        count = 0
        first = last = None
        for value in normalize_mid_source(descriptor, record):
            if first is None:
                first = value.location_id
            last = value.location_id
            count += 1
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert count == per_location * 1000
    assert (first, last) == ("loc-0000", "loc-0999")
    assert peak < 2 * 1024 * 1024
