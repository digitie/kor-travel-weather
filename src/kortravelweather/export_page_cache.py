"""검증 완료 페이지 재사용. 첫 예보 본문 보관과 매 hit의 fresh 승인 proof를 구분한다."""

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass

from .export_artifacts import ExportGuardTimeout


class PageReuseExpired(ExportGuardTimeout):
    """hit는 다시 구성하고, 최초 생성 중 경계 변화도 500 대신 503으로 닫는다."""


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
        seed_check = getattr(reader, "_check_current_seed_deadline", None)
        if seed_check is not None:
            seed_check()
        deadline = reader.selection_deadline
        if deadline is None:
            return result, None
        # 본문 생성 시간이 승인 proof 수명을 다 쓴 첫 예보는 payload 대신 새 metadata로 검산한다.
        fresh_first = getattr(reader, "revalidate_first_page", False)
        if fresh_first and self.clock() >= min(deadline, reader.selection_checked_after + 60):
            proof = reader.validate_cached_first_page(reader)
            reader.selection_deadline = proof.selection_deadline
            reader.selection_checked_after = proof.selection_checked_after
            reader.reuse_wall_started = proof.reuse_wall_started
        else:
            reader.validate_reusable_page()
        size = len(json.dumps(result, ensure_ascii=False, default=str).encode())
        size += reader.reusable_page_size()
        # 첫 예보는 hit마다 새 source/health proof를 얻으므로 본문만 완성 뒤 60초 보관한다.
        # current/daily/cursor의 승인 수명은 기존 기준 그대로다. hit로 보관 기한은 늘리지 않는다.
        deadline = (self.clock() + 60 if fresh_first else
                    min(deadline, reader.selection_checked_after + 60))
        deadline = min(deadline, getattr(reader, "page_valid_until", float("inf")))
        if seed_check is not None:
            seed_check()
        if self.clock() >= deadline:
            raise ExportGuardTimeout("페이지 생성 중 재사용 또는 business 경계가 지났습니다.")
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
        """완성 페이지를 재사용하거나 이 요청이 만든다.

        예보 첫 페이지(``revalidate_first_page``)도 조건부 여부와 관계없이 같은 길을 쓴다.
        hit마다 새 freeze로 최신 승인·head vector를 확인하고, 같은 snapshot handle·manifest
        digest·건강도이며 삭제 epoch가 같을 때만 본문을 재사용한다(호출 검토 A5).
        """
        now = self.clock()
        seed = None
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
                # limit만 다른 CURRENT 첫 페이지의 완료 proof만 빌린다.
                # 본문/key는 재사용하지 않는다.
                if (isinstance(key, tuple) and len(key) == 12
                        and key[1] == "current" and key[3] is None):
                    for old_key, old in reversed(self.entries.items()):
                        if (isinstance(old_key, tuple) and len(old_key) == len(key)
                                and old_key[:2] == key[:2] and old_key[3:] == key[3:]
                                and old_key[2] != key[2]
                                and getattr(old.reader, "_current_seed_eligible", False)):
                            seed = (old_key, old)
                            break
        if entry is not None:
            try:
                # 파일·DB 유실을 60초 캐시로 감추지 않는다. 이 검산은 snapshot 재구성이 아니다.
                if getattr(reader, "revalidate_first_page", False):
                    proof = reader.validate_cached_first_page(entry.reader)
                    deadline = min(entry.deadline, proof.selection_deadline)
                else:
                    entry.reader.validate_reusable_page()
                    deadline = entry.deadline
                if self.clock() >= deadline:
                    raise PageReuseExpired()
                reader.selection_deadline = deadline
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
            # 소유자가 실패(410/422/503)했거나 캐시하지 않은 결과면 이 요청이 직접 읽는다.
            # 소유자의 오류를 일시 경합 503으로 바꿔 숨기지 않는다.
            return self.read(key, reader, load)
        try:
            reader.page_reuse_status = "generated"
            if seed is not None:
                reader._current_page_seed = (seed[1].reader, seed[1].deadline)
            try:
                result, entry = self._prepare(reader, load)
            except PageReuseExpired:
                restart = getattr(reader, "_restart_expired_current_seed", None)
                if restart is None or not restart():
                    raise
                # 원래 seed lease가 끝난 경우만 한 번 정상 cold freeze를 한다.
                # 두 번째 생성의 실패나 hash/삭제/SQL 오류는 재시도하지 않는다.
                result, entry = self._prepare(reader, load)
            if entry is not None:
                self._put(key, entry)
            return result
        except BaseException:
            if seed is not None:
                self._evict(*seed)
            raise
        finally:
            with self.lock:
                self.inflight.pop(key).set()
