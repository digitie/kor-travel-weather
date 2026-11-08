"""API 소유 검증 완료 페이지 재사용. 승인 시점의 60초 수명과 매 요청 무결성을 지킨다."""

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass

from .export_artifacts import ExportGuardTimeout


class PageReuseExpired(Exception):
    """고정 handle/업무 날짜가 바뀌어 정상 reader에서 다시 구성해야 한다."""


@dataclass(frozen=True)
class _Entry:
    result: tuple
    reader: object
    deadline: float
    size: int


class ExportPageCache:
    """큰 계산·DB·파일 검산은 LRU 잠금 밖에서 수행한다. 만료/실패는 연장하지 않는다.

    페이지와 무결성 proof의 직렬화 크기 기준 64MiB/16항목, 개별 8MiB다. RSS 보장은 아니다.
    동일 키의 동시 첫 요청은 한 작업을 기다리며, 다른 API의 worker/인증 슬롯은 쓰지 않는다.
    """

    def __init__(self, *, clock, max_bytes=64 * 1024 * 1024, max_entry_bytes=8 * 1024 * 1024):
        self.clock = clock
        self.max_bytes, self.max_entry_bytes = max_bytes, max_entry_bytes
        self.entries = OrderedDict()
        self.bytes = 0
        self.inflight = {}
        self.lock = threading.Lock()

    def _evict(self, key, entry):
        with self.lock:
            if self.entries.get(key) is entry:
                self.bytes -= self.entries.pop(key).size

    def _prepare(self, reader, load):
        result = load()
        deadline = reader.selection_deadline
        if deadline is None:
            return result, None
        deadline = min(deadline, reader.selection_checked_after + 60)
        if self.clock() >= deadline:
            raise ExportGuardTimeout("페이지 생성 중 승인 상태의 60초 수명이 끝났습니다.")
        size = len(json.dumps(result, ensure_ascii=False, default=str).encode())
        size += reader.reusable_page_size()
        entry = None
        if size <= self.max_entry_bytes:
            entry = _Entry(result, reader, deadline, size)
        return result, entry

    def _put(self, key, entry):
        with self.lock:
            previous = self.entries.pop(key, None)
            if previous is not None:
                self.bytes -= previous.size
            self.entries[key] = entry
            self.bytes += entry.size
            while len(self.entries) > 16 or self.bytes > self.max_bytes:
                old_key, old = self.entries.popitem(last=False)
                self.bytes -= old.size

    def read(self, key, reader, load):
        now = self.clock()
        with self.lock:
            for old_key, old in list(self.entries.items()):
                if now >= old.deadline:
                    self.bytes -= self.entries.pop(old_key).size
            entry = self.entries.get(key)
            if entry is not None:
                self.entries.move_to_end(key)
            event = self.inflight.get(key)
            owner = entry is None and event is None
            if owner:
                event = threading.Event()
                self.inflight[key] = event
        if entry is not None:
            try:
                # 파일·DB 유실을 60초 캐시로 감추지 않는다. 이 검산은 snapshot 재구성이 아니다.
                entry.reader.validate_reusable_page()
                if self.clock() >= entry.deadline:
                    raise PageReuseExpired()
                reader.selection_deadline = entry.deadline
                reader.page_reuse_status = "reused"
                return entry.result
            except PageReuseExpired:
                self._evict(key, entry)
                return self.read(key, reader, load)
            except BaseException:
                self._evict(key, entry)
                raise
        if not owner:
            if not event.wait(60):
                raise ExportGuardTimeout("동일 export 페이지 생성 대기 상한을 넘었습니다.")
            with self.lock:
                completed = key in self.entries
            if not completed:
                raise ExportGuardTimeout("동일 export 페이지가 완료되지 않았습니다.")
            return self.read(key, reader, load)
        try:
            reader.page_reuse_status = "generated"
            result, entry = self._prepare(reader, load)
            if entry is not None:
                self._put(key, entry)
            return result
        finally:
            with self.lock:
                self.inflight.pop(key).set()
