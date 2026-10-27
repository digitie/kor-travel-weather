"""FastAPI application factory."""

from __future__ import annotations

import hmac
import logging
from collections.abc import Mapping
from time import perf_counter
from uuid import uuid4

from anyio import CapacityLimiter, to_thread
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from kortravelweather.metrics import (
    change_http_in_flight,
    forward_partition_days_exposition,
    metrics_content_type,
    metrics_payload,
    observe_http_request,
    observe_metric_error,
    oldest_partition_age_exposition,
)
from kortravelweather.repository import WeatherRepository, repository_from_settings
from kortravelweather.settings import WeatherSettings, get_settings

from . import __version__
from .response import Problem, request_id
from .routers.service_exports import router as service_export_router
from .routers.weather import admin_router, router

logger = logging.getLogger(__name__)


def _safe_errors(raw_errors: object) -> list[dict[str, object]]:
    if not isinstance(raw_errors, list):
        return []
    safe: list[dict[str, object]] = []
    for item in raw_errors:
        if not isinstance(item, Mapping):
            continue
        location = item.get("loc", [])
        safe.append(
            {
                "loc": (
                    [str(part) for part in location] if isinstance(location, (list, tuple)) else []
                ),
                "msg": str(item.get("msg", "요청 값이 올바르지 않습니다.")),
                "type": str(item.get("type", "value_error")),
            }
        )
    return safe


