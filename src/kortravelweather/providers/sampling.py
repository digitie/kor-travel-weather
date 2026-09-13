"""External provider 수집 범위를 줄일 때 쓰는 위치 표본 추출."""

from __future__ import annotations

from collections.abc import Sequence

from .base import ProviderLocation

#: A fixed lattice over Korea, wide enough for every catalog location plus the
#: offshore ones (the catalog spans 33.23-38.56N, 124.65-130.90E). Fixed is the
#: whole point: a lattice whose size is derived from the sample size, or from
#: the catalog's own bounding box, moves every cell whenever either changes.
_LATTICE_LAT_MIN, _LATTICE_LAT_MAX = 32.0, 40.0
_LATTICE_LON_MIN, _LATTICE_LON_MAX = 124.0, 132.0

#: 2**6 divisions per axis: cells of 13.9 km by 11.3 km at Korea's latitudes.
#: Measured against the real catalog at the caps actually shipped, that is the
#: resolution that spreads the sample best -- median distance between a pick and
#: its nearest neighbour, where an even 380 points over Korea would sit 16.2 km
#: apart:
#:
#:    cell size   cap 150   cap 350   cap 380
#:      55.6 km    14.5 km    6.9 km    6.8 km
#:      27.8 km    19.9 km    9.6 km    9.1 km
#:      13.9 km    20.5 km   10.0 km   10.0 km   <-- this one
#:       7.0 km    14.1 km    9.9 km    9.3 km
#:       3.5 km    13.6 km    7.8 km    7.5 km
#:
#: Too coarse and one cell holds many stations, so the sample stacks up inside
#: whichever cells come first; too fine and a city's worth of cells all qualify
#: before the next province's do.
_LATTICE_BITS = 6
_LATTICE_DIVISIONS = 1 << _LATTICE_BITS


def _cell(value: float, low: float, high: float) -> int:
    scaled = int((value - low) / (high - low) * _LATTICE_DIVISIONS)
    return max(0, min(_LATTICE_DIVISIONS - 1, scaled))


def _reverse_bits(value: int) -> int:
    reversed_value = 0
    for _ in range(_LATTICE_BITS):
        reversed_value = (reversed_value << 1) | (value & 1)
        value >>= 1
    return reversed_value


def _cell_rank(location: ProviderLocation) -> int:
    """Where this location's cell falls in the visiting order.

    Reversing each cell index before interleaving them is what makes a prefix
    of the order spread out. Interleaved as-is, the sequence would sweep the
    country in raster order and the first 380 entries would be a band across
    the south. Reversed, the most significant bits of the sort key are the
    *least* significant bits of the cell indices, so the order walks a coarse
    lattice over the whole country first and refines it -- every prefix is a
    roughly even covering, at whatever resolution that many points allow.

    It also separates neighbours that a raster order would keep together: two
    stations a hundred metres apart either share a cell, which the depth below
    handles, or sit in adjacent ones whose indices differ in the lowest bit --
    and after reversal that is the *highest* bit of the key, putting them in
    opposite halves of the order.
    """
    row = _reverse_bits(_cell(location.latitude, _LATTICE_LAT_MIN, _LATTICE_LAT_MAX))
    column = _reverse_bits(_cell(location.longitude, _LATTICE_LON_MIN, _LATTICE_LON_MAX))
    interleaved = 0
    for bit in range(_LATTICE_BITS):
        interleaved |= ((row >> bit) & 1) << (2 * bit + 1)
        interleaved |= ((column >> bit) & 1) << (2 * bit)
    return interleaved


def spatially_even_subset(
    locations: Sequence[ProviderLocation], limit: int
) -> list[ProviderLocation]:
    """Pick ``limit`` locations spread across the catalog.

    External providers exist to compare against KMA across the country, not to
    resolve individual stations, so when a free-tier quota forces the sweep to
    shrink the useful thing to keep is *coverage*. Taking the first N by id
    would keep 55 air-quality stations clustered wherever the alphabet happens
    to put them and leave whole provinces unobserved; this keeps the sample
    spread instead.

    Each location's position in the order depends on nothing but its own
    coordinates and id, and the subset is the order's first ``limit`` entries.
    Both properties matter, because a location that leaves the sample stops
    publishing with nothing in the data to say it was sampled out, which
    downstream is indistinguishable from a station that broke:

    * Independent of ``limit``, so raising a cap only ever adds. Sizing a grid
      from the limit -- the first version of this -- reshaped every cell when
      the number moved, and taking Open-Meteo from 300 to 380 locations kept
      only 140 of the original 300.
    * Independent of every location outside its own cell, so an upstream
      station appearing or disappearing moves almost nothing. A greedy
      farthest-point order, which was the second version, spreads a sample
      better than this -- 13.5 km between neighbours against this 10.0 km, at
      380 locations -- but it is global, and that costs far more than it buys:
      removing one sampled station dropped 160 of the 380, because every pick
      after it is re-derived. AirKorea builds ``location_id`` from a hash of the
      station's name and address, so an upstream address edit alone is a
      removal plus an addition, and new sources arrive in bulk.

    The catalog is clustered around cities while an even sample is not, so no
    limit-independent order can match the greedy one's spacing. Pinning the
    sample would -- storing each location's rank once and only ever appending --
    and that is the way to improve on this, not a cleverer function of the
    coordinates.
    """
    if limit <= 0:
        return []

    cells: dict[int, list[ProviderLocation]] = {}
    for location in locations:
        cells.setdefault(_cell_rank(location), []).append(location)

    # Depth first, then cell: every occupied cell contributes one location
    # before any cell contributes a second. Without it, the two stations that
    # share a cell sit next to each other in the order, and on the real catalog
    # that meant sampling an exactly co-located pair -- one wasted request out
    # of a quota this whole module exists to ration. Depth stays local to the
    # cell, so a station appearing elsewhere in the country does not move it.
    ranked = sorted(
        (
            (depth, cell, location.location_id, location)
            for cell, bucket in cells.items()
            for depth, location in enumerate(
                sorted(bucket, key=lambda item: item.location_id)
            )
        ),
        key=lambda entry: entry[:3],
    )
    ordered = [entry[3] for entry in ranked]
    if limit >= len(ordered):
        return ordered
    return ordered[:limit]
