"""Provider-independent weather adapter contract.

외부 API client는 이 모듈의 얇은 동기 transport 경계만 사용한다. 응답을
``WeatherValue``로 바꾸는 일과 source lineage 생성은 모든 provider에서 같은
규칙을 사용하므로, KMA와 유료/무키 provider를 같은 Dagster 경계에 넣을 수 있다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from kortravelweather.models import ForecastStyle, TimelineBucket, WeatherValue, kst_now


class ProviderError(RuntimeError):
    """분류 가능한 provider 호출 오류."""

    def __init__(
        self, message: str, *, code: str, retryable: bool = False, status_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class CredentialError(ProviderError):
    def __init__(self, provider: str) -> None:
        super().__init__(f"{provider} credential이 설정되지 않았습니다.", code="credential_missing")


class ProviderResponseLike(Protocol):
    status_code: int
    headers: Mapping[str, str]
    text: str

    def json(self) -> Any: ...


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> ProviderResponseLike: ...


class WeatherProvider(Protocol):
    provider_key: str

    def fetch(
        self,
        location: ProviderLocation,
        *,
        dataset_key: str | None = None,
        at: datetime | None = None,
    ) -> ProviderResponse: ...


@dataclass(frozen=True, slots=True)
class ProviderLocation:
    location_id: str
    latitude: float
    longitude: float
    metadata: Mapping[str, Any] | None = None

    @property
    def provider_metadata(self) -> Mapping[str, Any]:
        return self.metadata or {}


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    provider: str
    dataset_key: str
    source_record: dict[str, Any]
    values: list[WeatherValue]
    requests_fetched: int = 1


_SECRET_KEYS = {
    "key",
    "apikey",
    "api_key",
    "access_key",
    "accesskey",
    "service_key",
    "servicekey",
    "auth_key",
    "authkey",
    "token",
    "access_token",
    "authorization",
    "password",
    "secret",
    "client_secret",
    "appkey",
    "app_key",
    "api_token",
    "appid",
}


def _normalized_key(key: Any) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).lower().replace("-", "_")


def redact_secrets(value: Any) -> Any:
    """중첩 payload/request에서 credential과 URL query secret을 제거한다."""
    if isinstance(value, Mapping):
        return {
            key: "[REDACTED]" if _normalized_key(key) in _SECRET_KEYS else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return re.sub(
            r"(?i)(api[_-]?key|access[_-]?key|service[_-]?key|auth[_-]?key|access[_-]?token|token|password|secret|key)(=|:)([^&\s,;]+)",
            r"\1\2[REDACTED]",
            value,
        )
    return value


def parse_datetime(value: Any, *, tz: tzinfo = UTC) -> datetime:
    """ISO-8601, epoch seconds, RFC 2822, provider local date를 aware UTC로 변환."""
    if isinstance(value, datetime):
        current = value if value.tzinfo else value.replace(tzinfo=tz)
        return current.astimezone(UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    text = str(value).strip()
    if not text:
        raise ValueError("provider datetime이 비어 있습니다.")
    if text.isdigit() and len(text) >= 9:
        return datetime.fromtimestamp(float(text), tz=UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"provider datetime 형식 오류: {text!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(UTC)


def decimal_value(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def source_record_key(
    provider: str, dataset_key: str, location_id: str, payload: Mapping[str, Any]
) -> str:
    canonical = _canonical_json([provider, dataset_key, location_id, redact_secrets(payload)])
    return "sr_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:48]


def make_source_record(
    *,
    provider: str,
    dataset_key: str,
    location_id: str,
    payload: Mapping[str, Any],
    endpoint: str,
    request_params: Mapping[str, Any] | None = None,
    status_code: int | None = None,
    fetched_at: datetime | None = None,
) -> dict[str, Any]:
    safe_payload = redact_secrets(dict(payload))
    safe_metadata: dict[str, Any] = {"endpoint": endpoint}
    if request_params:
        safe_metadata["request_params"] = redact_secrets(dict(request_params))
    if status_code is not None:
        safe_metadata["status"] = status_code
    full_payload = {"rows": [safe_payload], "response_metadata": safe_metadata}
    return {
        "source_record_key": source_record_key(provider, dataset_key, location_id, full_payload),
        "provider": provider,
        "dataset_key": dataset_key,
        "source_entity_type": "weather_response",
        "source_entity_id": location_id,
        "payload": full_payload,
        "fetched_at": fetched_at or kst_now(),
    }


def make_value(
    *,
    provider: str,
    dataset_key: str,
    location_id: str,
    metric_key: str,
    value: Any,
    unit: str | None,
    target_at: datetime,
    source_metric_key: str | None = None,
    source_metric_name: str | None = None,
    metric_name: str | None = None,
    forecast_style: ForecastStyle = ForecastStyle.OBSERVED,
    timeline_bucket: TimelineBucket | None = None,
    source_record_key: str | None = None,
    raw: Mapping[str, Any] | None = None,
    issued_at: datetime | None = None,
    valid_until: datetime | None = None,
) -> WeatherValue:
    number = decimal_value(value)
    canonical_unit = unit
    if number is not None and unit == "km/h":
        number = number / Decimal("3.6")
        canonical_unit = "m/s"
    text = None if number is not None else (None if value is None else str(value))
    return WeatherValue(
        location_id=location_id,
        provider=provider,
        dataset_key=dataset_key,
        weather_domain="weather",
        forecast_style=forecast_style,
        timeline_bucket=timeline_bucket,
        metric_key=metric_key,
        metric_name=metric_name or metric_key,
        source_metric_key=source_metric_key or metric_key,
        source_metric_name=source_metric_name,
        value_number=number,
        value_text=text,
        unit=canonical_unit,
        issued_at=issued_at,
        valid_at=target_at,
        target_at=target_at,
        known_at=kst_now(),
        normalization_version=f"{provider}-v1",
        payload=redact_secrets(dict(raw or {})),
        source_record_key=source_record_key,
    )


def request_json(
    transport: HttpTransport,
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 15.0,
    retries: int = 1,
) -> tuple[Any, ProviderResponseLike, dict[str, Any]]:
    """HTTP 호출을 provider 공통 retry/error 분류로 감싼다."""
    clean_params = redact_secrets(dict(params or {}))
    attempts = retries + 1
    last_error: Exception | None = None
    response: ProviderResponseLike | None = None
    for attempt in range(attempts):
        try:
            response = transport.request(
                method, url, params=params, headers=headers, timeout=timeout
            )
            status_code = response.status_code
            if status_code == 401 or status_code == 403:
                raise ProviderError(
                    "provider credential이 거부되었습니다.", code="auth", status_code=status_code
                )
            if status_code == 429:
                if attempt + 1 < attempts:
                    continue
                raise ProviderError(
                    "provider rate limit입니다.",
                    code="rate_limit",
                    retryable=True,
                    status_code=status_code,
                )
            if status_code >= 500:
                if attempt + 1 < attempts:
                    continue
                raise ProviderError(
                    "provider server 오류입니다.",
                    code="server",
                    retryable=True,
                    status_code=status_code,
                )
            if status_code >= 400:
                raise ProviderError(
                    "provider client 오류입니다.", code="client", status_code=status_code
                )
            try:
                payload = response.json()
            except Exception as exc:
                raise ProviderError(
                    "provider JSON 응답을 읽을 수 없습니다.", code="schema", status_code=status_code
                ) from exc
            if not isinstance(payload, (dict, list)):
                raise ProviderError(
                    "provider 응답 형식이 object/array가 아닙니다.",
                    code="schema",
                    status_code=status_code,
                )
            return (
                payload,
                response,
                {"endpoint": url, "request_params": clean_params, "status": status_code},
            )
        except ProviderError:
            raise
        except (TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                continue
            raise ProviderError(
                "provider 네트워크/timeout 오류입니다.", code="network", retryable=True
            ) from exc
        except Exception as exc:
            # httpx.TimeoutException/RequestError는 optional transport가 정의한
            # 예외이므로 이름으로 분류해 adapter import 의존성을 유지한다.
            last_error = exc
            name = type(exc).__name__.lower()
            if (
                "timeout" in name or "network" in name or "request" in name
            ) and attempt + 1 < attempts:
                continue
            if "timeout" in name or "network" in name or "request" in name:
                raise ProviderError(
                    "provider 네트워크/timeout 오류입니다.", code="network", retryable=True
                ) from exc
            raise ProviderError("provider transport 오류입니다.", code="transport") from exc
    raise ProviderError(
        "provider 호출이 실패했습니다.", code="network", retryable=True
    ) from last_error


def httpx_transport() -> HttpTransport:
    import httpx

    return httpx.Client(follow_redirects=True)
