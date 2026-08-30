"""AirKorea/data.go.kr 오류 매핑을 포함한 HTTP 헬퍼."""

from __future__ import annotations

import asyncio
import random
import urllib.parse
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx

from airkorea.exceptions import (
    AirKoreaAuthError,
    AirKoreaNetworkError,
    AirKoreaNoDataError,
    AirKoreaParseError,
    AirKoreaRateLimitError,
    AirKoreaRequestError,
    AirKoreaServerError,
)
from airkorea.metadata import normalize_service_key, sanitize_request_params


class ResponseLike(Protocol):
    status_code: int
    text: str

    def json(self) -> Any: ...


class SessionLike(Protocol):
    def get(self, url: str, *, params: Mapping[str, Any], timeout: float) -> ResponseLike: ...


class AsyncSessionLike(Protocol):
    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any],
        timeout: float,
    ) -> ResponseLike: ...


TRANSIENT_STATUSES = {500, 502, 503, 504}

_MAX_EXCHANGES = 1000
_MAX_RETRY_BACKOFF_SECONDS = 10.0


@dataclass(frozen=True)
class HttpExchange:
    """디버그 fixture 저장을 위해 인증키를 제거해 보관한 HTTP 교환 기록."""

    method: str
    url: str
    request_params: Mapping[str, Any]
    status_code: int
    response_headers: Mapping[str, str]
    response_body: Mapping[str, Any] | None = None
    response_json: Mapping[str, Any] | None = None
    response_text: str = ""


