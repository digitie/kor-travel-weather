"""중기 지역코드 anchor의 작은 SQL projection. SDK 지역 선택은 재구현하지 않는다."""

from sqlalchemy import func


def mid_region_expression(catalog, part):
    if part not in {"land", "temperature"}:
        raise ValueError("지원하지 않는 중기 source part입니다.")
    return func.coalesce(
        catalog.c.metadata[f"mid_{part}_region_code"].as_string(),
        catalog.c.metadata["mid_region_code"].as_string(),
    )


def mid_anchor_columns(catalog):
    return [func.left(mid_region_expression(catalog, part), 33).label(f"mid_{part}_region")
            for part in ("land", "temperature")]
