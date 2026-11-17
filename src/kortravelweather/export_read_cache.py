"""고정 60초 수명·bytes 상한의 immutable snapshot manifest 캐시."""

import json
import threading
from collections import OrderedDict
from time import monotonic


class SnapshotMetadataCache:
    def __init__(self, *, clock=monotonic, max_bytes=16 * 1024 * 1024):
        self.clock = clock
        self.max_bytes = max_bytes
        self.entries = OrderedDict()
        self.bytes = 0
        self.lock = threading.Lock()

    def read(self, key, load):
        started = self.clock()
        with self.lock:
            for old_key, (_value, deadline, size) in list(self.entries.items()):
                if started >= deadline:
                    self.entries.pop(old_key)
                    self.bytes -= size
            entry = self.entries.get(key)
            if entry is not None:
                self.entries.move_to_end(key)
                return dict(entry[0])
        value = load()
        if value is None:
            return None
        size = len(json.dumps(value, ensure_ascii=False, default=str).encode())
        # DB load 지연은 캐시 수명에 포함한다. hit마다 deadline을 미루지 않는다.
        if size <= self.max_bytes and self.clock() < started + 60:
            with self.lock:
                old = self.entries.pop(key, None)
                if old is not None:
                    self.bytes -= old[2]
                self.entries[key] = (dict(value), started + 60, size)
                self.bytes += size
                while len(self.entries) > 16 or self.bytes > self.max_bytes:
                    self.bytes -= self.entries.popitem(last=False)[1][2]
        return dict(value)
