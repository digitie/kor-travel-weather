"""External provider 수집 범위를 줄일 때 쓰는 위치 표본 추출."""

from __future__ import annotations

import math
from collections.abc import Sequence

from .base import ProviderLocation


def spatially_even_subset(
    locations: Sequence[ProviderLocation], limit: int
) -> list[ProviderLocation]:
    """Pick ``limit`` locations spread across the catalog's bounding box.

    External providers exist to compare against KMA across the country, not to
    resolve individual stations, so when a free-tier quota forces the sweep to
    shrink the useful thing to keep is *coverage*. Taking the first N by id
    would keep 55 air-quality stations clustered wherever the alphabet happens
    to put them and leave whole provinces unobserved; this keeps the sample
    spread instead.

    Deterministic: the same catalog and limit always yield the same subset, so
    a location's readings do not appear and disappear between runs.
    """
    if limit <= 0:
        return []
    if limit >= len(locations):
        return list(locations)

    lats = [location.latitude for location in locations]
    lons = [location.longitude for location in locations]
    lat_min, lon_min = min(lats), min(lons)
    lat_span = (max(lats) - lat_min) or 1e-9
    lon_span = (max(lons) - lon_min) or 1e-9

    # Proportion the grid to the bounding box so cells stay roughly square;
    # a fixed square grid over a tall country samples one axis far more
    # finely than the other and reintroduces the clustering this avoids.
    aspect = lon_span / lat_span
    rows = max(1, round(math.sqrt(limit / aspect)))
    cols = max(1, math.ceil(limit / rows))

    cells: dict[tuple[int, int], list[ProviderLocation]] = {}
    for location in locations:
        row = min(rows - 1, int((location.latitude - lat_min) / lat_span * rows))
        col = min(cols - 1, int((location.longitude - lon_min) / lon_span * cols))
        cells.setdefault((row, col), []).append(location)

    # Order each cell by distance from its centre, not by id: the id order is
    # effectively arbitrary, and taking its first entry lands on whichever
    # corner of the cell sorts first, which pulls the whole sample toward one
    # corner of the map and narrows the span it covers.
    for (row, col), bucket in cells.items():
        centre_lat = lat_min + (row + 0.5) * lat_span / rows
        centre_lon = lon_min + (col + 0.5) * lon_span / cols
        bucket.sort(
            key=lambda item: (
                (item.latitude - centre_lat) ** 2 + (item.longitude - centre_lon) ** 2,
                item.location_id,
            )
        )

    # Round-robin across cells so every occupied cell contributes its first
    # location before any cell contributes a second.
    picked: list[ProviderLocation] = []
    ordered_cells = sorted(cells)
    depth = 0
    while len(picked) < limit:
        added = False
        for cell in ordered_cells:
            bucket = cells[cell]
            if depth >= len(bucket):
                continue
            picked.append(bucket[depth])
            added = True
            if len(picked) == limit:
                return picked
        if not added:
            break
        depth += 1
    return picked
