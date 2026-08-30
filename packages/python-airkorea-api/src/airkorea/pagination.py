"""data.go.kr 형식 AirKorea 응답 body용 페이지 순회 헬퍼."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from typing import Any, TypeVar

from airkorea.exceptions import AirKoreaNoDataError
from airkorea.models import AirKoreaPage

T = TypeVar("T")


def has_next_page(body: Mapping[str, Any]) -> bool:
    """data.go.kr 응답 body에 다음 페이지가 있는지 반환합니다."""

    page_no = _int_from_body(body, "pageNo", default=1)
    num_of_rows = _int_from_body(body, "numOfRows", default=0)
    total_count = _int_from_body(body, "totalCount", default=0)
    if page_no < 1 or num_of_rows < 1 or total_count < 1:
        return False
    return page_no * num_of_rows < total_count


def next_page_no(body: Mapping[str, Any]) -> int | None:
    """다음 페이지 번호를 반환하고, 마지막 페이지이면 ``None``을 반환합니다."""

    if not has_next_page(body):
        return None
    return _int_from_body(body, "pageNo", default=1) + 1


def iter_paginated_pages(
    fetch_page: Callable[[int, int], AirKoreaPage[T]],
    *,
    page_no: int = 1,
    num_of_rows: int = 100,
    max_pages: int | None = None,
    max_items: int | None = None,
    min_request_interval: float = 0.0,
) -> Iterator[AirKoreaPage[T]]:
    """페이지 메타데이터가 끝을 가리킬 때까지 AirKorea 페이지를 순회합니다."""

    _validate_pagination_input(
        page_no=page_no,
        num_of_rows=num_of_rows,
        max_pages=max_pages,
        max_items=max_items,
        min_request_interval=min_request_interval,
    )
    current_page_no = page_no
    pages_seen = 0
    items_seen = 0

    while True:
        if max_pages is not None and pages_seen >= max_pages:
            return
        if max_items is not None and items_seen >= max_items:
            return

        if pages_seen > 0 and min_request_interval > 0:
            time.sleep(min_request_interval)

        try:
            page = fetch_page(current_page_no, num_of_rows)
        except AirKoreaNoDataError:
            return
        if page.is_empty:
            return

        yield page
        pages_seen += 1
        items_seen += len(page.items)

        if not page.has_next_page:
            return
        current_page_no += 1


async def aiter_paginated_pages(
    fetch_page: Callable[[int, int], Awaitable[AirKoreaPage[T]]],
    *,
    page_no: int = 1,
    num_of_rows: int = 100,
    max_pages: int | None = None,
    max_items: int | None = None,
    min_request_interval: float = 0.0,
) -> AsyncIterator[AirKoreaPage[T]]:
    """페이지 메타데이터가 끝을 가리킬 때까지 AirKorea 페이지를 비동기로 순회합니다."""

    _validate_pagination_input(
        page_no=page_no,
        num_of_rows=num_of_rows,
        max_pages=max_pages,
        max_items=max_items,
        min_request_interval=min_request_interval,
    )
    current_page_no = page_no
    pages_seen = 0
    items_seen = 0

    while True:
        if max_pages is not None and pages_seen >= max_pages:
            return
        if max_items is not None and items_seen >= max_items:
            return

        if pages_seen > 0 and min_request_interval > 0:
            await asyncio.sleep(min_request_interval)

        try:
            page = await fetch_page(current_page_no, num_of_rows)
        except AirKoreaNoDataError:
            return
        if page.is_empty:
            return

        yield page
        pages_seen += 1
        items_seen += len(page.items)

        if not page.has_next_page:
            return
        current_page_no += 1


def _validate_pagination_input(
    *,
    page_no: int,
    num_of_rows: int,
    max_pages: int | None,
    max_items: int | None,
    min_request_interval: float = 0.0,
) -> None:
    if page_no < 1:
        raise ValueError("page_no must be >= 1")
    if num_of_rows < 1:
        raise ValueError("num_of_rows must be >= 1")
    if max_pages is not None and max_pages < 0:
        raise ValueError("max_pages must be >= 0")
    if max_items is not None and max_items < 0:
        raise ValueError("max_items must be >= 0")
    if min_request_interval < 0:
        raise ValueError("min_request_interval must be >= 0")


def _int_from_body(body: Mapping[str, Any], key: str, *, default: int) -> int:
    try:
        return int(str(body.get(key, default)).strip())
    except (TypeError, ValueError):
        return default
