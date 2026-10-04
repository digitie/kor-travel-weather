"""raw-only 수집과 재게시도 실행 전체 예산을 우회하지 못한다."""

from types import SimpleNamespace

import pytest
from kortravelweather_dagster import external_weather

from kortravelweather.providers import ProviderLocation


class Repository:
    def __init__(self):
        self.flushes = []
        self.failed = False

    def start_sync_run(self, **kwargs):
        return SimpleNamespace(run_id="run")

    def ingest_batch(self, *, source_records, values):
        self.flushes.append(len(source_records))
        # 이미 저장된 fact의 재게시를 모사한다.
        return 0

    def publish_and_finish(self, **kwargs):
        return 0, SimpleNamespace(run_id="run", status="success")

    def finish_sync_run(self, *args, **kwargs):
        self.failed = True


def targets(count):
    return [ProviderLocation(location_id=str(i), latitude=37, longitude=127) for i in range(count)]


@pytest.mark.parametrize("bound", ["sources", "bytes"])
def test_raw_only_responses_are_published_before_sweep_end(monkeypatch, bound):
    repository = Repository()
    monkeypatch.setattr(
        external_weather, "_PUBLISH_BATCH_SOURCES", 2 if bound == "sources" else 100
    )
    monkeypatch.setattr(
        external_weather, "_PUBLISH_BATCH_PAYLOAD_BYTES", 100 if bound == "bytes" else 10000
    )

    class Provider:
        provider_key = "fixture"

        def fetch(self, target, *, dataset_key):
            if target.location_id == "2":
                assert repository.flushes, "빈 fact 응답도 raw 버퍼를 먼저 비워야 한다"
            return SimpleNamespace(
                provider="fixture",
                dataset_key=dataset_key,
                response_rows=0,
                values=[],
                source_record={
                    "source_record_key": target.location_id,
                    "payload": {"raw": "x" * 60},
                },
            )

    external_weather.run_external_weather_sync(
        repository=repository, provider=Provider(), targets=targets(3), dataset_key="fixture"
    )
    assert not repository.failed


def test_replayed_batches_cannot_reset_normalized_value_budget(monkeypatch):
    repository = Repository()
    monkeypatch.setattr(
        external_weather, "chunk_publications", lambda sources, values: iter([(sources, values)])
    )
    monkeypatch.setattr(external_weather, "uncited_sources", lambda *args: [])

    class Provider:
        provider_key = "fixture"

        def fetch(self, target, *, dataset_key):
            return SimpleNamespace(
                provider="fixture",
                dataset_key=dataset_key,
                response_rows=1,
                values=[SimpleNamespace(provider="fixture", dataset_key=dataset_key)],
                source_record={"source_record_key": target.location_id, "payload": {}},
            )

    with pytest.raises(ValueError, match="normalized value"):
        external_weather.run_external_weather_sync(
            repository=repository,
            provider=Provider(),
            targets=targets(3),
            dataset_key="fixture",
            max_values=2,
            publish_batch_values=1,
        )
    assert repository.flushes == [1, 1]
    assert repository.failed
