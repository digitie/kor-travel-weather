"""Map에 공개할 실제 dataset 범위. 활성 위치와 provider의 곱을 만들지 않는다."""

from dataclasses import dataclass

from .export_artifacts import ExportArtifactError
from .export_external import ADAPTERS

CURRENT = {
    "python-kma-api": ("kma_ultra_short_nowcast",),
    "python-airkorea-api": ("airkorea_realtime_measurement",),
    "python-krforest-api": ("krforest_mountain_weather", "krforest_dust"),
    "python-krex-api": ("krex_restarea_weather",),
    **{key: (f"{key}_current",) for key in ADAPTERS},
}
# W5 요약은 기온·습도·풍속·강수만 쓴다. AirKorea 측정값(PM·O3·NO2·SO2·CO·KHAI)은
# 요약 지표가 없으므로 W5 필수 원천이 아니다. W2 current에는 그대로 남는다.
DAILY = {key: value for key, value in CURRENT.items() if key != "python-airkorea-api"}
FORECASTS = {
    "ultra_short": {"python-kma-api": ("kma_ultra_short_forecast",)},
    "short": {"python-kma-api": ("kma_short_forecast",)},
    "mid": {"python-kma-api": ("kma_mid_forecast",)},
    "external": {
        "python-khoa-api": ("khoa_beach_index",),
        **{key: (f"{key}_forecast",) for key in ADAPTERS},
    },
}
KNOWN = set(CURRENT) | set(FORECASTS["external"])
# 이 style은 python-kma-api만 공급한다. 다른 활성 provider는 처음부터 범위 밖이다.
KMA_FORECAST_STYLES = frozenset({"ultra_short", "short", "mid"})


@dataclass(frozen=True)
class ExportSourceSelection:
    providers: tuple[str, ...]
    expected: tuple[tuple[str, str], ...]
    optional: tuple[tuple[str, str], ...]


def select_export_sources(enabled, *, resource, style=None, provider=None):
    """명시 비활성화만 범위에서 뺀다. 지원하지 않는 활성 source는503으로 드러낸다.

    KMA 전용 예보 style(ultra_short·short·mid)은 그 style을 공급할 수 없는 활성
    provider를 먼저 제외한다. current·external·daily-summaries는 export 경로가 없는
    활성 provider가 있으면 계속 fail-closed다.
    """
    enabled = set(enabled)
    requested = {provider} if provider else enabled
    if not provider and resource == "forecasts" and style in KMA_FORECAST_STYLES:
        requested &= set(FORECASTS[style])
    if not requested or not requested.issubset(KNOWN):
        raise ExportArtifactError("요청 provider의 전체 source 계약이 준비되지 않았습니다.")
    if provider and provider not in enabled:
        raise ExportArtifactError("비활성 provider를 정상 빈 집합으로 제공하지 않습니다.")
    if resource == "forecasts":
        mapping = FORECASTS.get(style, {})
    else:
        mapping = {"current": CURRENT, "daily-summaries": DAILY}.get(resource, {})
    providers = requested.intersection(mapping)
    if provider and provider not in providers or not providers:
        raise ExportArtifactError("해당 provider/style에 공개할 dataset이 없습니다.")
    expected = tuple(sorted((key, dataset) for key in providers for dataset in mapping[key]))
    # 별도 catalog 승인 권한이 필요한 청정넷 최초 미준비는 degraded/stale로 보인다.
    # 이전 anchor/승인은 계속 expected에 남고 실패를 정상0건으로 꾸미지 않는다.
    optional = tuple(source for source in expected if source[1] == "krforest_dust")
    return ExportSourceSelection(tuple(sorted(providers)), expected, optional)
