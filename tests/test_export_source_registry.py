"""공개 API가 초기 미준비 provider를 조용히 전체 정상 집합에서 빼지 않는다."""

import pytest

from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_source_registry import KNOWN, select_export_sources


def test_current_inventory_includes_every_observation_dataset_and_keeps_dust_explicit():
    selected = select_export_sources(KNOWN, resource="current")
    assert len(selected.providers) == 7
    assert len(selected.expected) == 8
    assert selected.optional == (("python-krforest-api", "krforest_dust"),)
    assert all(source[0] != "python-khoa-api" for source in selected.expected)
    assert not any("catalog" in source[1] for source in selected.expected)


def test_external_forecasts_include_khoa_and_three_http_adapters():
    selected = select_export_sources(KNOWN, resource="forecasts", style="external")
    assert len(selected.providers) == 4
    assert ("python-khoa-api", "khoa_beach_index") in selected.expected
    assert all(source[0] != "python-kma-api" for source in selected.expected)


@pytest.mark.parametrize("enabled,provider", [
    (["python-kma-api", "unknown"], None),
    (["python-kma-api"], "open_meteo"),
    (["python-khoa-api"], "python-khoa-api"),
])
def test_unknown_disabled_or_nonobserved_provider_is_not_successfully_omitted(enabled, provider):
    with pytest.raises(ExportArtifactError):
        select_export_sources(enabled, provider=provider, resource="current")
