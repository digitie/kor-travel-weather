"""raw-only 수집과 재게시도 실행 전체 예산을 우회하지 못한다."""

import json
import tracemalloc
from datetime import UTC, datetime
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


@pytest.mark.parametrize("payload", [
    {}, {"한글": ["서울", "🌧", "\\", "\n", 1, True, None]},
    {"timestamp": datetime(2026, 10, 14, tzinfo=UTC)},
])
def test_streamed_payload_size_preserves_json_utf8_budget(payload):
    encoded = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")).encode()
    assert external_weather._bounded_payload_size(payload, max_bytes=len(encoded)) == len(encoded)
    with pytest.raises(ValueError, match="raw payload"):
        external_weather._bounded_payload_size(payload, max_bytes=len(encoded) - 1)


def test_oversized_payload_stops_before_serializing_later_rows():
    class Later:
        def __str__(self):
            raise AssertionError("예산 초과 뒤의 행을 직렬화하면 안 된다")

    with pytest.raises(ValueError, match="raw payload"):
        external_weather._bounded_payload_size(["한글" * 100, Later()], max_bytes=50)


def test_payload_budget_check_does_not_copy_entire_large_json():
    # 입력 자체는 계측 전에 만들고 검사 과정의 추가 할당만 잰다.
    payload = [{"raw": "x" * 1024} for _ in range(6000)]
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="raw payload"):
            external_weather._bounded_payload_size(payload, max_bytes=2048)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 256 * 1024
