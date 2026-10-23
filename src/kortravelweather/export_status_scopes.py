"""기존 provider catalog에서 상태 export의 데이터셋 범위를 선택한다."""

from __future__ import annotations

from functools import cache

from .export_status import SourceKey

_KMA_STYLES = {
    "kma_ultra_short_nowcast": "nowcast",
    "kma_ultra_short_forecast": "ultra_short",
    "kma_short_forecast": "short",
    "kma_mid_forecast": "mid",
}


@cache
def _all_scopes() -> tuple[SourceKey, ...]:
    # providers.factory가 repository를 쓰므로 모듈 정의 시 import하지 않는다.
    from .providers.catalog import PROVIDER_CATALOG

    return tuple(sorted(
        SourceKey(
            provider.key, dataset.key,
            _KMA_STYLES.get(dataset.key, "external" if dataset.forecast else "observed"),
        )
        for provider in PROVIDER_CATALOG
        for dataset in provider.datasets
        if dataset.key != "airkorea_station_catalog"
    ))


@cache
def _by_dataset() -> dict[tuple[str, str], SourceKey]:
    return {(key.provider, key.dataset_key): key for key in _all_scopes()}


def status_scopes(providers: set[str] | None = None) -> tuple[SourceKey, ...]:
    """참조 카탈로그/특보는 제외하고 현재·예보 데이터셋만 명시한다."""
    return tuple(key for key in _all_scopes() if providers is None or key.provider in providers)


def dataset_scopes(provider: str, datasets: set[str]) -> tuple[SourceKey, ...]:
    return tuple(sorted(
        key for dataset in datasets
        if (key := _by_dataset().get((provider, dataset))) is not None
    ))
