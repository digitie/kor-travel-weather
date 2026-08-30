"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import Callable
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kortravelweather.repository import WeatherRepository, repository_from_settings
from kortravelweather.settings import WeatherSettings, get_settings

from . import __version__
from .response import Problem, request_id
from .routers.weather import admin_router, router

logger = logging.getLogger(__name__)


def _problem(request: Request, status: int, title: str, detail: str, code: str) -> JSONResponse:
    body = Problem(
        title=title, status=status, detail=detail, code=code, request_id=request_id(request)
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
            allow_methods=["GET", "POST", "PATCH"],
            allow_headers=["*"],
        )

    @api.middleware("http")
    async def request_context(request: Request, call_next: Callable[..., object]):
        request.state.request_id = request.headers.get("x-request-id") or str(uuid4())
        started = perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        response.headers["x-duration-ms"] = str(max(0, int((perf_counter() - started) * 1000)))
        return response

    @api.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return _problem(request, exc.status_code, "요청 처리 실패", str(exc.detail), "HTTP_ERROR")

    @api.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(
            request,
            422,
            "요청 검증 실패",
            "요청 값이 API 계약에 맞지 않습니다.",
            "VALIDATION_ERROR",
        )

    @api.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "kor-travel-weather", "version": __version__}

    @api.get("/version", tags=["system"])
    async def version() -> dict[str, str | None]:
        return {"service": "kor-travel-weather", "version": __version__, "git_commit": runtime_settings.git_commit}

    api.include_router(router)
    api.include_router(admin_router)
    return api


app = create_app()

__all__ = ["app", "create_app"]
