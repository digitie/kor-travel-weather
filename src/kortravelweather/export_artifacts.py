"""성공 export 후보의 제한된 불변 artifact. 공개 head 승격은 별도 단계다."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

MAX_CHUNK_POINTS = 5000
MAX_CHUNK_BYTES = 8 * 1024 * 1024
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

    def publish(self, chunk: PreparedExportChunk) -> None:
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
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
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
