"""외부 provider 공통 Dagster 수집 경계."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable
from typing import Any

from kortravelweather.metrics import provider_request
from kortravelweather.providers import ProviderLocation, WeatherProvider, redact_secrets
from kortravelweather.repository import WeatherRepository


def _fetch_with_deadline(
    provider: WeatherProvider,
    target: ProviderLocation,
    *,
    dataset_key: str,
    timeout_seconds: float | None,
) -> Any:
    """Call ``provider.fetch`` under a wall-clock ceiling.

    ``request_json`` already bounds every HTTP *attempt*, and that is enough
    for a provider that answers slowly or refuses. It is not enough for a
    connection that neither returns a byte nor errors: no attempt ever
    completes, so no retry budget is ever spent. One such call held a run for
    eighteen hours, and because its process stayed alive the whole time,
    Dagster's own liveness check had nothing to act on.

    The worker is a daemon thread because an abandoned fetch cannot be killed
    -- only outlived. Daemon threads do not keep the run process alive once
    the step gives up, which a ``ThreadPoolExecutor`` worker would.
    """
    if timeout_seconds is None:
        return provider.fetch(target, dataset_key=dataset_key)

    outcome: dict[str, Any] = {}

    def _call() -> None:
        try:
            outcome["value"] = provider.fetch(target, dataset_key=dataset_key)
        except BaseException as exc:  # re-raised on the calling thread below
            outcome["error"] = exc

    worker = threading.Thread(
        target=_call,
        name=f"fetch-{provider.provider_key}-{dataset_key}",
        daemon=True,
    )
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        raise TimeoutError(
            f"provider fetch가 {timeout_seconds:.0f}초 안에 끝나지 않았습니다: "
            f"{provider.provider_key}/{dataset_key}/{target.location_id}"
        )
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def run_external_weather_sync(
    *,
    repository: WeatherRepository,
    provider: WeatherProvider,
    targets: Iterable[ProviderLocation],
    dataset_key: str,
    max_targets: int = 10_000,
    max_response_rows: int = 1_000_000,
    max_values: int = 500_000,
    max_payload_bytes: int = 16 * 1024 * 1024,
    fetch_timeout_seconds: float | None = None,
    min_request_interval_seconds: float = 0.0,
) -> dict[str, Any]:
    """응답을 bounded stage한 후 하나의 publish transaction으로 저장한다."""
    target_list = list(targets)
    if not target_list:
        raise ValueError("external weather target이 비어 있습니다.")
    if len(target_list) > max_targets:
        raise ValueError(f"external weather target 수가 상한을 초과했습니다: {len(target_list)}")
    if len({target.location_id for target in target_list}) != len(target_list):
        raise ValueError("external weather location_id가 중복됩니다.")
    if max_response_rows <= 0 or max_values <= 0 or max_payload_bytes <= 0:
        raise ValueError("external weather budget은 양수여야 합니다.")

    run = repository.start_sync_run(
        provider=provider.provider_key,
        dataset_key=dataset_key,
        locations_total=len(target_list),
    )
    staged_sources: list[dict[str, Any]] = []
    staged_values = []
    sent_at: float | None = None
    try:
        for target in target_list:
            heartbeat = getattr(repository, "heartbeat_sync_run", None)
            if callable(heartbeat) and heartbeat(run.run_id) is False:
                raise RuntimeError("sync run lease가 만료되어 publish를 중단했습니다.")
            if min_request_interval_seconds > 0 and sent_at is not None:
                # Monthly quota is not the only ceiling a provider publishes;
                # a per-minute one is breached by a fast sweep long before the
                # month is, and being throttled costs far more time than the
                # pacing does.
                idle = min_request_interval_seconds - (time.monotonic() - sent_at)
                if idle > 0:
                    time.sleep(idle)
            sent_at = time.monotonic()
            with provider_request(provider.provider_key, dataset_key):
                response = _fetch_with_deadline(
                    provider,
                    target,
                    dataset_key=dataset_key,
                    timeout_seconds=fetch_timeout_seconds,
                )
            if response.provider != provider.provider_key or response.dataset_key != dataset_key:
                raise ValueError("provider 응답의 provider/dataset 계약이 요청과 다릅니다.")
            if response.response_rows > max_response_rows:
                raise ValueError(
                    f"provider 응답 row 수가 상한을 초과했습니다: {response.response_rows}"
                )
            payload_size = len(
                json.dumps(
                    response.source_record.get("payload", {}),
                    ensure_ascii=False,
                    default=str,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            if payload_size > max_payload_bytes:
                raise ValueError(
                    f"provider raw payload가 상한을 초과했습니다: {payload_size} bytes"
                )
            if len(staged_values) + len(response.values) > max_values:
                raise ValueError("external weather normalized value 수가 상한을 초과했습니다.")
            if any(
                value.provider != provider.provider_key or value.dataset_key != dataset_key
                for value in response.values
            ):
                raise ValueError("provider 응답 fact의 provider/dataset 계약이 요청과 다릅니다.")
            staged_sources.append({**response.source_record, "run_id": run.run_id})
            staged_values.extend(response.values)
            if callable(heartbeat) and heartbeat(run.run_id) is False:
                raise RuntimeError("sync run lease가 만료되어 publish를 중단했습니다.")
        loaded, finished = repository.publish_and_finish(
            run_id=run.run_id,
            source_records=staged_sources,
            values=staged_values,
            grids_fetched=0,
            requests_fetched=len(target_list),
        )
    except Exception as exc:
        repository.finish_sync_run(
            run.run_id,
            status="failed",
            requests_fetched=len(staged_sources),
            error=str(redact_secrets(str(exc)))[:1000],
        )
        raise
    return {
        "provider": provider.provider_key,
        "dataset_key": dataset_key,
        "run_id": finished.run_id,
        "status": finished.status,
        "targets": len(target_list),
        "requests_fetched": len(target_list),
        "values_loaded": loaded,
        "source_record_keys": [record["source_record_key"] for record in staged_sources],
    }
