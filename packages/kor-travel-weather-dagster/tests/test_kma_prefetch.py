"""제한된 선행 요청의 병행성·순서·메모리·취소 계약."""
import asyncio
from contextlib import aclosing

import pytest
from kortravelweather_dagster import kma_weather
from test_kma_grid_publish import _Client, _Repository, _targets


@pytest.mark.asyncio
async def test_two_fetches_overlap_but_third_waits_for_consumer():
    started = []
    release = asyncio.Event()

    async def fetch(index):
        started.append(index)
        await release.wait()
        return index

    async with aclosing(kma_weather._prefetch_grids(
        [lambda index=index: fetch(index) for index in range(5)]
    )) as stream:
        first = asyncio.create_task(anext(stream))
        for _ in range(10):
            await asyncio.sleep(0)
            if len(started) == 2:
                break
        assert started == [0, 1]
        release.set()
        assert await first == 0
        # 소비자가 DB 게시 중인 동안에는 세 번째 요청을 시작하지 않는다.
        for _ in range(5):
            await asyncio.sleep(0)
        assert started == [0, 1]
        assert [item async for item in stream] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_consumer_failure_cancels_and_joins_pending_requests():
    cancelled = asyncio.Event()

    async def first():
        await asyncio.sleep(0)
        return 1

    async def slow():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(RuntimeError, match="DB failed"):
        async with aclosing(kma_weather._prefetch_grids([first, slow])) as stream:
            async for _ in stream:
                raise RuntimeError("DB failed")
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_out_of_order_failure_is_reported_and_tasks_are_joined():
    async def first():
        await asyncio.sleep(0)
        return 1

    async def broken():
        raise ValueError("provider failed")

    async with aclosing(kma_weather._prefetch_grids([first, broken])) as stream:
        assert await anext(stream) == 1
        with pytest.raises(ValueError, match="provider failed"):
            await anext(stream)


def test_grid_response_byte_cap_fails_before_publication(monkeypatch):
    monkeypatch.setattr(kma_weather, "GRID_RESPONSE_BYTES", 1)
    repository = _Repository()
    with pytest.raises(ValueError, match="바이트 상한"):
        kma_weather.run_weather_sync(
            repository=repository, client=_Client(), targets=_targets(3, 2),
            base_datasets=frozenset({kma_weather.KMA_ULTRA_SHORT_NOWCAST}),
        )
    assert repository.runs[-1].status == "failed"
    assert repository._committed == 0
