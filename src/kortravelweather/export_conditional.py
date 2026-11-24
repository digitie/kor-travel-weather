"""service export의 내용 기반 약한 ETag와 본문 읽기 전 304 판정(호출 검토 A2).

예전 ETag는 ``snapshot_id``를 넣어 같은 승인 집합도 snapshot handle TTL(15분)이 지나 새 handle이
발급되면 바뀌었다. 매시·매일 도는 Map 적재는 그래서 거의 304를 받지 못했다. 이제 ETag는
handle이 아니라 **내용**의 digest다.

- 고정 manifest digest(승인 head vector·artifact ref·위치·selection)
- 페이지 식별(resource·selection 서명·limit·첫 페이지 여부와 시작 위치)
- 상태 token(``source_status``·``source_collected_at``, daily는 건강도 digest와 본문)

handle 고유 값(``snapshot_id``·``snapshot_generated_at``·``as_of``·``next_cursor``)과 요청마다
바뀌는 ``generated_at``은 넣지 않는다. 그래서 ETag는 약한(``W/``) 의미 validator다. 304를 받은
쪽은 이미 가진 본문의 항목·상태·수집 시각이 지금과 같다는 뜻으로만 써야 하고, 이어서 cursor를
따라가려면 조건 없이 첫 페이지를 다시 받아야 한다(Map은 304면 순회하지 않는다).

current·locations 첫 페이지는 freeze(또는 handle 재사용)와 상태 판정 직후, artifact 본문을 읽기
전에 ``If-None-Match``를 비교한다. 맞으면 :class:`ExportNotModified`로 본문 읽기·검증·직렬화를
건너뛴다. 이 예외는 오류가 아니라 제어 흐름이다. 페이지 캐시는 이 결과를 저장하지 않는다.

적용 범위: locations·current·daily-summaries. daily 요약 값은 같은 manifest에서도 사전 계산
갱신과 시각 경계(``partial``)로 바뀌므로 본문을 ETag에 넣고, 비교는 값을 읽은 뒤 직렬화 전에 한다.
forecasts는 바꾸지 않았다(``snapshot_id``+본문). 본문이 응답 시각 이후 target만 담아 manifest만으로
정해지지 않고, Map은 예보에 ``If-None-Match``를 보내지 않는다(Map ADR-122).
"""

from __future__ import annotations

from typing import Any

from .export_heads import _digest

#: ETag 의미가 바뀌면 올린다. 예전 형식 ETag가 새 ETag와 우연히 맞지 않게 한다.
CONTENT_ETAG_VERSION = "content-v1"


class ExportNotModified(Exception):
    """요청의 ``If-None-Match``가 지금 내용 ETag와 맞아 본문 읽기를 건너뛴다(오류가 아니다).

    route는 이 예외를 본문 없는 304로 바꾼다. 응답 header의 ``etag``·``x-source-status``는 200과
    같은 값이다.
    """

    def __init__(self, etag: str, source_status: str):
        super().__init__("not modified")
        self.etag = etag
        self.source_status = source_status


def content_etag(*parts: Any) -> str:
    """내용·페이지 식별·상태 token으로 만든 약한 ETag. handle 고유 값은 넣지 않는다."""
    return 'W/"' + _digest([CONTENT_ETAG_VERSION, *parts]) + '"'


def matches_etag(header: str, etag: str) -> bool:
    """``If-None-Match``(쉼표 목록·``*``)가 ``etag``와 약한 비교로 맞으면 참이다."""
    if not header:
        return False
    return any(
        value.strip().removeprefix("W/") in {etag.removeprefix("W/"), "*"}
        for value in header.split(",")
    )
