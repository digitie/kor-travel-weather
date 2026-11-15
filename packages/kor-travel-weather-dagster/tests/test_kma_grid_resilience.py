"""한 격자의 재시도 소진은 정상 격자의 저장·원본 신선도를 바꾸지 않는다."""

import asyncio
from types import SimpleNamespace

import pytest
from kma.exceptions import KmaError
from kortravelweather_dagster import kma_weather
from test_kma_grid_publish import _Client, _targets
from test_kma_weather import FakeRepository


class Repository(FakeRepository):
    def finish_sync_run(self, run_id, **kwargs):
        self.runs[-1].error = kwargs.get("error")
        return super().finish_sync_run(run_id, **kwargs)


@pytest.mark.parametrize("kind,code", [
    ("network", "PROVIDER_NETWORK"), ("server", "PROVIDER_SERVER"),
    ("parse", "PROVIDER_PARSE"), ("empty", "PROVIDER_NO_DATA"),
])
@pytest.mark.parametrize("failed_grid", [50, 52])
def test_one_grid_failure_keeps_later_healthy_grids_and_exact_counts(kind, code, failed_grid):
    repository, calls = Repository(), []

    class Client(_Client):
        async def now(self, **kwargs):
            calls.append(kwargs["nx"])
            if kwargs["nx"] == failed_grid:
                if kind == "empty":
                    return SimpleNamespace(raw={"items": []})
                raise KmaError("private URL and payload", failure_kind=kind)
            return await super().now(**kwargs)

    result = kma_weather.run_weather_sync(
        repository=repository, client=Client(), targets=_targets(6, 2),
        base_datasets=frozenset({kma_weather.KMA_ULTRA_SHORT_NOWCAST}),
    )
    assert calls == list(range(50, 56))  # 실패 격자의 추가 앱 재시도는 없다.
    assert result["status"] == repository.runs[-1].status == "partial"
    assert result["values_loaded"] == len(repository.values) == 10
    assert result["planned_grids"] == 6
    assert result["completed_grids"] == result["grids_fetched"] == 5
    assert result["failed_grids"] == result["deferred_grids"] == 1
    assert result["grid_failure_counts"] == {code: 1}
    assert result["deferred_grid_ids"] == [f"grid:{failed_grid}:127"]
    assert result["previous_data_preserved"] is True
    assert all(value.location_id != f"g{failed_grid - 50:02d}-0" for value in repository.values)
    assert "private" not in repository.runs[-1].error


def test_all_grids_failed_is_real_failed_not_zero_value_success():
    repository = Repository()

    class Client(_Client):
        async def now(self, **kwargs):
            raise KmaError("private URL", failure_kind="server", status_code=503)

    with pytest.raises(kma_weather.KmaAllGridsFailedError) as caught:
        kma_weather.run_weather_sync(
            repository=repository, client=Client(), targets=_targets(6, 1),
            base_datasets=frozenset({kma_weather.KMA_ULTRA_SHORT_NOWCAST}),
        )
    assert repository.runs[-1].status == "failed" and not repository.values
    assert caught.value.kma_diagnostics["failed_grids"] == 6
    assert caught.value.grid_failure_counts == {"PROVIDER_SERVER": 6}
    assert repository.runs[-1].error == "KMA_ALL_GRIDS_FAILED"


@pytest.mark.parametrize("kind,status", [
    ("auth", 403), ("quota", None), ("rate_limit", 429), ("request", None),
    ("network", 429), ("server", 401),
])
def test_common_provider_refusal_remains_fatal_and_stops_new_requests(kind, status):
    repository, calls = Repository(), []

    class Client(_Client):
        async def now(self, **kwargs):
            calls.append(kwargs["nx"])
            raise KmaError("provider refused", failure_kind=kind, status_code=status)

    with pytest.raises(KmaError):
        kma_weather.run_weather_sync(
            repository=repository, client=Client(), targets=_targets(8, 1),
            base_datasets=frozenset({kma_weather.KMA_ULTRA_SHORT_NOWCAST}),
        )
    assert calls == [50, 51, 52, 53]  # 이미 시작한 bounded window만 정리한다.
    assert repository.runs[-1].status == "failed" and not repository.values


@pytest.mark.asyncio
async def test_cancel_after_recoverable_failure_joins_all_remaining_fetches():
    repository, started, active = Repository(), asyncio.Event(), set()

    class Client(_Client):
        async def now(self, **kwargs):
            if kwargs["nx"] == 50:
                raise KmaError("network unavailable", failure_kind="network")
            active.add(kwargs["nx"])
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active.remove(kwargs["nx"])

    task = asyncio.create_task(kma_weather._run_weather_sync(
        repository=repository, client=Client(), targets=_targets(8, 1),
        base_datasets=frozenset({kma_weather.KMA_ULTRA_SHORT_NOWCAST}),
    ))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not active and repository.runs[-1].status == "failed"
