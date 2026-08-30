"""디버그 Web UI와 replay fixture 저장을 위한 helper."""

from __future__ import annotations

import json
import re
import time
import traceback
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from airkorea._http import HttpExchange
from airkorea.catalog import api_catalog_for_debug_input
from airkorea.client import AirKoreaClient
from airkorea.exceptions import AirKoreaError
from airkorea.metadata import is_credential_param
from airkorea.models import AirKoreaPage

SENSITIVE_KEYS = {
    "authorization",
    "x_api_key",
    "x-api-key",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "servicekey",
    "service_key",
}

DEBUGGABLE_METHODS = frozenset(
    {
        "station_measurements",
        "latest_station_measurement",
        "sido_measurements",
        "unhealthy_stations",
        "forecast_notices",
        "weekly_forecasts",
        "stations",
        "nearby_stations",
        "tm_coordinates",
        "measurement_near",
        "sido_average_stats",
        "city_average_stats",
        "station_daily_stats",
        "station_monthly_stats",
        "ozone_advisories",
        "yellow_dust_advisories",
        "dust_alarms",
        "traffic_stats",
        "high_pm25_forecasts",
        "cai_measurements",
        "background_concentrations",
        "english_measurements",
        "english_stations",
        "call",
    }
)

DEFAULT_ASSERTION = {
    "mode": "snapshot",
    "exclude_fields": ["fetched_at", "request_id", "updated_at", "collected_at"],
    "required_fields": [],
}

# airkorea 자체 예외 타입을 오류 dict의 provider별 필드로 매핑합니다.
_FAILURE_KIND_BY_EXCEPTION = {
    "AirKoreaAuthError": "auth",
    "AirKoreaRequestError": "request",
    "AirKoreaNoDataError": "no_data",
    "AirKoreaRateLimitError": "rate_limit",
    "AirKoreaNetworkError": "network",
    "AirKoreaServerError": "server",
    "AirKoreaParseError": "parse",
}
_RETRYABLE_FAILURE_KINDS = {"network", "server", "rate_limit"}
_STATUS_CODE_PATTERN = re.compile(r"HTTP (\d{3})")
_RESULT_CODE_PATTERN = re.compile(r"returned (\w+):")


@dataclass(frozen=True)
class DebugRun:
    """디버그 UI가 표시하고 fixture로 저장할 한 번의 실행 결과."""

    function: str
    input: Mapping[str, Any]
    request: Mapping[str, Any]
    response: Mapping[str, Any]
    parsed: Any
    processed: Any
    catalog: tuple[Mapping[str, Any], ...] = ()
    trace: tuple[str, ...] = ()
    error: Mapping[str, Any] | None = None


def run_debug_method(
    client: AirKoreaClient,
    function_name: str,
    input_data: Mapping[str, Any] | None = None,
) -> DebugRun:
    """허용된 public method를 실행하고 UI/fixture용 ``DebugRun``을 반환합니다."""

    if function_name not in DEBUGGABLE_METHODS:
        known = ", ".join(sorted(DEBUGGABLE_METHODS))
        raise ValueError(f"debug fixture does not support {function_name!r}; known: {known}")

    safe_input = redact_sensitive(jsonable(input_data or {}))
    start_index = len(client._http.exchanges)
    parsed: Any = None
    processed: Any = None
    error: Mapping[str, Any] | None = None
    catalog = _catalog_for_debug_run(function_name, input_data or {})

    start_time = time.perf_counter()
    try:
        method = getattr(client, function_name)
        parsed = method(**dict(input_data or {}))
        # AirKoreaPage(raw call() 결과)는 item 목록을 "processed"로 펴서
        # Processed Result 탭이 pandas 표로 보여줄 수 있게 합니다.
        processed = list(parsed.items) if isinstance(parsed, AirKoreaPage) else parsed
    except Exception as exc:
        error = build_error(exc)
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    exchanges = client._http.exchanges[start_index:]
    trace: list[str] = [_catalog_trace(item) for item in catalog]
    trace.append(f"실행 시간: {elapsed_ms:.1f}ms")
    if error is not None:
        trace.append(f"실행 실패: {error['type']}: {error['message']}")
    trace.extend(_exchange_trace(exchange) for exchange in exchanges)
    last_exchange = exchanges[-1] if exchanges else None

    return DebugRun(
        function=function_name,
        input=safe_input,
        request=_request_from_exchange(last_exchange),
        response=_response_from_exchange(last_exchange),
        parsed=parsed,
        processed=processed,
        catalog=catalog,
        trace=tuple(trace),
        error=error,
    )


