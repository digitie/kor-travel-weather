"""2026-11-18..24 운영 실패(ALERT_PAGE_DUPLICATE)를 실제 getPwnCd 응답으로 재현한다.

fixture는 같은 요청 구간(stnId=108, 20261120..20261123)을 2026-11-24에 두 방식으로 받은 것이다.

- ``single_page``: numOfRows=1000 한 페이지. 272행, 행 중복 없음.
- ``pages_100``: numOfRows=100 세 페이지. 각 행을 ``single_page.rows``의 index로 적었다.
  같은 발표 시각의 순서가 요청마다 고정되지 않아 페이지 경계 행이 다시 나오고 그만큼 빠진다.

또 당진(L1031200) 특보는 원천에 지역명 두 개(당진시·당진군)로 같은 관측이 두 행씩 있다.
"""

from __future__ import annotations

import json
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from kma import DataGoKrClient
from kortravelweather_dagster import alert_events as module

from kortravelweather.alert_events import normalize_alert, reconcile_alert_rows
from kortravelweather.repository import WeatherRepository

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "kma_getpwncd_20261120_20261123.json").read_text(
        encoding="utf-8"
    )
)
ROWS = FIXTURE["single_page"]["rows"]
#: fixture를 받은 실제 요청의 numOfRows(``_comment``의 ``single_page``). 원천이 이 값으로 요청한
#: 한 페이지에 모든 행을 돌려준 것만 측정됐다.
MEASURED_NUM_OF_ROWS = 1000


def response(rows):
    return httpx.Response(200, json={"response": {
        "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
        "body": {
            "items": {"item": rows}, "pageNo": 1, "numOfRows": MEASURED_NUM_OF_ROWS,
            "totalCount": FIXTURE["single_page"]["totalCount"],
        },
    }})


def test_collection_requests_only_the_measured_page_size():
    """측정하지 않은 numOfRows로 요청하면 원천 상한·축소에 모든 run이 걸릴 수 있다."""
    from kortravelweather.alert_events import ALERT_MAX_ROWS

    assert f"numOfRows={MEASURED_NUM_OF_ROWS} response rows" in FIXTURE["_comment"]
    assert module.PAGE_SIZE == MEASURED_NUM_OF_ROWS
    assert module.PAGE_SIZE <= ALERT_MAX_ROWS


@asynccontextmanager
async def sdk(handler):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as session:
        client = DataGoKrClient("fixture-only-key", session=session, retries=0, max_rps=1000)
        try:
            yield client
        finally:
            await client.aclose()


@pytest.fixture
def repo(weather_test_database_url):
    result = WeatherRepository(weather_test_database_url)
    result.create_schema()
    try:
        yield result
    finally:
        result.engine.dispose()


def test_hundred_row_pages_repeat_boundary_rows_and_drop_others():
    """페이지 분할을 하지 않는 근거. 반복 행을 병합해도 빠진 행은 복구되지 않는다."""
    pages = FIXTURE["pages_100"]
    seen = Counter(index for page in pages for index in page)
    assert [len(page) for page in pages] == [100, 100, 72]
    assert sum(seen.values()) == FIXTURE["single_page"]["totalCount"] == len(ROWS) == 272
    repeated = {index for index, count in seen.items() if count > 1}
    missing = set(range(len(ROWS))) - set(seen)
    assert len(repeated) == len(missing) == 29
    # 반복은 페이지 경계 근처에서 다음 페이지 앞부분에 다시 나온다.
    assert repeated <= set(pages[0][-40:]) | set(pages[1][-40:])


def test_single_page_has_one_conflicting_area_name_pair_per_dangjin_observation():
    result = reconcile_alert_rows(ROWS)
    assert result.duplicates_merged == 0
    assert [group.area_code for group in result.quarantined] == ["L1031200", "L1031200"]
    assert result.quarantined_rows == 4
    for group in result.quarantined:
        names = {row["areaName"] for row in group.rows}
        assert names == {"당진시", "당진군"}
        # 지역명만 다르고 관측 식별 필드는 같다.
        assert len({normalize_alert(row).event_id for row in group.rows}) == 1
    assert len(result.accepted) == len(result.events) == 268


