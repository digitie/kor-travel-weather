"""Publish staged facts in short, whole-location transactions.

``WeatherRepository.ingest_batch`` holds the advisory lock of every location
in its batch until it commits.  A writer that publishes many locations in one
transaction therefore makes every other writer of any of them wait for as long
as the whole insert takes: an alerts run held 1,450 locations for over an hour
on 2026-10-01, a short-forecast run held 1,103 for 4h03m on 2026-10-02.  Every
collector publishes through these chunks instead, so no transaction holds more
than ``PUBLISH_CHUNK_LOCATIONS`` locations or ``PUBLISH_CHUNK_VALUES`` facts.

Each chunk carries exactly the source records its facts cite.  Those records
carry the run id, so each transaction locks the run row and re-checks that the
run still owns its lease before it publishes anything
(``_ingest_batch_session``); a chunk citing none would skip that check and is
refused.  A source cited by two chunks goes with both -- the second is an
idempotent replay.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from kortravelweather.models import WeatherValue

#: Most locations one publish transaction holds.  Every ingest waits at most
#: ``3 x deadlock_timeout`` per attempt for a lock (``INGEST_LOCK_ATTEMPTS``),
#: so a chunk has to finish well inside that for writers that share a location
#: to take turns instead of failing.
PUBLISH_CHUNK_LOCATIONS = 50

#: Most facts one publish transaction carries; a location's facts never split.
#: A short-forecast location carries about 900 facts and an open_meteo forecast
#: location about 1,350, so a chunk is a handful of locations for those and
#: the location cap binds for observation-style collectors.
PUBLISH_CHUNK_VALUES = 5_000


def location_chunks(
    values: Sequence[WeatherValue], *, max_locations: int, max_values: int
) -> Iterator[list[WeatherValue]]:
    """Split facts into whole-location chunks, in first-appearance order.

    Collectors stage facts response by response, so first-appearance order
    keeps the locations sharing a response -- a KMA grid -- in one chunk.
    """
    by_location: dict[str, list[WeatherValue]] = {}
    for value in values:
        by_location.setdefault(value.location_id, []).append(value)
    chunk: list[WeatherValue] = []
    locations = 0
    for facts in by_location.values():
        if chunk and (locations >= max_locations or len(chunk) + len(facts) > max_values):
            yield chunk
            chunk, locations = [], 0
        chunk.extend(facts)
        locations += 1
    if chunk:
        yield chunk


def chunk_publications(
    sources: Sequence[Mapping[str, Any]],
    values: Sequence[WeatherValue],
    *,
    max_locations: int | None = None,
    max_values: int | None = None,
) -> list[tuple[list[Mapping[str, Any]], list[WeatherValue]]]:
    """Pair every location chunk with exactly the source records its facts cite.

    Built whole, up front, so a chunk citing no source is refused before
    anything publishes.  Holds references only; the facts are not copied.
    """
    by_key: dict[str, list[Mapping[str, Any]]] = {}
    for source in sources:
        by_key.setdefault(str(source["source_record_key"]), []).append(source)
    publications: list[tuple[list[Mapping[str, Any]], list[WeatherValue]]] = []
    for chunk in location_chunks(
        values,
        max_locations=PUBLISH_CHUNK_LOCATIONS if max_locations is None else max_locations,
        max_values=PUBLISH_CHUNK_VALUES if max_values is None else max_values,
    ):
        cited = dict.fromkeys(str(value.source_record_key) for value in chunk)
        records = [record for key in cited for record in by_key.get(key, [])]
        if not records:
            raise ValueError("publish chunk의 fact가 이 run의 source record를 인용하지 않습니다.")
        publications.append((records, chunk))
    return publications


def uncited_sources(
    sources: Sequence[Mapping[str, Any]], values: Sequence[WeatherValue]
) -> list[Mapping[str, Any]]:
    """Sources no fact cites: still fetched responses, recorded without facts."""
    cited = {str(value.source_record_key) for value in values}
    return [source for source in sources if str(source["source_record_key"]) not in cited]
