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


# compose.yaml·.env.example의 기본 활성 목록. 뒤 4개는 export 경로가 없다(KNOWN 밖).
COMPOSE_DEFAULT = [
    "python-kma-api", "python-airkorea-api", "open_meteo", "weatherapi", "openweathermap",
    "tomorrow_io", "weatherbit", "weatherstack", "accuweather",
]


@pytest.mark.parametrize("style,dataset", [
    ("ultra_short", "kma_ultra_short_forecast"),
    ("short", "kma_short_forecast"),
    ("mid", "kma_mid_forecast"),
])
def test_kma_only_forecast_styles_ignore_enabled_providers_that_cannot_feed_them(style, dataset):
    selected = select_export_sources(COMPOSE_DEFAULT, resource="forecasts", style=style)
    assert selected.providers == ("python-kma-api",)
    assert selected.expected == (("python-kma-api", dataset),)
    assert selected.optional == ()


@pytest.mark.parametrize("resource,style,provider", [
    ("current", None, None),
    ("forecasts", "external", None),
    ("daily-summaries", None, None),
    ("forecasts", "short", "tomorrow_io"),
])
def test_unfeedable_enabled_provider_still_fails_closed_outside_kma_styles(
    resource, style, provider,
):
    with pytest.raises(ExportArtifactError):
        select_export_sources(COMPOSE_DEFAULT, resource=resource, style=style, provider=provider)


def test_kma_style_without_enabled_kma_is_not_an_empty_success():
    with pytest.raises(ExportArtifactError):
        select_export_sources(["open_meteo", "tomorrow_io"], resource="forecasts", style="short")


def test_daily_summaries_do_not_require_airkorea_which_has_no_summary_metric():
    selected = select_export_sources(KNOWN, resource="daily-summaries")
    assert "python-airkorea-api" not in selected.providers
    assert all(source[0] != "python-airkorea-api" for source in selected.expected)
    assert set(selected.expected) == set(
        select_export_sources(KNOWN, resource="current").expected
    ) - {("python-airkorea-api", "airkorea_realtime_measurement")}
    assert selected.optional == (("python-krforest-api", "krforest_dust"),)
    with pytest.raises(ExportArtifactError):
        select_export_sources(["python-airkorea-api"], resource="daily-summaries")