def save_debug_fixture(
    *,
    base_dir: str | Path,
    case_name: str,
    function_name: str | None = None,
    description: str = "",
    input_data: Mapping[str, Any] | None = None,
    request_data: Mapping[str, Any] | None = None,
    response_data: Mapping[str, Any] | None = None,
    parsed_result: Any = None,
    processed_result: Any = None,
    assertion: Mapping[str, Any] | None = None,
    library_version: str | None = None,
    overwrite: bool = False,
    debug_run: DebugRun | None = None,
) -> Path:
    """디버그 실행 결과를 pytest replay용 JSON fixture로 저장합니다."""

    if debug_run is not None:
        function_name = function_name or debug_run.function
        input_data = input_data if input_data is not None else debug_run.input
        request_data = request_data if request_data is not None else debug_run.request
        response_data = response_data if response_data is not None else debug_run.response
        parsed_result = parsed_result if parsed_result is not None else debug_run.parsed
        processed_result = processed_result if processed_result is not None else debug_run.processed

    if function_name is None:
        raise ValueError("function_name is required")

    safe_case_name = slugify(case_name)
    fixture_dir = Path(base_dir) / slugify(function_name)
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / f"{safe_case_name}.json"

    if fixture_path.exists() and not overwrite:
        raise FileExistsError(f"Fixture already exists: {fixture_path}")

    fixture = {
        "name": safe_case_name,
        "function": function_name,
        "description": description,
        "input": redact_sensitive(jsonable(input_data or {})),
        "request": redact_sensitive(jsonable(request_data or {})),
        "response": redact_sensitive(jsonable(response_data or {})),
        "catalog": redact_sensitive(jsonable(debug_run.catalog if debug_run else ())),
        "parsed": redact_sensitive(jsonable(parsed_result)),
        "processed": redact_sensitive(jsonable(processed_result)),
        "assertion": dict(assertion or DEFAULT_ASSERTION),
        "meta": {
            "created_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
            "library_version": library_version,
            "source": "debug_ui",
        },
    }

    with fixture_path.open("w", encoding="utf-8") as file:
        json.dump(fixture, file, ensure_ascii=False, indent=2)
        file.write("\n")

    return fixture_path


def load_debug_fixture(path: str | Path) -> dict[str, Any]:
    """저장된 replay fixture JSON을 읽어 dict로 반환합니다."""

    with Path(path).open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("fixture root must be an object")
    return data


def jsonable(value: Any) -> Any:
    """Pydantic 모델과 Python 값을 JSON 저장 가능한 값으로 변환합니다."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [jsonable(item) for item in sorted(value, key=repr)]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def redact_sensitive(value: Any) -> Any:
    """fixture에 남기면 안 되는 인증/토큰 값을 ``<REDACTED>``로 치환합니다."""

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if _is_sensitive_key(text_key):
                redacted[text_key] = "<REDACTED>"
            else:
                redacted[text_key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value


def build_error(exc: Exception) -> dict[str, Any]:
    """예외를 UI/fixture에 저장 가능한 구조화된 오류 dict로 변환합니다.

    항상 ``type``/``message``/``traceback``을 담고, ``AirKoreaError`` 하위
    타입이면 ``failure_kind``/``retryable``과, 메시지에서 뽑아낼 수 있으면
    ``status_code``/``result_code``도 추가합니다.
    """

    message = str(exc)
    payload: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": message,
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    }
    if isinstance(exc, AirKoreaError):
        failure_kind = _FAILURE_KIND_BY_EXCEPTION.get(type(exc).__name__)
        payload["failure_kind"] = failure_kind
        payload["retryable"] = failure_kind in _RETRYABLE_FAILURE_KINDS
        status_match = _STATUS_CODE_PATTERN.search(message)
        if status_match:
            payload["status_code"] = int(status_match.group(1))
        result_match = _RESULT_CODE_PATTERN.search(message)
        if result_match:
            payload["result_code"] = result_match.group(1)
    return cast(dict[str, Any], redact_sensitive(payload))


def slugify(value: str) -> str:
    """case name을 파일명에 쓸 수 있는 짧은 slug로 변환합니다."""

    slug = re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", value.strip()).strip("-_").lower()
    return slug or "case"


def _request_from_exchange(exchange: HttpExchange | None) -> dict[str, Any]:
    if exchange is None:
        return {}
    return {
        "method": exchange.method,
        "url": exchange.url,
        "query": redact_sensitive(jsonable(exchange.request_params)),
        "headers": {},
    }


def _response_from_exchange(exchange: HttpExchange | None) -> dict[str, Any]:
    if exchange is None:
        return {}
    body: Mapping[str, Any] | None = exchange.response_body
    if body is None:
        body = exchange.response_json
    return {
        "status_code": exchange.status_code,
        "headers": redact_sensitive(jsonable(exchange.response_headers)),
        "body": redact_sensitive(jsonable(body or {})),
    }


def _exchange_trace(exchange: HttpExchange) -> str:
    return f"{exchange.method} {exchange.url} -> HTTP {exchange.status_code}"


def _catalog_for_debug_run(
    function_name: str,
    input_data: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    try:
        return tuple(
            entry.to_dict()
            for entry in api_catalog_for_debug_input(function_name, input_data)
        )
    except ValueError:
        return ()


def _catalog_trace(item: Mapping[str, Any]) -> str:
    dataset_name = item.get("dataset_name", "")
    endpoint = item.get("endpoint", "")
    service_key_url = item.get("service_key_url", "")
    return f"Catalog: {dataset_name} / {endpoint} / service key: {service_key_url}"


def _is_sensitive_key(key: str) -> bool:
    normalized = key.replace("-", "_").lower()
    return normalized in SENSITIVE_KEYS or is_credential_param(key)


__all__ = [
    "DEBUGGABLE_METHODS",
    "DEFAULT_ASSERTION",
    "DebugRun",
    "build_error",
    "jsonable",
    "load_debug_fixture",
    "redact_sensitive",
    "run_debug_method",
    "save_debug_fixture",
    "slugify",
]
