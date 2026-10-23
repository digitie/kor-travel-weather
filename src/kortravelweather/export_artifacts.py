"""성공 export 후보의 제한된 불변 artifact. 공개 head 승격은 별도 단계다."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

MAX_CHUNK_POINTS = 5000
MAX_CHUNK_BYTES = 8 * 1024 * 1024
MAX_SHARED_ARTIFACT_READERS = 2
_DIGEST = re.compile(r"[0-9a-f]{64}")


class ExportArtifactError(RuntimeError):
    """불완전한 export 후보는 원천 성공/공개 세대와 구분한다."""


@dataclass(frozen=True)
class PreparedExportChunk:
    payload: bytes
    digest: str
    point_count: int
    source_collected_at: datetime


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExportArtifactError("export 시각에는 timezone이 필요합니다.")
    return value.astimezone(UTC)


def canonical_export_number(value: Decimal) -> str:
    # 원본 fact 열 Numeric(14,4)의 PostgreSQL 반올림과 같은 표현을 쓴다.
    fixed = value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if fixed == 0:
        return "0"
    return format(fixed, "f").rstrip("0").rstrip(".") if "." in format(fixed, "f") else str(fixed)


def prepare_export_chunk(
    points: Iterable[Mapping[str, Any]],
    *,
    source_collected_at: datetime,
) -> PreparedExportChunk:
    """이미 검증된 canonical persisted point만 받는다. raw payload는 받지 않는다."""
    collected = _utc(source_collected_at)
    parts = bytearray()
    count = 0
    for point in points:
        count += 1
        if count > MAX_CHUNK_POINTS:
            raise ExportArtifactError("export chunk point 상한을 넘었습니다.")
        line = (
            json.dumps(
                point, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
            + b"\n"
        )
        if len(parts) + len(line) > MAX_CHUNK_BYTES:
            raise ExportArtifactError("export chunk byte 상한을 넘었습니다.")
        parts.extend(line)
    payload = bytes(parts)
    return PreparedExportChunk(
        payload=payload,
        digest=hashlib.sha256(payload).hexdigest(),
        point_count=count,
        source_collected_at=collected,
    )


class ExportArtifactStore:
    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ExportArtifactError("export artifact 경로는 절대 경로여야 합니다.")
        self.root = root.resolve()

    def _path(self, digest: str) -> Path:
        if _DIGEST.fullmatch(digest) is None:
            raise ExportArtifactError("export artifact digest가 올바르지 않습니다.")
        return self.root / (digest + ".jsonl")

    def _shared_slot(self, deadline: float) -> int:
        """프로세스 수와 무관하게 같은 root의 동시 파일 읽기를 두 개로 제한한다."""
        import fcntl

        while True:
            for ordinal in range(MAX_SHARED_ARTIFACT_READERS):
                descriptor = os.open(
                    self.root / f".export-read-{ordinal}.lock",
                    os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                    0o600,
                )
                try:
                    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                        raise ExportArtifactError("artifact 읽기 slot은 일반 파일이어야 합니다.")
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(descriptor)
                    continue
                except BaseException:
                    os.close(descriptor)
                    raise
                return descriptor
            if time.monotonic() >= deadline:
                raise ExportArtifactError("artifact 동시 읽기 대기 상한을 넘었습니다.")
            time.sleep(0.01)

    @contextmanager
    def guard(self, *, timeout_seconds: float = 5, shared: bool = False):
        # 관리되는 replace/unlink와 승격의 검증→CAS 구간을 같은 유한 guard로 보호한다.
        # 관리자 외부 unlink/디스크 장애까지 원자적으로 묶는다는 뜻은 아니다.
        # reader끼리는 두 개의 OS 공유 slot 안에서만 겹치며 writer 기본값은 배타다.
        import fcntl

        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(
            self.root / ".export.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        acquired, read_slot = False, None
        deadline = time.monotonic() + timeout_seconds
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ExportArtifactError("artifact guard는 일반 파일이어야 합니다.")
            if shared:
                read_slot = self._shared_slot(deadline)
            while not acquired:
                try:
                    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                    fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ExportArtifactError(
                            "artifact guard 대기 상한을 넘었습니다."
                        ) from None
                    time.sleep(0.01)
            yield
        finally:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            if read_slot is not None:
                os.close(read_slot)

    def publish(self, chunk: PreparedExportChunk) -> None:
        with self.guard():
            self._publish(chunk)

    def _publish(self, chunk: PreparedExportChunk) -> None:
        """DB commit 뒤에만 호출한다. 파일이 없으면 receipt만으로 공개할 수 없다."""
        target = self._path(chunk.digest)
        if (
            len(chunk.payload) > MAX_CHUNK_BYTES
            or hashlib.sha256(chunk.payload).hexdigest() != chunk.digest
        ):
            raise ExportArtifactError("export artifact 내용과 digest가 다릅니다.")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(prefix=".candidate-", dir=self.root)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(chunk.payload)
                stream.flush()
                os.fchmod(stream.fileno(), 0o400)
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, digest: str, *, byte_count: int, point_count: int) -> Iterator[dict[str, Any]]:
        if not 0 <= byte_count <= MAX_CHUNK_BYTES or not 0 <= point_count <= MAX_CHUNK_POINTS:
            raise ExportArtifactError("export receipt 상한이 올바르지 않습니다.")
        path = self._path(digest)
        # 검증한 동일 fd의 bounded 내용을 파싱한다. symlink를 따라가지 않는다.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ExportArtifactError("export artifact는 일반 파일이어야 합니다.")
            payload = stream.read(MAX_CHUNK_BYTES + 1)
        if len(payload) != byte_count or hashlib.sha256(payload).hexdigest() != digest:
            raise ExportArtifactError("export artifact가 없거나 손상됐습니다.")
        lines = payload.splitlines()
        if len(lines) != point_count:
            raise ExportArtifactError("export artifact point 수가 receipt와 다릅니다.")
        for line in lines:
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ExportArtifactError("export artifact point 형식이 다릅니다.")
            yield item
