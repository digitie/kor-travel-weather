"""FastAPI application factory."""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable, Mapping
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response

from kortravelweather.metrics import (
    change_http_in_flight,
    metrics_content_type,
    metrics_payload,
    observe_http_request,
)
from kortravelweather.repository import WeatherRepository, repository_from_settings
from kortravelweather.settings import WeatherSettings, get_settings

from . import __version__
from .response import Problem, request_id
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

    @api.middleware("http")
    async def request_context(request: Request, call_next: Callable[..., object]):
        request.state.request_id = request.headers.get("x-request-id") or str(uuid4())
        started = perf_counter()
        method = request.method
        change_http_in_flight(method, 1)
        response = None
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = request.state.request_id
            response.headers["x-duration-ms"] = str(
                max(0, int((perf_counter() - started) * 1000))
            )
            return response
        finally:
            # Do not count scrapes themselves: Prometheus polling must not
            # make request volume grow when it observes the request metric.
            if request.url.path != "/metrics":
                observe_http_request(
                    method=method,
                    route=getattr(request.scope.get("route"), "path", None),
                    status_code=response.status_code if response is not None else 500,
                    duration_seconds=perf_counter() - started,
                )
            change_http_in_flight(method, -1)

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
        try:
            payload = metrics_payload()
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
                if path.startswith("/v1/admin/"):
                    operation["security"] = [{"AdminToken": []}]
                    responses["401"] = problem_response("Admin authentication required")
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
