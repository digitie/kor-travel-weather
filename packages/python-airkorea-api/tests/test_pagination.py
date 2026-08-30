from __future__ import annotations

import pytest

from airkorea.models import AirKoreaPage
from airkorea.pagination import has_next_page, iter_paginated_pages, next_page_no


def test_body_pagination_helpers() -> None:
    body = {"pageNo": "2", "numOfRows": "10", "totalCount": "25"}

    assert has_next_page(body)
    assert next_page_no(body) == 3
    assert not has_next_page({"pageNo": "3", "numOfRows": "10", "totalCount": "25"})


def test_iter_paginated_pages_follows_page_metadata() -> None:
    seen: list[tuple[int, int]] = []

    def fetch_page(page_no: int, num_of_rows: int) -> AirKoreaPage[str]:
        seen.append((page_no, num_of_rows))
        return AirKoreaPage[str](
            items=(f"page-{page_no}",),
            total_count=2,
            page_no=page_no,
            num_of_rows=num_of_rows,
            raw={},
        )

    pages = list(iter_paginated_pages(fetch_page, num_of_rows=1))

    assert [page.items[0] for page in pages] == ["page-1", "page-2"]
    assert seen == [(1, 1), (2, 1)]


def test_iter_paginated_pages_validates_guards() -> None:
    def fetch_page(page_no: int, num_of_rows: int) -> AirKoreaPage[str]:
        return AirKoreaPage[str](
            items=("x",),
            total_count=1,
            page_no=page_no,
            num_of_rows=num_of_rows,
            raw={},
        )

    with pytest.raises(ValueError):
        list(iter_paginated_pages(fetch_page, page_no=0))

    with pytest.raises(ValueError):
        list(iter_paginated_pages(fetch_page, max_items=-1))
