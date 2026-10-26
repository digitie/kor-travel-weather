"""휴게소 날씨는 페이지 없는 전체 snapshot이다. 그 완결 증거로만 export를 열어 둔다.

운영 실측(2026-10-27) restWeatherList 응답은 ``{"code": "SUCCESS", "count": 187,
"list": [187행]}``이고 pageNo/numOfRows가 없다. catalog roster도 187곳이다.
"""

from types import SimpleNamespace

import pytest
from krex import parse_rest_area_weather

from kortravelweather.export_sdk_collection import SdkCollectionContext


def _rows(count):
    return [
        {"sdate": "20261027", "stdHour": "06", "unitCode": f"{index:03d}",
         "unitName": f"휴게소{index}", "xValue": "127.1", "yValue": "37.3",
         "tempValue": "12.5", "humidityValue": "60", "weatherContents": "맑음"}
        for index in range(count)
    ]


def _page(rows, *, count=None, **paging):
    raw = {"code": "SUCCESS", "count": len(rows) if count is None else count, "list": rows}
    items = tuple(parse_rest_area_weather(row) for row in rows)
    return SimpleNamespace(
        items=items, raw=raw, total_count=raw["count"],
        page_no=paging.get("page_no"), num_of_rows=paging.get("num_of_rows"),
    )


def _context(enabled, disabled=0):
    return SdkCollectionContext(
        collector=None,
        plans={f"krex-{index}": None for index in range(enabled)},
        ignored_ids={f"krex-off-{index}" for index in range(disabled)},
    )


@pytest.mark.parametrize("rows", [2, 3])
def test_unpaged_snapshot_matching_roster_keeps_export_open(rows):
    # 활성 2곳은 모두 있어야 하고, 비활성 1곳은 응답에 있어도 없어도 된다.
    context = _context(enabled=2, disabled=1)
    context.observe_krex_page(_page(_rows(rows)))
    assert context.complete


@pytest.mark.parametrize(
    "page",
    [
        # count가 원문 행 수와 다르다(전송 중 잘림).
        lambda: _page(_rows(3), count=4),
        # 페이지 필드가 하나만 있는 응답은 전체 snapshot도, 완결 페이지도 아니다.
        lambda: _page(_rows(3), page_no=1),
        lambda: _page(_rows(3), num_of_rows=1000),
        # 전체 snapshot이 활성 roster보다 작거나 활성+비활성 roster보다 크다.
        lambda: _page(_rows(1)),
        lambda: _page(_rows(4)),
    ],
)
def test_unpaged_snapshot_without_complete_evidence_closes_export(page):
    context = _context(enabled=2, disabled=1)
    context.observe_krex_page(page())
    assert not context.complete


def test_paged_envelope_keeps_the_paged_receipt():
    context = _context(enabled=3)
    context.observe_krex_page(_page(_rows(3), page_no=1, num_of_rows=1000))
    assert context.complete