class HttpClient:
    """AirKorea 서비스군이 공유하는 httpx 기반 동기 요청 래퍼."""

    def __init__(
        self,
        service_key: str,
        *,
        session: SessionLike | None = None,
        timeout: float = 10.0,
        retries: int = 3,
        retry_backoff: float = 0.3,
    ) -> None:
        normalized_service_key = normalize_service_key(service_key)
        if not normalized_service_key:
            raise AirKoreaAuthError("service_key is required")
        self._service_key = normalized_service_key
        self._session = cast(SessionLike, session or httpx.Client())
        self._owns_session = session is None
        self._timeout = timeout
        self._retries = max(0, retries)
        self._retry_backoff = max(0.0, retry_backoff)
        self._exchanges: deque[HttpExchange] = deque(maxlen=_MAX_EXCHANGES)

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def exchanges(self) -> tuple[HttpExchange, ...]:
        """현재 클라이언트가 기록한 인증키 제거 HTTP 교환 목록입니다."""

        return tuple(self._exchanges)

    @property
    def last_exchange(self) -> HttpExchange | None:
        """마지막 HTTP 교환 기록을 반환하고, 호출 전이면 ``None``을 반환합니다."""

        return self._exchanges[-1] if self._exchanges else None

    def clear_exchanges(self) -> None:
        """디버그 실행 단위를 나누기 위해 누적 HTTP 교환 기록을 비웁니다."""

        self._exchanges.clear()

    def close(self) -> None:
        """내부에서 만든 httpx 클라이언트를 닫습니다."""

        if not self._owns_session:
            return
        close = getattr(self._session, "close", None)
        if callable(close):
            close()

    def get_body(
        self,
        base_url: str,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        service_key_param: str = "serviceKey",
        format_param: str = "returnType",
        format_value: str = "json",
    ) -> Mapping[str, Any]:
        url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        request_params = self._params(
            params,
            service_key_param=service_key_param,
            format_param=format_param,
            format_value=format_value,
        )
        response = self._request(
            url,
            request_params,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            self._record_exchange(
                url=url,
                request_params=request_params,
                response=response,
                response_text=response.text[:300],
            )
            _raise_for_plain_text(response.text)
            raise AirKoreaParseError(f"failed to parse JSON response: {exc}") from exc

        if not isinstance(payload, Mapping):
            self._record_exchange(
                url=url,
                request_params=request_params,
                response=response,
            )
            raise AirKoreaParseError("JSON response root is not an object")

        try:
            body = _extract_body(payload, self._service_key)
        except Exception:
            self._record_exchange(
                url=url,
                request_params=request_params,
                response=response,
                response_json=payload,
            )
            raise

        self._record_exchange(
            url=url,
            request_params=request_params,
            response=response,
            response_body=body,
            response_json=payload,
        )
        return body

    def _request(self, url: str, params: Mapping[str, Any]) -> ResponseLike:
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                response = self._session.get(url, params=params, timeout=self._timeout)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < self._retries:
                    self._sleep_before_retry(attempt)
                    continue
                raise AirKoreaNetworkError(str(exc)) from None

            if response.status_code in TRANSIENT_STATUSES and attempt < self._retries:
                self._sleep_before_retry(attempt)
                continue
            _raise_for_status(response, self._service_key)
            return response

        raise AirKoreaNetworkError(str(last_error) if last_error else "request failed")

    def _params(
        self,
        params: Mapping[str, Any],
        *,
        service_key_param: str,
        format_param: str,
        format_value: str,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {
            service_key_param: self._service_key,
            format_param: format_value,
        }
        for key, value in params.items():
            if value is not None:
                query[key] = value
        return query

    def _sleep_before_retry(self, attempt: int) -> None:
        if self._retry_backoff <= 0:
            return
        import time

        delay = min(self._retry_backoff * (2**attempt), _MAX_RETRY_BACKOFF_SECONDS)
        time.sleep(delay * random.uniform(0.5, 1.5))

    def _record_exchange(
        self,
        *,
        url: str,
        request_params: Mapping[str, Any],
        response: ResponseLike,
        response_body: Mapping[str, Any] | None = None,
        response_json: Mapping[str, Any] | None = None,
        response_text: str = "",
    ) -> None:
        self._exchanges.append(
            HttpExchange(
                method="GET",
                url=url,
                request_params=sanitize_request_params(request_params),
                status_code=response.status_code,
                response_headers=_response_headers(response),
                response_body=response_body,
                response_json=response_json,
                response_text=response_text,
            )
        )


class AsyncHttpClient:
    """AirKorea 서비스군이 공유하는 httpx 기반 비동기 요청 래퍼."""

    def __init__(
        self,
        service_key: str,
        *,
        session: AsyncSessionLike | None = None,
        timeout: float = 10.0,
        retries: int = 3,
        retry_backoff: float = 0.3,
    ) -> None:
        normalized_service_key = normalize_service_key(service_key)
        if not normalized_service_key:
            raise AirKoreaAuthError("service_key is required")
        self._service_key = normalized_service_key
        self._session = cast(AsyncSessionLike, session or httpx.AsyncClient())
        self._owns_session = session is None
        self._timeout = timeout
        self._retries = max(0, retries)
        self._retry_backoff = max(0.0, retry_backoff)
        self._exchanges: deque[HttpExchange] = deque(maxlen=_MAX_EXCHANGES)

    async def __aenter__(self) -> AsyncHttpClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    @property
    def exchanges(self) -> tuple[HttpExchange, ...]:
        """현재 클라이언트가 기록한 인증키 제거 HTTP 교환 목록입니다."""

        return tuple(self._exchanges)

    @property
    def last_exchange(self) -> HttpExchange | None:
        """마지막 HTTP 교환 기록을 반환하고, 호출 전이면 ``None``을 반환합니다."""

        return self._exchanges[-1] if self._exchanges else None

    def clear_exchanges(self) -> None:
        """디버그 실행 단위를 나누기 위해 누적 HTTP 교환 기록을 비웁니다."""

        self._exchanges.clear()

    async def aclose(self) -> None:
        """내부에서 만든 httpx 비동기 클라이언트를 닫습니다."""

        if not self._owns_session:
            return
        aclose = getattr(self._session, "aclose", None)
        if callable(aclose):
            await aclose()

    async def get_body(
        self,
        base_url: str,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        service_key_param: str = "serviceKey",
        format_param: str = "returnType",
        format_value: str = "json",
    ) -> Mapping[str, Any]:
        url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        request_params = self._params(
            params,
            service_key_param=service_key_param,
            format_param=format_param,
            format_value=format_value,
        )
        response = await self._request(
            url,
            request_params,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            self._record_exchange(
                url=url,
                request_params=request_params,
                response=response,
                response_text=response.text[:300],
            )
            _raise_for_plain_text(response.text)
            raise AirKoreaParseError(f"failed to parse JSON response: {exc}") from exc

        if not isinstance(payload, Mapping):
            self._record_exchange(
                url=url,
                request_params=request_params,
                response=response,
            )
            raise AirKoreaParseError("JSON response root is not an object")

        try:
            body = _extract_body(payload, self._service_key)
        except Exception:
            self._record_exchange(
                url=url,
                request_params=request_params,
                response=response,
                response_json=payload,
            )
            raise

        self._record_exchange(
            url=url,
            request_params=request_params,
            response=response,
            response_body=body,
            response_json=payload,
        )
        return body

    async def _request(self, url: str, params: Mapping[str, Any]) -> ResponseLike:
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                response = await self._session.get(
                    url,
                    params=params,
                    timeout=self._timeout,
                )
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < self._retries:
                    await self._sleep_before_retry(attempt)
                    continue
                raise AirKoreaNetworkError(str(exc)) from None

            if response.status_code in TRANSIENT_STATUSES and attempt < self._retries:
                await self._sleep_before_retry(attempt)
                continue
            _raise_for_status(response, self._service_key)
            return response

        raise AirKoreaNetworkError(str(last_error) if last_error else "request failed")

    def _params(
        self,
        params: Mapping[str, Any],
        *,
        service_key_param: str,
        format_param: str,
        format_value: str,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {
            service_key_param: self._service_key,
            format_param: format_value,
        }
        for key, value in params.items():
            if value is not None:
                query[key] = value
        return query

    async def _sleep_before_retry(self, attempt: int) -> None:
        if self._retry_backoff <= 0:
            return
        delay = min(self._retry_backoff * (2**attempt), _MAX_RETRY_BACKOFF_SECONDS)
        await asyncio.sleep(delay * random.uniform(0.5, 1.5))

    def _record_exchange(
        self,
        *,
        url: str,
        request_params: Mapping[str, Any],
        response: ResponseLike,
        response_body: Mapping[str, Any] | None = None,
        response_json: Mapping[str, Any] | None = None,
        response_text: str = "",
    ) -> None:
        self._exchanges.append(
            HttpExchange(
                method="GET",
                url=url,
                request_params=sanitize_request_params(request_params),
                status_code=response.status_code,
                response_headers=_response_headers(response),
                response_body=response_body,
                response_json=response_json,
                response_text=response_text,
            )
        )


def _response_headers(response: ResponseLike) -> dict[str, str]:
    headers = getattr(response, "headers", {})
    if not isinstance(headers, Mapping):
        return {}
    return {str(key): str(value) for key, value in headers.items()}


def _scrub_service_key(text: str, service_key: str) -> str:
    if not service_key:
        return text
    scrubbed = text.replace(service_key, "<REDACTED>")
    encoded = urllib.parse.quote(service_key, safe="")
    if encoded != service_key:
        scrubbed = scrubbed.replace(encoded, "<REDACTED>")
    return scrubbed


def _raise_for_status(response: ResponseLike, service_key: str) -> None:
    status = response.status_code
    text = _scrub_service_key(response.text, service_key)[:300]
    if status in {401, 403}:
        raise AirKoreaAuthError(f"HTTP {status}: {text}")
    if status == 429:
        raise AirKoreaRateLimitError(f"HTTP {status}: {text}")
    if 400 <= status < 500:
        raise AirKoreaRequestError(f"HTTP {status}: {text}")
    if 500 <= status < 600:
        raise AirKoreaServerError(f"HTTP {status}: {text}")


def _raise_for_plain_text(text: str) -> None:
    upper = text.upper()
    preview = text[:300]
    auth_markers = (
        "SERVICE_KEY",
        "SERVICE KEY",
        "SERVICEKEY",
        "UNREGISTERED_IP_ERROR",
        "SERVICE_ACCESS_DENIED_ERROR",
        "UNSIGNED_CALL_ERROR",
    )
    if any(marker in upper for marker in auth_markers):
        raise AirKoreaAuthError(preview)
    if "LIMIT" in upper or "LIMITED" in upper or "초과" in text:
        raise AirKoreaRateLimitError(preview)


def _find_fault_header(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    header = payload.get("cmmMsgHeader")
    if isinstance(header, Mapping):
        return header
    service_response = payload.get("OpenAPI_ServiceResponse")
    if isinstance(service_response, Mapping):
        header = service_response.get("cmmMsgHeader")
        if isinstance(header, Mapping):
            return header
    return None


def _extract_body(payload: Mapping[str, Any], service_key: str) -> Mapping[str, Any]:
    fault_header = _find_fault_header(payload)
    if fault_header is not None:
        fault_code = str(fault_header.get("returnReasonCode", "")).strip()
        if fault_code and fault_code != "00":
            fault_message = str(
                fault_header.get("returnAuthMsg") or fault_header.get("errMsg", "")
            ).strip()
            _raise_for_result_code(fault_code, fault_message, service_key)

    response = payload.get("response")
    if not isinstance(response, Mapping):
        raise AirKoreaParseError("response object is missing")
    header = response.get("header")
    if not isinstance(header, Mapping):
        raise AirKoreaParseError("response.header object is missing")

    code = str(header.get("resultCode", "")).strip()
    message = str(header.get("resultMsg", "")).strip()
    if code != "00":
        _raise_for_result_code(code, message, service_key)

    body = response.get("body", {})
    if not isinstance(body, Mapping):
        raise AirKoreaParseError("response.body is not an object")
    return body


def _raise_for_result_code(code: str, message: str, service_key: str) -> None:
    text = _scrub_service_key(f"AirKorea API returned {code}: {message}", service_key)
    upper = message.upper()
    if code in {"20", "30", "31"} or "SERVICE_KEY" in upper:
        raise AirKoreaAuthError(text)
    if code == "03":
        raise AirKoreaNoDataError(text)
    if code == "22" or "LIMIT" in upper:
        raise AirKoreaRateLimitError(text)
    if code in {"04", "99"}:
        raise AirKoreaServerError(text)
    raise AirKoreaRequestError(text)
