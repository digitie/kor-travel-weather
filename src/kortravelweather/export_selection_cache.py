"""API 소유 승인 상태 캐시. 소유자가 허용한 최대 60초를 넘겨 재사용하지 않는다."""

import threading
from collections import OrderedDict
from time import monotonic

from .export_artifacts import ExportGuardTimeout

SELECTION_MAX_AGE_SECONDS = 60
# 예보(초단기·단기·외부 × provider 필터)도 같은 캐시를 쓴다(2026-11-10). 현재·일별 항목이
# 예보 항목에 밀려나지 않도록 항목 수를 늘린다. 메모리 상한은 바이트(_MAX_BYTES)가 정한다.
_MAX_ENTRIES = 8
_MAX_BYTES = 16 * 1024 * 1024
_MAX_ENTRY_BYTES = 8 * 1024 * 1024
_CATALOG_FIELDS = (
    "location_id",
    "latitude",
    "longitude",
    "nx",
    "ny",
    "mid_land_region",
    "mid_temperature_region",
    "point_provider",
    "point_station_id",
    "point_station_name",
    "point_sido_name",
    "point_network",
)


class ExportSelectionCache:
    """잠금 안에서는 작은 LRU만 만진다. 같은 키의 중복 DB 작업은 하나만 하고 나머지는 기다린다.

    용량은 descriptor·위치 ID·키의 직렬화 크기로 제한하며 Python RSS 상한은 아니다.
    실패는 기억하지 않는다. 캐시 만료/갱신 실패 때 이전 정상 상태로 연장하지 않는다.
    """

    def __init__(self, *, clock=monotonic):
        self.clock = clock
        self.entries = OrderedDict()
        self.bytes = 0
        self.inflight = {}
        self.lock = threading.Lock()

    @staticmethod
    def key(connection, locations, expected, optional, health_location_ids):
        return (
            id(connection.engine),
            tuple(sorted(expected)),
            tuple(sorted(optional)),
            None if health_location_ids is None else tuple(sorted(health_location_ids)),
            tuple(
                sorted(
                    tuple(
                        (type(value).__name__, value if type(value) in (str, int, type(None))
                         else str(value))
                        for field in _CATALOG_FIELDS for value in (row.get(field),)
                    )
                    for row in locations
                )
            ),
        )

    def read(self, key, load, *, checked_after, validate=None, min_deadline=None, token=None):
        """승인 선택을 읽는다. 조건에 맞는 공용 항목만 재사용하고, 아니면 이 요청이 새로 읽는다.

        - ``min_deadline``: 이 시각보다 일찍 끝나는 항목은 이 요청에 쓰지 않는다(지우지는 않는다).
          긴 첫 페이지는 ``자기 시작 시각+60``을 줘서 자기보다 먼저 읽힌 선택을 잇지 않고,
          cursor 페이지는 ``지금+30``처럼 충분히 남은 것만 받는다.
        - ``token``: 읽은 transaction의 삭제 변경 번호(page liveness) 같은 값이다. 다르면
          다른 시점의 선택이므로 재사용하지 않는다.
        - 재사용 검산(``validate``)이 실패하면 다른 시점 snapshot의 차이일 수 있으므로 그 항목을
          버리고 이 요청의 transaction에서 새로 읽는다. 60초 상한(``ExportGuardTimeout``)은 그대로
          올린다.
        같은 키를 다른 요청이 읽는 중이면 이 요청의 60초 안에서만 기다린다. 기다리는 동안 API
        worker 슬롯과 열린 transaction을 쥐고 있다(페이지 캐시의 동일 키 대기와 같다).
        """
        own_deadline = checked_after + SELECTION_MAX_AGE_SECONDS

        def usable(entry, now):
            return (
                now < entry[1]
                and (min_deadline is None or entry[1] >= min_deadline)
                and (token is None or entry[3] == token)
            )

        now = self.clock()
        with self.lock:
            for old_key, entry in list(self.entries.items()):
                if now >= entry[1]:
                    self.entries.pop(old_key)
                    self.bytes -= entry[2]
            found = self.entries.get(key)
            if found is not None and not usable(found, now):
                found = None  # 다른 요청에는 여전히 유효할 수 있어 지우지 않는다.
            waiting = None
            if found is not None:
                self.entries.move_to_end(key)
            elif key in self.inflight:
                waiting = self.inflight[key]
            else:
                if len(self.inflight) >= _MAX_ENTRIES:
                    raise ExportGuardTimeout("승인 상태 갱신이 이미 진행 중입니다.")
                self.inflight[key] = threading.Event()
        if waiting is not None:
            if not waiting.wait(max(0.0, own_deadline - self.clock())):
                raise ExportGuardTimeout("승인 상태 갱신 대기 상한을 넘었습니다.")
            return self.read(
                key, load, checked_after=checked_after, validate=validate,
                min_deadline=min_deadline, token=token,
            )
        if found is not None:
            try:
                if validate is not None:
                    validate(found[0])  # DB 무결성 조회도 LRU 잠금 밖이다.
                self.require_current(found[1])
                return found[0], found[1]
            except ExportGuardTimeout:
                self._drop(key, found)
                raise
            except Exception:
                # 다른 요청의 더 새 snapshot에서 읽은 선택을 이 요청의 오래된 RR로
                # 검산하면 새 세대가 보이지 않아 유실처럼 보인다. 버리고 이 요청에서
                # 새로 읽어, 실제 유실이면 거기서 실패한다.
                self._drop(key, found)
                return self.read(
                    key, load, checked_after=checked_after, validate=None,
                    min_deadline=min_deadline, token=token,
                )
        try:
            result = load()
            deadline = own_deadline
            self.require_current(deadline)
            size = len(repr((key, result.expired)).encode("utf-8")) + sum(
                len(scope.descriptor.model_dump_json().encode("utf-8"))
                + sum(len(key.encode("utf-8")) for key in scope.location_ids)
                + 256
                for scope in result.scopes
            )
            with self.lock:
                previous = self.entries.pop(key, None)
                if previous is not None:
                    self.bytes -= previous[2]
                if size <= _MAX_ENTRY_BYTES:
                    self.entries[key] = (result, deadline, size, token)
                    self.bytes += size
                    while len(self.entries) > _MAX_ENTRIES or self.bytes > _MAX_BYTES:
                        self.bytes -= self.entries.popitem(last=False)[1][2]
            return result, deadline
        finally:
            with self.lock:
                self.inflight.pop(key).set()

    def _drop(self, key, entry):
        with self.lock:
            if self.entries.get(key) is entry:
                self.bytes -= self.entries.pop(key)[2]

    def require_current(self, deadline):
        if self.clock() >= deadline:
            raise ExportGuardTimeout("승인 상태 확인이 60초를 넘어 다시 조회해야 합니다.")
