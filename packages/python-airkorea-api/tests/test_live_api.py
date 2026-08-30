from __future__ import annotations

import os

import pytest

from airkorea import AirKoreaClient

pytestmark = pytest.mark.integration


def _require_live() -> None:
    if os.getenv("AIRKOREA_RUN_LIVE") != "1":
        pytest.skip("set AIRKOREA_RUN_LIVE=1 to run live AirKorea API tests")


def test_live_station_measurements_smoke() -> None:
    _require_live()

    with AirKoreaClient() as air:
        rows = air.station_measurements("종로구", num_of_rows=1)

    assert isinstance(rows, list)
    if rows:
        assert rows[0].station_name
        assert rows[0].raw


@pytest.mark.asyncio
async def test_live_async_station_measurements_smoke() -> None:
    _require_live()

    async with AirKoreaClient.aio() as air:
        rows = await air.station_measurements("종로구", num_of_rows=1)

    assert isinstance(rows, list)
    if rows:
        assert rows[0].station_name
        assert rows[0].raw
