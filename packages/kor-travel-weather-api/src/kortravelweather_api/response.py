"""Small response/error helpers shared by API routers."""

from __future__ import annotations

from time import perf_counter
from typing import Any, Generic, TypeVar
from uuid import uuid4

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from kortravelweather.models import kst_now


class PageMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int
    offset: int = 0
    returned: int
    total: int | None = None


class Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    generated_at: str
    duration_ms: int
    page: PageMeta | None = None


ResponseData = TypeVar("ResponseData")


class Envelope(BaseModel, Generic[ResponseData]):
    """Typed public envelope shared by generated API clients."""

    model_config = ConfigDict(extra="forbid")

    data: ResponseData
    meta: Meta


def request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    if isinstance(value, str) and value:
        return value
    value = request.headers.get("x-request-id") or str(uuid4())
    request.state.request_id = value
    return value


def make_meta(
    request: Request,
    started_at: float,
    *,
    limit: int | None = None,
    offset: int = 0,
    returned: int | None = None,
    total: int | None = None,
) -> Meta:
    return Meta(
        request_id=request_id(request),
        generated_at=kst_now().isoformat(),
        duration_ms=max(0, int((perf_counter() - started_at) * 1000)),
        page=(
            PageMeta(limit=limit, offset=offset, returned=returned or 0, total=total)
            if limit is not None
            else None
        ),
    )


def envelope(request: Request, started_at: float, data: Any, **page: int) -> dict[str, Any]:
    return {"data": data, "meta": make_meta(request, started_at, **page).model_dump(mode="json")}


class Problem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "about:blank"
    title: str
    status: int
    detail: str
    code: str
    request_id: str
    errors: list[dict[str, Any]] = Field(default_factory=list)
