"""W3 meta의 멤버십 digest와 clock epoch(2026-11-11, Map ADR-122 요청 1·2).

Map은 ``since`` 증분을 병합한 상태의 같은 digest와 비교해 다르면 전량을 받는다. 그래서 digest는
얼린 manifest 전체(시간 필터 전)여야 하고 ``since``와 무관해야 하며, 소비자가 응답 item만으로
같은 값을 계산할 수 있어야 한다.
"""

import hashlib
import json
from datetime import UTC, datetime

from test_frozen_service_exports import HEADERS, _client, _collect_forecast, repository

# sibling fixture를 명시 export한다.
repository = repository

URL = "/v1/service/exports/forecasts"


def _walk(client, params):
    pages = [client.get(URL, params=params, headers=HEADERS)]
    while pages[-1].json()["meta"]["next_cursor"]:
        assert pages[-1].status_code == 200, pages[-1].text
        pages.append(client.get(URL, params={
            **params, "cursor": pages[-1].json()["meta"]["next_cursor"],
        }, headers=HEADERS))
    assert all(page.status_code == 200 for page in pages)
    return pages


def _consumer_digest(items):
    """소비자(Map)가 응답 item만으로 계산하는 방법. 문서의 정의를 그대로 따른다."""
    rows = sorted(
        (item["location_id"], item["provider"], datetime.fromisoformat(
            item["published_at"].replace("Z", "+00:00"),
        ).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
        for item in items
    )
    body = json.dumps([list(row) for row in rows], ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def test_forecast_meta_carries_full_membership_independent_of_since(repository, tmp_path):
    _collect_forecast(repository, tmp_path)
    client = _client(repository, tmp_path)
    params = {"style": "ultra_short", "limit": 1}
    pages = _walk(client, params)
    metas = [page.json()["meta"] for page in pages]
    digest = metas[0]["membership_digest"]
    assert digest and digest.startswith("sha256:")
    # 모든 페이지가 같은 값이고, 전량 item만으로 소비자가 같은 값을 얻는다.
    assert {meta["membership_digest"] for meta in metas} == {digest}
    items = [item for page in pages for item in page.json()["items"]]
    assert _consumer_digest(items) == digest
    # since 증분(여기서는 바뀐 것이 없어 빈 응답)도 전체 멤버십 digest를 그대로 싣는다.
    delta = client.get(URL, params={**params, "since": metas[0]["next_since"]}, headers=HEADERS)
    assert delta.status_code == 200 and delta.json()["items"] == []
    assert delta.json()["meta"]["membership_digest"] == digest


def test_forecast_meta_clock_epoch_is_stable_and_shared(repository, tmp_path):
    _collect_forecast(repository, tmp_path)
    client = _client(repository, tmp_path)
    params = {"style": "ultra_short", "limit": 1}
    first = client.get(URL, params=params, headers=HEADERS).json()["meta"]
    again = client.get(URL, params=params, headers=HEADERS).json()["meta"]
    assert first["clock_epoch"] and first["clock_epoch"].startswith("pg:")
    assert first["clock_epoch"] == again["clock_epoch"]
    from kortravelweather.export_forecasts import clock_epoch

    with repository.engine.connect() as connection:
        assert clock_epoch(connection) == first["clock_epoch"]


def test_membership_digest_definition():
    """표기만 다른 같은 시각은 같은 값, 멤버가 하나라도 다르면 다른 값이다."""
    from kortravelweather.export_forecasts import membership_digest

    base = [("b", "p", "2026-11-11T00:00:00+00:00"), ("a", "p", "2026-11-11T09:00:00+09:00")]
    same = [("a", "p", "2026-11-11T00:00:00Z"), ("b", "p", datetime(2026, 11, 11, tzinfo=UTC))]
    assert membership_digest(base) == membership_digest(same)
    assert membership_digest(base) != membership_digest(base[:1])
    moved = [("a", "p", "2026-11-11T00:00:00.000001+00:00"), base[0]]
    assert membership_digest(base) != membership_digest(moved)
    assert membership_digest(base + base[:1]) != membership_digest(base)