class RequestContextMiddleware:
    """요청 ID·처리 시간 header와 HTTP metric을 남기는 순수 ASGI middleware.

    ``receive``를 감싸지 않는다. ``BaseHTTPMiddleware``는 하위 앱의 receive를 task group으로
    감싸 ``Request.is_disconnected()``가 연결 끊김 메시지를 잃고 늘 False를 반환했다.
    그러면 지도 이동으로 취소된 marker 배치도 limiter 뒤에서 DB 조회를 시작한다.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = Headers(scope=scope).get("x-request-id") or str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = perf_counter()
        method = scope["method"]
        status_code = 500
        change_http_in_flight(method, 1)

        async def send_with_context(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers["x-request-id"] = request_id
                headers["x-duration-ms"] = str(max(0, int((perf_counter() - started) * 1000)))
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        finally:
            # Do not count scrapes themselves: Prometheus polling must not
            # make request volume grow when it observes the request metric.
            if scope.get("path") != "/metrics":
                observe_http_request(
                    method=method,
                    route=getattr(scope.get("route"), "path", None),
                    status_code=status_code,
                    duration_seconds=perf_counter() - started,
                )
            change_http_in_flight(method, -1)


def _problem(
    request: Request,
    status: int,
    title: str,
    detail: str,
    code: str,
    *,
    errors: list[dict[str, object]] | None = None,
) -> JSONResponse:
    body = Problem(
        title=title,
        status=status,
        detail=detail,
        code=code,
        request_id=request_id(request),
        errors=errors or [],
    ).model_dump(mode="json")
    return JSONResponse(status_code=status, content=body, media_type="application/problem+json")


def create_app(
    settings: WeatherSettings | None = None,
    repository: WeatherRepository | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    if runtime_settings.is_production:
        # Do this during app construction so a misconfigured deployment never
        # starts an unauthenticated admin surface.
        runtime_settings.require_admin_token()
        runtime_settings.require_metrics_token()
    runtime_repository = repository or repository_from_settings(runtime_settings)
    api = FastAPI(
        title="kor-travel-weather API",
        version=__version__,
        description="Provider-independent Korean weather facts and forecasts.",
    )
    api.state.settings = runtime_settings
    api.state.repository = runtime_repository
    # 지도 이동의 이전 배치가 DB·일반 worker 슬롯을 독점하지 않도록 제한한다.
    api.state.marker_limiter = CapacityLimiter(2)
    api.state.service_export_limiter = CapacityLimiter(2)
    # 인증 조회는 긴 일반 조회가 모두 worker를 사용해도 별도로 처리한다.
    api.state.auth_limiter = CapacityLimiter(2)
    # 수집 버퍼는 하나만 만들고 대기는 일반 API의 worker 슬롯을 쓰지 않는다.
    metrics_limiter = CapacityLimiter(1)
    if not runtime_settings.is_production:
        runtime_repository.create_schema()

    if runtime_settings.cors_origins:
        api.add_middleware(
            CORSMiddleware,
            allow_origins=runtime_settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["*"],
        )

    # CORS 다음에 등록해 기존과 같이 가장 바깥(ServerErrorMiddleware 안쪽)에 둔다.
    api.add_middleware(RequestContextMiddleware)

    @api.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "요청 처리 실패"
        errors = _safe_errors(exc.detail)
        return _problem(
            request,
            exc.status_code,
            "요청 처리 실패",
            detail,
            "HTTP_ERROR",
            errors=errors,
        )

    @api.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(
            request,
            422,
            "요청 검증 실패",
            "요청 값이 API 계약에 맞지 않습니다.",
            "VALIDATION_ERROR",
            errors=_safe_errors(exc.errors()),
        )

    @api.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "kor-travel-weather", "version": __version__}

    @api.get("/version", tags=["system"])
    async def version() -> dict[str, str | None]:
        return {
            "service": "kor-travel-weather",
            "version": __version__,
            "git_commit": runtime_settings.git_commit,
        }

    @api.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        """Return aggregate Prometheus samples without catalog identifiers."""
        expected = runtime_settings.require_metrics_token()
        if expected:
            authorization = request.headers.get("authorization", "")
            supplied = (
                authorization[7:].strip()
                if authorization.lower().startswith("bearer ")
                else request.headers.get("x-metrics-token", "")
            )
            if not supplied or not hmac.compare_digest(supplied, expected):
                return Response(
                    status_code=401,
                    content="metrics authentication required\n",
                    media_type="text/plain",
                    headers={
                        "cache-control": "no-store, private",
                        "www-authenticate": "Bearer",
                        "x-content-type-options": "nosniff",
                    },
                )
        forward_days = getattr(runtime_repository, "forward_partition_days", None)
        oldest_age = getattr(runtime_repository, "oldest_partition_age_days", None)
        forward_sample = b""
        if callable(forward_days):
            # A catalog read, refreshed per scrape so the alert sees the
            # partitions that exist now rather than at the last nightly run.
            # Only this scrape carries the sample; see the function's docstring.
            # The retention staleness gauge reads the same catalog and is
            # omitted with it, so one absent() rule covers both.
            try:
                sample = forward_partition_days_exposition(await run_in_threadpool(forward_days))
                if callable(oldest_age):
                    sample += oldest_partition_age_exposition(
                        await run_in_threadpool(oldest_age)
                    )
                forward_sample = sample
            except Exception:
                # The sample is omitted, so the scrape still succeeds and `up`
                # stays 1; KorTravelWeatherForwardPartitionsUnknown catches the
                # gap and this counter says why.
                observe_metric_error("forward_partitions")
                logger.warning("forward partition gauge refresh failed", exc_info=True)
        try:
            # mmap 병합/정리가 느려져도 생존·일반 API의 이벤트 루프를 막지 않는다.
            payload = (
                await to_thread.run_sync(metrics_payload, limiter=metrics_limiter)
            ) + forward_sample
        except Exception:
            logger.exception("Prometheus metrics collection failed")
            return Response(
                status_code=500,
                content="metrics collection failed\n",
                media_type="text/plain",
                headers={
                    "cache-control": "no-store, private",
                    "x-content-type-options": "nosniff",
                },
            )
        return Response(
            content=payload,
            media_type=metrics_content_type(),
            headers={
                "cache-control": "no-store, private",
                "x-content-type-options": "nosniff",
            },
        )

    api.include_router(router)
    api.include_router(admin_router)
    api.include_router(service_export_router)

    def custom_openapi() -> dict[str, object]:
        if api.openapi_schema:
            return api.openapi_schema
        schema = get_openapi(
            title=api.title,
            version=api.version,
            description=api.description,
            routes=api.routes,
        )
        components = schema.setdefault("components", {})
        schemas = components.setdefault("schemas", {})
        components["securitySchemes"] = {
            "AdminToken": {
                "type": "apiKey",
                "in": "header",
                "name": "x-admin-token",
                "description": "관리자 API token (server-side only)",
            }
        }
        schemas["Problem"] = Problem.model_json_schema(ref_template="#/components/schemas/{model}")

        def problem_response(description: str) -> dict[str, object]:
            return {
                "description": description,
                "content": {
                    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
                },
            }

        for path, path_item in schema.get("paths", {}).items():
            if not path.startswith("/v1/"):
                continue
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                responses = operation.setdefault("responses", {})
                responses["422"] = problem_response("Request validation failed")
                if path.startswith(("/v1/admin/", "/v1/service/")):
                    operation["security"] = [{"AdminToken": []}]
                    responses["401"] = problem_response("Admin authentication required")
                if path.startswith("/v1/service/") and "503" in responses:
                    response = responses["503"]
                    response["content"] = {"application/problem+json": {
                        "schema": {"$ref": "#/components/schemas/ExportUnavailable"},
                    }}
                if "{location_id}" in path or "{run_id}" in path:
                    responses["404"] = problem_response("Resource not found")
                operation_name = operation.get("operationId", "").split("_")[0]
                if path.startswith("/v1/admin/") and (
                    operation_name in {"create", "patch", "put", "delete"}
                    or (
                        "provider-credentials" in path
                        and operation_name in {"put", "delete"}
                    )
                ):
                    responses["409"] = problem_response("Resource conflict")
        api.openapi_schema = schema
        return schema

    api.openapi = custom_openapi
    return api


app = create_app()

__all__ = ["app", "create_app"]
