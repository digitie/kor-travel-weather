"""API 소유 승인 상태 캐시. 소유자가 허용한 최대 60초를 넘겨 재사용하지 않는다."""

import threading
from collections import OrderedDict
from time import monotonic

from .export_artifacts import ExportGuardTimeout

SELECTION_MAX_AGE_SECONDS = 60
_MAX_ENTRIES = 4
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

    def read(self, key, load, *, checked_after, validate=None, min_remaining=0.0):
        """``min_remaining``초보다 수명이 덜 남은 항목은 쓰지 않고 새로 읽는다.

        긴 페이지 생성은 남은 수명이 짧은 선택을 이어받으면 도중에 60초를 넘겨 실패한다.
        그런 요청은 ``min_remaining``을 크게(60이면 항상 새로) 주고, 그 결과를 같은 앱의 다음
        요청(이어지는 cursor 페이지)이 재사용한다.
        """
        now = self.clock()
        with self.lock:
            for old_key, (_, deadline, size) in list(self.entries.items()):
                if now >= deadline or (old_key == key and deadline - now < min_remaining):
                    self.entries.pop(old_key)
                    self.bytes -= size
            found = self.entries.get(key)
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
            # 같은 키를 다른 요청이 읽는 중이면 끝나기를 기다렸다가 그 결과를 쓴다(즉시 503 대신).
            if not waiting.wait(SELECTION_MAX_AGE_SECONDS):
                raise ExportGuardTimeout("승인 상태 갱신 대기 상한을 넘었습니다.")
            return self.read(
                key, load, checked_after=checked_after, validate=validate,
                min_remaining=min_remaining,
            )
        if found is not None:
            try:
                if validate is not None:
                    validate(found[0])  # DB 무결성 조회도 LRU 잠금 밖이다.
                self.require_current(found[1])
                return found[0], found[1]
            except BaseException:
                with self.lock:
                    if self.entries.get(key) is found:
                        self.bytes -= self.entries.pop(key)[2]
                raise
        try:
            result = load()
            deadline = checked_after + SELECTION_MAX_AGE_SECONDS
            self.require_current(deadline)
            size = len(repr((key, result.expired)).encode("utf-8")) + sum(
                len(scope.descriptor.model_dump_json().encode("utf-8"))
                + sum(len(key.encode("utf-8")) for key in scope.location_ids)
                + 256
                for scope in result.scopes
            )
            with self.lock:
                if size <= _MAX_ENTRY_BYTES:
                    self.entries[key] = (result, deadline, size)
                    self.bytes += size
                    while len(self.entries) > _MAX_ENTRIES or self.bytes > _MAX_BYTES:
                        self.bytes -= self.entries.popitem(last=False)[1][2]
            return result, deadline
        finally:
            with self.lock:
                self.inflight.pop(key).set()

    def require_current(self, deadline):
        if self.clock() >= deadline:
            raise ExportGuardTimeout("승인 상태 확인이 60초를 넘어 다시 조회해야 합니다.")
