"""External providers publish in short location chunks, against the real repository.

An open_meteo forecast run loads ~510,720 values and staged them 50,000 at a
time, each batch one transaction holding its ~37 locations.  With the ingest
lock timeout, a KMA chunk queued behind such a batch gives up instead of
waiting -- so no collector may hold many locations for long.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest
from kortravelweather_dagster import chunked_publish
from kortravelweather_dagster.chunked_publish import chunk_publications, location_chunks
from kortravelweather_dagster.external_weather import run_external_weather_sync
from sqlalchemy import event, text

from kortravelweather.models import ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.providers import OpenMeteoProvider, ProviderLocation
from kortravelweather.repository import WeatherRepository

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)

_PAYLOAD = {
    "current": {
        "time": "2026-08-30T03:00:00Z",
        "temperature_2m": 25,
        "relative_humidity_2m": 50,
    }
}


class _Response:
    status_code = 200
    headers = {"content-type": "application/json"}
    text = "fixture"

    def json(self) -> Any:
        return _PAYLOAD


class _Transport:
    def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        return _Response()


def _setup(count: int) -> tuple[WeatherRepository, list[ProviderLocation]]:
    targets = [
        ProviderLocation(
            location_id=f"loc-{index}",
            latitude=37.0 + index / 100,
            longitude=127.0 + index / 100,
            metadata={},
        )
        for index in range(count)
    ]
    repository = WeatherRepository(TEST_DATABASE_URL)
    repository.create_schema()
    for target in targets:
        repository.upsert_location(
            WeatherLocation(
                location_id=target.location_id,
                name=target.location_id,
                latitude=target.latitude,
                longitude=target.longitude,
            )
        )
    return repository, targets


def _sync(repository: WeatherRepository, targets: list[ProviderLocation], provider: Any = None):
    return run_external_weather_sync(
        repository=repository,
        provider=provider or OpenMeteoProvider(transport=_Transport()),
        targets=targets,
        dataset_key="open_meteo_current",
    )


def _fact_count(repository: WeatherRepository) -> int:
    with repository.engine.connect() as connection:
        return int(connection.execute(text("SELECT count(*) FROM weather_values")).scalar_one())


def _run_row(repository: WeatherRepository) -> Any:
    runs = [run for run in repository.list_sync_runs(limit=5) if run.provider == "open_meteo"]
    return runs[0]


def test_external_publish_holds_only_a_chunk_of_locations(monkeypatch) -> None:
    # Old: the five locations and their five sources went out in the run's
    # finish, one transaction holding ten advisory locks.
    monkeypatch.setattr(chunked_publish, "PUBLISH_CHUNK_LOCATIONS", 2)
    repository, targets = _setup(5)
    held: list[int] = []

    def probe(conn, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().startswith("INSERT INTO weather_current_values"):
            cursor.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND pid = pg_backend_pid() AND granted"
            )
            held.append(cursor.fetchone()[0])

    event.listen(repository.engine, "before_cursor_execute", probe)
    try:
        result = _sync(repository, targets)
    finally:
        event.remove(repository.engine, "before_cursor_execute", probe)
    assert result["values_loaded"] == 10
    # Two locations and their two sources per transaction, three transactions.
    assert held == [4, 4, 2]
    assert _run_row(repository).values_loaded == 10


def test_a_lease_lost_between_chunks_stops_the_next_chunk_and_keeps_the_count(
    monkeypatch,
) -> None:
    monkeypatch.setattr(chunked_publish, "PUBLISH_CHUNK_LOCATIONS", 1)
    repository, targets = _setup(3)
    original = repository.ingest_batch
    calls: list[int] = []

    def reaped_after_first(**kwargs: Any) -> int:
        if calls:
            # The stale-run reaper wins between two chunks.
            with repository.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE weather_sync_runs SET status = 'failed', error = 'reaped' "
                        "WHERE status = 'running'"
                    )
                )
        loaded = original(**kwargs)
        calls.append(loaded)
        return loaded

    repository.ingest_batch = reaped_after_first  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="이미 종료"):
        _sync(repository, targets)
    # The first chunk stays published; the second refused inside its own
    # transaction, before any fact; the third never ran.
    assert calls == [2]
    assert _fact_count(repository) == 2
    run = _run_row(repository)
    assert run.status == "failed" and run.error == "reaped"
    # The reaper's terminal row makes the collector's failed finish a no-op;
    # the count is still the true one because each chunk recorded its own.
    assert run.values_loaded == 2


def test_a_chunk_citing_no_source_is_refused_before_anything_publishes() -> None:
    repository, targets = _setup(2)
    real = OpenMeteoProvider(transport=_Transport())

    class Misattributed:
        provider_key = real.provider_key

        def fetch(self, target: Any, *, dataset_key: str) -> Any:
            response = real.fetch(target, dataset_key=dataset_key)
            values = [
                value.model_copy(update={"source_record_key": "not-this-runs-source"})
                for value in response.values
            ]
            return SimpleNamespace(
                provider=response.provider,
                dataset_key=response.dataset_key,
                response_rows=response.response_rows,
                source_record=response.source_record,
                values=values,
            )

    calls: list[Any] = []
    original = repository.ingest_batch

    def counting(**kwargs: Any) -> int:
        calls.append(kwargs)
        return original(**kwargs)

    repository.ingest_batch = counting  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="source record를 인용하지"):
        _sync(repository, targets, provider=Misattributed())
    assert calls == []
    assert _fact_count(repository) == 0
    assert _run_row(repository).status == "failed"


def _value(location_id: str, source_key: str) -> WeatherValue:
    return WeatherValue(
        location_id=location_id,
        provider="p",
        dataset_key="d",
        weather_domain="forecast",
        forecast_style=ForecastStyle.SHORT,
        metric_key="TMP",
        value_number=1,
        payload={},
        source_record_key=source_key,
    )


def test_chunks_never_split_a_location_and_respect_both_caps() -> None:
    values = [_value(f"l{index}", "s") for index in range(4) for _ in range(3)]
    sizes = [
        (len({v.location_id for v in chunk}), len(chunk))
        for chunk in location_chunks(values, max_locations=3, max_values=7)
    ]
    assert sizes == [(2, 6), (2, 6)]
    sizes = [
        (len({v.location_id for v in chunk}), len(chunk))
        for chunk in location_chunks(values, max_locations=1, max_values=100)
    ]
    assert sizes == [(1, 3)] * 4


def test_chunk_publications_refuses_a_chunk_without_its_sources() -> None:
    sources = [{"source_record_key": "s1", "run_id": "r"}]
    with pytest.raises(ValueError, match="source record를 인용하지"):
        chunk_publications(sources, [_value("a", "s1"), _value("b", "s2")], max_locations=1)
    # Each chunk gets exactly the records its facts cite.
    sources.append({"source_record_key": "s2", "run_id": "r"})
    publications = chunk_publications(
        sources, [_value("a", "s1"), _value("b", "s2")], max_locations=1
    )
    assert [[r["source_record_key"] for r in records] for records, _ in publications] == [
        ["s1"],
        ["s2"],
    ]
