"""이미 선택한 KMA 격자 집합을 HTTP 전에 선언한다. 격자/발표 선택은 SDK 소유다."""

from .export_heads import ScopeDescriptor
from .export_source_cohorts import declare_source_cohort


def declare_kma_grid_cohort(repository, *, run_id, dataset_key, grid_groups):
    style = {
        "kma_ultra_short_nowcast": "nowcast",
        "kma_ultra_short_forecast": "ultra_short",
        "kma_short_forecast": "short",
    }[dataset_key]
    descriptors = [ScopeDescriptor(
        dataset_key=dataset_key, forecast_style=style, entity_type="weather_response",
        entity_id=f"grid:{nx}:{ny}", nx=nx, ny=ny,
        location_ids=tuple(sorted(target.location.location_id for target in targets)),
        normalizer_revision="kma-v1", winner_policy_revision="kma-base-v1",
        coverage_contract=("kma-grid-nowcast-v1" if style == "nowcast"
                           else "kma-grid-forecast-v1"),
    ) for (nx, ny), targets in sorted(grid_groups.items())]
    return declare_source_cohort(
        repository, run_id=run_id, provider="python-kma-api", dataset_key=dataset_key,
        descriptors=descriptors,
    )
