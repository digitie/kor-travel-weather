"""성공 export 후보의 제한된 불변 artifact. 공개 head 승격은 별도 단계다."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_CHUNK_POINTS = 5000
MAX_CHUNK_BYTES = 8 * 1024 * 1024
MAX_SHARED_ARTIFACT_READERS = 2
#: 공유(reader) guard 대기 기본·범위(초). 2026-11-05 운영: 승격 배타 구간을 줄인 뒤에도 eMMC fsync가
#: 섞인 짧은 배타 보유가 2초를 넘겨 reader 503이 시간당 약 6건 남았다. writer 상한(5초)과 맞춘다.
READER_GUARD_SECONDS = 5.0
READER_GUARD_SECONDS_RANGE = (0.5, 10.0)
#: 배타 guard 보유가 이 시간을 넘으면 보유자를 경고 log로 남긴다.
SLOW_EXCLUSIVE_HOLD_SECONDS = 2.0
_HOLDERS = frozenset({"publish", "gc", "promotion_cas", "other"})
_DIGEST = re.compile(r"[0-9a-f]{64}")
# hash로 검증해 복호한 내용을 파일 identity(inode·크기·mtime·ctime)와 함께 기억한다. 같은
# identity면 다시 읽거나 hash하지 않는다(``_cached_points``). 다르면 다시 읽고 hash한다.
_decoded_artifacts = OrderedDict()
_decoded_artifact_bytes = 0
_decoded_artifact_lock = threading.Lock()
# 파일 byte 기준 상한이다(복호한 dict는 대략 3~5배 메모리). odroid 운영 current snapshot
# 하나가 3,302개 파일·24.7MB여서 예전 8MB/2048개로는 Map 한 번의 순회도 담지 못했다.
# API는 uvicorn 1 프로세스(2026-11-10 RSS 368MB, 호스트 가용 2.9GB)라 32MB(복호 약 100~160MB)로
# 둔다. 같은 모듈을 import하는 code-server(승격 검산)도 같은 상한을 갖는다.
_DECODED_ARTIFACT_BYTES = 32 * 1024 * 1024
_DECODED_ARTIFACT_ENTRIES = 8192


def _file_identity(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def observe_export_artifact_cache(outcome: str, count: int = 1) -> None:
    """복호 캐시 hit/miss/evict를 센다(예외 없음)."""
    try:
        from .metrics import observe_export_artifact_cache as observe

        observe(outcome, count)
    except Exception:  # noqa: BLE001 - 계측 실패가 읽기를 바꾸지 않는다
        pass


def _observe_exclusive_hold(holder: str, seconds: float) -> None:
    """배타 writer가 reader를 막은 시간(turnstile 취득→해제)을 보유자별로 센다(예외 없음)."""
    try:
        label = holder if holder in _HOLDERS else "other"
        from .metrics import observe_export_guard_hold

        observe_export_guard_hold(label, seconds)
        if seconds > SLOW_EXCLUSIVE_HOLD_SECONDS:
            logger.warning("weather_export_guard_hold holder=%s seconds=%.2f", label, seconds)
    except Exception:  # noqa: BLE001 - 계측 실패가 guard 해제·원래 예외를 바꾸지 않는다
        pass


class ExportArtifactError(RuntimeError):
    """불완전한 export 후보는 원천 성공/공개 세대와 구분한다."""


class ExportGuardTimeout(ExportArtifactError):
    """artifact guard를 상한 안에 얻지 못했다. export 부재가 아니라 일시 경합이다."""


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
    def __init__(self, root: Path, *, reader_guard_seconds: float = READER_GUARD_SECONDS) -> None:
        if not root.is_absolute():
            raise ExportArtifactError("export artifact 경로는 절대 경로여야 합니다.")
        low, high = READER_GUARD_SECONDS_RANGE
        if not low <= reader_guard_seconds <= high:
            raise ExportArtifactError("reader guard 대기 상한이 범위를 벗어났습니다.")
        self.reader_guard_seconds = reader_guard_seconds
        self.root = root.resolve()
        # 배타 guard 한 번 안에서 이미 hash를 확인한 digest. 관리되는 writer/GC는 같은
        # 배타 guard를 기다리므로 그 안에서는 내용이 바뀌지 않는다. guard를 나가면 버린다.
        self._scope = threading.local()

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
                raise ExportGuardTimeout("artifact 동시 읽기 대기 상한을 넘었습니다.")
            time.sleep(0.01)

    def _turnstile(self, deadline: float) -> int:
        """writer가 기존 reader를 기다릴 때 새 reader의 입장을 막는다."""
        import fcntl

        descriptor = os.open(
            self.root / ".export-admission.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ExportArtifactError("artifact 입장 guard는 일반 파일이어야 합니다.")
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return descriptor
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ExportGuardTimeout("artifact 입장 대기 상한을 넘었습니다.") from None
                    time.sleep(0.01)
        except BaseException:
            os.close(descriptor)
            raise

    @contextmanager
    def guard(
        self, *, timeout_seconds: float | None = None, shared: bool = False, holder: str = "other",
    ):
        # 관리되는 replace/unlink와 승격의 최종 CAS(파일 존재 재확인 포함)를 같은 유한 guard로
        # 보호한다.
        # 관리자 외부 unlink/디스크 장애까지 원자적으로 묶는다는 뜻은 아니다.
        # reader끼리는 두 개의 OS 공유 slot 안에서만 겹치며 writer 기본값은 배타다.
        import fcntl

        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(
            self.root / ".export.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        acquired, read_slot, admission = False, None, None
        if timeout_seconds is None:
            timeout_seconds = self.reader_guard_seconds if shared else 5
        deadline = time.monotonic() + timeout_seconds
        held_since = None
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ExportArtifactError("artifact guard는 일반 파일이어야 합니다.")
            if shared:
                read_slot = self._shared_slot(deadline)
            admission = self._turnstile(deadline)
            if not shared:
                # 새 reader는 writer가 turnstile을 잡은 순간부터 막힌다(기존 reader 대기 포함).
                held_since = time.monotonic()
            while not acquired:
                try:
                    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                    fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ExportGuardTimeout(
                            "artifact guard 대기 상한을 넘었습니다."
                        ) from None
                    time.sleep(0.01)
            if shared:
                # 새 reader는 짧게 통과한다. writer는 admission을 끝까지 보유해
                # 기존 reader가 끝나기 전에 후속 reader가 연속 진입하지 못하게 한다.
                os.close(admission)
                admission = None
                yield
            else:
                owned = getattr(self._scope, "verified", None) is None
                if owned:
                    self._scope.verified = set()
                try:
                    yield
                finally:
                    if owned:
                        self._scope.verified = None
        finally:
            hold = None
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            if not shared and held_since is not None:
                hold = time.monotonic() - held_since
            os.close(descriptor)
            if admission is not None:
                os.close(admission)
            if read_slot is not None:
                os.close(read_slot)
            if hold is not None:
                # 모든 fd를 닫은 뒤에 센다. 계측이 진행 중 예외를 바꾸거나 turnstile을 붙들지
                # 않는다.
                _observe_exclusive_hold(holder, hold)

    @contextmanager
    def verifying(self):
        """guard 없이 같은 thread의 verify→read가 hash를 한 번만 계산하게 한다.

        파일 이름이 내용 hash이고 managed writer(``publish``)는 같은 hash의 내용으로만 바꾸므로
        한 번 맞은 digest의 bytes는 그 뒤에도 같다. 사라지면(GC·외부 unlink) 다음 open이 실패한다.
        이미 guard·verifying 안이면 바깥 scope를 그대로 쓴다.
        """
        owned = getattr(self._scope, "verified", None) is None
        if owned:
            self._scope.verified = set()
        try:
            yield
        finally:
            if owned:
                self._scope.verified = None

    def read_shared(
        self, digest: str, *, byte_count: int, point_count: int,
    ) -> list[dict[str, Any]]:
        """공유 guard를 파일 하나 읽는 동안만 잡는다(최대 8MB를 메모리로 읽고 바로 놓는다).

        reader가 snapshot freeze·DB 단계 내내 공유 guard를 쥐면 writer(collector publish·승격
        CAS)가 5초 상한에서 실패했다(2026-11-07 운영: 6시간마다 450~700건). 읽는 파일은 이미
        commit된 snapshot ref·head·보존 창이 GC에서 지킨다(export_gc); guard는 관리되는
        replace/unlink와 한 파일 읽기를 겹치지 않게 할 뿐이다.
        """
        # 검증해 복호한 같은 파일이면 파일을 읽지 않으므로 guard도 필요 없다(교체·삭제는
        # _cached_points의 lstat이 본다).
        if 0 <= byte_count <= MAX_CHUNK_BYTES and 0 <= point_count <= MAX_CHUNK_POINTS:
            remembered = self._cached_points(
                (digest, byte_count, point_count), self._path(digest),
            )
            if remembered is not None:
                items, flat, _identity = remembered
                return [dict(item) if flat else deepcopy(item) for item in items]
        with self.guard(shared=True):
            return list(self.read(digest, byte_count=byte_count, point_count=point_count))

    @contextmanager
    def exclusive(self, *, holder: str, attempts: int = 3, timeout_seconds: float = 5):
        """배타 guard. 대기 상한에 걸리면 짧게 쉬고 몇 번 더 시도한다(writer 일시 경합)."""
        import random

        for attempt in range(1, attempts + 1):
            stack = ExitStack()
            try:
                stack.enter_context(self.guard(timeout_seconds=timeout_seconds, holder=holder))
            except ExportGuardTimeout:
                stack.close()
                if attempt >= attempts:
                    raise
                time.sleep(random.uniform(0.2, 1.0))
                continue
            with stack:
                yield
            return

    def all_present(self, refs) -> bool:
        """공유 guard 안에서 ref 파일이 모두 남아 있는지 본다(내용은 읽지 않는다).

        GC는 참조 재확인과 unlink를 한 배타 구간에서 하므로, 이 확인이 통과하면 그 뒤 GC는
        이미 commit된 snapshot ref를 보고 그 파일을 지키거나, 이미 지운 것이 여기서 보인다.
        """
        with self.guard(shared=True):
            for ref in refs:
                try:
                    self.present(ref["artifact_digest"], byte_count=ref["byte_count"])
                except ExportArtifactError:
                    return False
        return True

    def present(self, digest: str, *, byte_count: int) -> None:
        """내용을 읽지 않고 파일이 같은 크기의 일반 파일로 남아 있는지만 본다(최종 CAS 직전)."""
        try:
            info = os.stat(self._path(digest), follow_symlinks=False)
        except OSError as exc:
            raise ExportArtifactError("export artifact가 없거나 손상됐습니다.") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_size != byte_count:
            raise ExportArtifactError("export artifact가 없거나 손상됐습니다.")

    def publish(self, chunk: PreparedExportChunk) -> None:
        with self.exclusive(holder="publish"):
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

    def _lines(self, digest: str, *, byte_count: int, point_count: int) -> list[bytes]:
        return self._read_lines(digest, byte_count=byte_count, point_count=point_count)[0]

    def _read_lines(
        self, digest: str, *, byte_count: int, point_count: int,
    ) -> tuple[list[bytes], tuple[int, ...]]:
        if not 0 <= byte_count <= MAX_CHUNK_BYTES or not 0 <= point_count <= MAX_CHUNK_POINTS:
            raise ExportArtifactError("export receipt 상한이 올바르지 않습니다.")
        path = self._path(digest)
        # 검증한 동일 fd의 bounded 내용을 파싱한다. symlink를 따라가지 않는다.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ExportArtifactError("export artifact는 일반 파일이어야 합니다.")
            payload = stream.read(MAX_CHUNK_BYTES + 1)
            after = os.fstat(stream.fileno())
        if _file_identity(before) != _file_identity(after):
            raise ExportArtifactError("검증 중 export artifact가 바뀌었습니다.")
        verified_key = (digest, *_file_identity(after))
        verified = getattr(self._scope, "verified", None)
        if len(payload) != byte_count or (
            (verified is None or verified_key not in verified)
            and hashlib.sha256(payload).hexdigest() != digest
        ):
            raise ExportArtifactError("export artifact가 없거나 손상됐습니다.")
        if verified is not None:
            verified.add(verified_key)
        lines = payload.splitlines()
        if len(lines) != point_count:
            raise ExportArtifactError("export artifact point 수가 receipt와 다릅니다.")
        return lines, _file_identity(after)

    def _cached_points(self, key, path) -> tuple[Any, ...] | None:
        """같은 파일(inode·크기·mtime·ctime 동일)이면 이미 hash로 검증해 복호한 내용을 쓴다.

        파일 이름이 내용 hash다. 내용을 바꾸는 모든 쓰기(같은 길이 변조 포함)는 ctime을 바꾸고
        (utime으로 mtime을 되돌려도 ctime은 되돌릴 수 없다), 교체는 inode를 바꾼다. 그러면
        hit가 아니라 다시 읽고 hash한다. 삭제·symlink·크기 변화는 lstat에서 바로 실패한다.
        """
        with _decoded_artifact_lock:
            remembered = _decoded_artifacts.get(key)
        if remembered is None:
            return None
        info = os.stat(path, follow_symlinks=False)  # 삭제는 여기서 FileNotFoundError다
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size != key[1]
            or remembered[2] != _file_identity(info)
        ):
            # symlink·크기·내용 변화는 전체 검증 경로(O_NOFOLLOW·hash)가 실패로 판정한다.
            return None
        with _decoded_artifact_lock:
            if key in _decoded_artifacts:
                _decoded_artifacts.move_to_end(key)
        observe_export_artifact_cache("hit")
        return remembered

    def verify(self, digest: str, *, byte_count: int, point_count: int) -> None:
        """내용 hash·크기·줄 수만 확인한다. JSON은 해석하지 않는다.

        파일 이름이 곧 내용 hash이므로 hash가 맞으면 collector가 쓴 canonical JSON과 같다.
        point 해석은 승격 단계의 ``read``가 한 번만 한다. 배타 guard 안에서 확인한 digest는
        같은 guard 안의 다음 ``read``가 hash를 다시 계산하지 않는다.
        """
        self._lines(digest, byte_count=byte_count, point_count=point_count)

    def read(self, digest: str, *, byte_count: int, point_count: int) -> Iterator[dict[str, Any]]:
        if not 0 <= byte_count <= MAX_CHUNK_BYTES or not 0 <= point_count <= MAX_CHUNK_POINTS:
            raise ExportArtifactError("export receipt 상한이 올바르지 않습니다.")
        key = (digest, byte_count, point_count)
        remembered = self._cached_points(key, self._path(digest))
        if remembered is None:
            items, flat = self._decode_and_remember(key)
        else:
            items, flat, _identity = remembered
        # 호출자가 중첩 dict/list를 수정해도 다음 응답이나 다른 reader에는 전파하지 않는다.
        for item in items:
            yield dict(item) if flat else deepcopy(item)

    def _decode_and_remember(self, key) -> tuple[tuple[dict[str, Any], ...], bool]:
        global _decoded_artifact_bytes
        digest, byte_count, point_count = key
        lines, identity = self._read_lines(
            digest, byte_count=byte_count, point_count=point_count,
        )
        observe_export_artifact_cache("miss")
        items = tuple(json.loads(line) for line in lines)
        if any(not isinstance(item, dict) for item in items):
            raise ExportArtifactError("export artifact point 형식이 다릅니다.")
        # canonical point는 scalar뿐이다. 중첩 값이 있는 legacy/검증 자료만 깊게 복사한다.
        flat = all(type(value) in (str, int, float, bool, type(None))
                   for item in items for value in item.values())
        evicted = 0
        with _decoded_artifact_lock:
            previous = _decoded_artifacts.pop(key, None)
            if previous is not None:
                _decoded_artifact_bytes -= byte_count
            _decoded_artifacts[key] = (items, flat, identity)
            _decoded_artifact_bytes += byte_count
            while _decoded_artifacts and (
                len(_decoded_artifacts) > _DECODED_ARTIFACT_ENTRIES
                or _decoded_artifact_bytes > _DECODED_ARTIFACT_BYTES
            ):
                old, _ = _decoded_artifacts.popitem(last=False)
                _decoded_artifact_bytes -= old[1]
                evicted += 1
        if evicted:
            observe_export_artifact_cache("evict", evicted)
        return items, flat
