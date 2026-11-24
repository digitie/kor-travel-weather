"""공개 조회(``/v1/weather/*``) 응답을 worker thread에서 bytes로 만든다(호출 검토 B3).

예전에는 handler가 event loop에서 DTO를 dict로 바꾸고(``model_dump``), FastAPI가 그 dict를
``response_model``로 다시 검증·직렬화했다(async handler라 둘 다 event loop). 마커 60곳
(최대 4,800행) 같은 큰 응답이면 그동안 event loop가 멈춰 다른 요청·관리 proxy·``/health``가
함께 기다렸다.

지금은 handler가 DB 읽기부터 DTO 생성·JSON 직렬화·gzip까지를 한 worker 함수로 넘기고
``Response(bytes)``를 돌려준다. typed DTO를 ``TypeAdapter.dump_json``으로 바로 bytes로 만들므로
같은 값을 다시 검증하지 않는다. OpenAPI 문서는 route의 ``response_model``이 그대로 만든다.

gzip은 ``Accept-Encoding``이 허용하고 본문이 :data:`GZIP_MINIMUM_BYTES` 이상일 때만 worker
thread에서 한다(event loop에서 압축하는 ``GZipMiddleware``는 쓰지 않는다). 압축 여부와 관계없이
``Vary: Accept-Encoding``을 붙인다.

내용 ETag(:func:`data_etag`)를 쓰는 handler는 ``If-None-Match``가 맞으면 :class:`NotModified`를
돌려주고, :func:`respond`가 본문 없는 304로 바꾼다(위치 카탈로그, 호출 검토 B12).
"""

from __future__ import annotations

import gzip
import hashlib
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from functools import cache
from time import perf_counter
from typing import Any

from fastapi import Request, Response
from pydantic import BaseModel, TypeAdapter
from starlette.concurrency import run_in_threadpool

from kortravelweather.models import kst_now
from kortravelweather.read_performance import read_stage

from .response import BundleMeta, Meta, PageMeta, request_id

#: 이보다 작은 본문은 압축하지 않는다(한 패킷 안이라 이득보다 CPU가 크다).
GZIP_MINIMUM_BYTES = 1024
#: service export와 같은 수준. 압축률보다 worker 점유 시간을 줄인다.
_GZIP_LEVEL = 4

Rendered = tuple[bytes, dict[str, str]]


def accepts_gzip(header: str) -> bool:
    """``Accept-Encoding``이 gzip을 ``q=0`` 없이 허용하면 참이다."""
    for token in header.lower().split(","):
        parts = [part.strip() for part in token.split(";")]
        if parts[0] != "gzip":
            continue
        try:
            return all(float(part[2:]) > 0 for part in parts[1:] if part.startswith("q="))
        except ValueError:
            return False
    return False


@dataclass(frozen=True, slots=True)
class PublicRequest:
    """worker thread에 넘길 요청 값. ``Request`` 객체는 thread로 넘기지 않는다."""

    request_id: str
    started: float
    accept_encoding: str

    @classmethod
    def of(cls, request: Request) -> PublicRequest:
        return cls(
            request_id=request_id(request),
            started=perf_counter(),
            accept_encoding=request.headers.get("accept-encoding", ""),
        )

    def meta(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        returned: int | None = None,
        total: int | None = None,
        bundle: BundleMeta | None = None,
        alert_scope_unverified: bool = False,
    ) -> Meta:
        """``response.make_meta``와 같은 값. 처리 시간은 직렬화 직전까지다(예전과 같다)."""
        return Meta(
            request_id=self.request_id,
            generated_at=kst_now().isoformat(),
            duration_ms=max(0, int((perf_counter() - self.started) * 1000)),
            page=(
                PageMeta(limit=limit, offset=offset, returned=returned or 0, total=total)
                if limit is not None
                else None
            ),
            bundle=bundle,
            alert_scope_unverified=alert_scope_unverified,
        )


@dataclass(frozen=True, slots=True)
class NotModified:
    """``If-None-Match``가 지금 ETag와 같아 본문 없이 304로 답한다(오류가 아니다)."""

    etag: str


@cache
def _adapter(model: Any) -> TypeAdapter[Any]:
    return TypeAdapter(model)


def data_etag(data_type: Any, data: Any, *parts: Any) -> str:
    """``data``와 페이지 식별 ``parts``만으로 만든 약한 ETag.

    요청마다 바뀌는 meta(``request_id``·``generated_at``·``duration_ms``)는 넣지 않는다. gzip
    여부와 무관한 의미 validator라 약한(``W/``) ETag다. 같은 URL의 본문 내용이 같으면 같다.
    """
    digest = hashlib.blake2b(_adapter(data_type).dump_json(data), digest_size=16)
    for part in parts:
        digest.update(b"\x00" + str(part).encode())
    return f'W/"{digest.hexdigest()}"'


def render(
    public: PublicRequest,
    model: type[BaseModel],
    data: Any,
    meta: Meta,
    *,
    etag: str | None = None,
) -> Rendered:
    """``model``(``Envelope[...]``) 본문을 JSON bytes로 만들고 필요하면 gzip한다."""
    with read_stage("render"):
        body = model(data=data, meta=meta)
        payload = _adapter(model).dump_json(body)
        headers = {"vary": "Accept-Encoding"}
        if etag is not None:
            headers["etag"] = etag
        if len(payload) >= GZIP_MINIMUM_BYTES and accepts_gzip(public.accept_encoding):
            payload = gzip.compress(payload, compresslevel=_GZIP_LEVEL, mtime=0)
            headers["content-encoding"] = "gzip"
        return payload, headers


async def respond(work: Callable[[], Rendered | NotModified]) -> Response:
    """``work``(DB 읽기·변환·직렬화)를 worker thread에서 실행해 응답으로 만든다."""
    result = await run_in_threadpool(work)
    if isinstance(result, NotModified):
        return Response(status_code=304, headers={"etag": result.etag, "vary": "Accept-Encoding"})
    payload, headers = result
    return Response(payload, media_type="application/json", headers=headers)


def read_scope(repo: Any) -> AbstractContextManager[Any]:
    """repository의 공유 읽기 묶음. 그 기능이 없는 repository double은 그대로 둔다."""
    scope = getattr(repo, "read_scope", None)
    return scope() if callable(scope) else nullcontext()