def test_reconciliation_merges_page_boundary_repeats_but_cannot_restore_dropped_rows():
    paged = [ROWS[index] for page in FIXTURE["pages_100"] for index in page]
    result = reconcile_alert_rows(paged)
    # 반복 29행 중 2행은 당진 이중 지역명 묶음에 속해 병합이 아니라 격리로 셈한다.
    assert result.duplicates_merged == 27
    # 단일 페이지의 268개 게시 대상 중 페이지 분할이 빠뜨린 관측은 끝내 없다.
    single = {event.event_id for event in reconcile_alert_rows(ROWS).events}
    assert len(single - {event.event_id for event in result.events}) > 0


NOV10 = json.loads(
    (Path(__file__).parent / "fixtures" / "kma_getpwncd_20261107_20261110.json").read_text(
        encoding="utf-8"
    )
)


def test_nov10_episode_boundary_fell_inside_one_announcement():
    """2026-11-06..10 ALERT_PAGE_DUPLICATE(837건)의 조건: 100행 경계가 한 tmFc 묶음 안이었다.

    그 구간에는 당진 같은 내용 충돌이 없었다. 실패는 원천 순서가 흔들린 run에서만 났고
    (같은 날에도 성공이 섞였다) 경계가 묶음 밖으로 나가거나 순서가 우연히 맞으면 멎었다.
    """
    rows = NOV10["single_page"]["rows"]
    assert len(rows) == NOV10["single_page"]["totalCount"] == 174
    assert rows[99]["tmFc"] == rows[100]["tmFc"]
    result = reconcile_alert_rows(rows)
    assert result.quarantined == [] and result.duplicates_merged == 0


async def test_nov10_episode_is_one_request_and_publishes_every_row(repo):
    rows = NOV10["single_page"]["rows"]
    calls = []

    def handler(request):
        calls.append(request.url.params["pageNo"])
        assert request.url.params["numOfRows"] == str(MEASURED_NUM_OF_ROWS)
        return httpx.Response(200, json={"response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
            "body": {"items": {"item": rows}, "pageNo": 1, "numOfRows": MEASURED_NUM_OF_ROWS,
                     "totalCount": len(rows)},
        }})

    async with sdk(handler) as client:
        result = await module.run_alert_event_sync_async(repository=repo, client=client)
    assert calls == ["1"]
    assert result["status"] == "success" and result["events_loaded"] == 174
    assert result["quarantined_rows"] == result["duplicates_merged"] == 0


@pytest.mark.parametrize("format", ["json", "xml"])
async def test_nov14_window_without_alerts_is_an_empty_success(repo, format):
    """2026-11-14 15:03Z~11-15 03:30Z ALERT_PAGE_METADATA(152건)의 응답.

    KST 자정에 요청 창(11-12..11-15)에 발표가 하나도 없어지자 원천이 NO_DATA(resultCode 03)를
    돌려줬고, SDK가 pageNo/numOfRows 없는 빈 body로 정규화했다. #239가 이 모양을 빈 성공으로
    받기 시작한 03:30Z 배포 뒤 멎었다. 한 요청 수집에서도 같은 응답은 빈 성공이다.
    """
    def handler(_request):
        if format == "xml":
            return httpx.Response(200, text=(
                "<OpenAPI_ServiceResponse><cmmMsgHeader><resultCode>03</resultCode>"
                "<resultMsg>NODATA_ERROR</resultMsg></cmmMsgHeader></OpenAPI_ServiceResponse>"
            ), headers={"content-type": "application/xml"})
        return httpx.Response(200, json={"response": {
            "header": {"resultCode": "03", "resultMsg": "NODATA_ERROR"},
        }})

    async with sdk(handler) as client:
        result = await module.run_alert_event_sync_async(repository=repo, client=client)
    assert result["status"] == "success" and result["observations"] == 0
    assert result["requests_fetched"] == 1 and result["quarantined_rows"] == 0


async def test_real_response_publishes_all_but_the_quarantined_observations(repo):
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        return response(ROWS)

    async with sdk(handler) as client:
        result = await module.run_alert_event_sync_async(repository=repo, client=client)
    assert len(calls) == 1 and calls[0]["pageNo"] == "1"
    assert calls[0]["numOfRows"] == str(MEASURED_NUM_OF_ROWS)
    assert calls[0]["fromTmFc"] < calls[0]["toTmFc"]
    assert result["status"] == "success" and result["events_loaded"] == 268
    assert result["quarantined_events"] == 2 and result["quarantined_rows"] == 4
    assert result["quarantined_area_codes"] == "L1031200"
    assert result["duplicates_merged"] == 0
    run = repo.list_sync_runs(provider=module.ALERT_PROVIDER, dataset_key=module.ALERT_DATASET)[0]
    assert run.status == "success" and run.values_loaded == 268
