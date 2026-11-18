"""승인된 export snapshot을 파일 없이 DB에 보관한다.

원본 fact는 독립 보존 정책으로 먼저 삭제될 수 있다. 이 불변 읽기 모델은 기존
head·snapshot·일별 checkpoint의 수명 동안 canonical bytes를 유지한다.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from weakref import WeakKeyDictionary

from pydantic_core import from_json
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    Table,
    delete,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .export_artifacts import (
    MAX_CHUNK_BYTES,
    MAX_CHUNK_POINTS,
    ExportArtifactError,
    ExportGuardTimeout,
    PreparedExportChunk,
)

_DIGEST = re.compile(r"[0-9a-f]{64}")
_LOCK_NAMESPACE = 1465017671
_LOCK_KEY = 42
MAX_ARTIFACT_READ_BATCH = 8
MAX_ARTIFACT_READ_BYTES = MAX_CHUNK_BYTES
_context = threading.local()
_sqlite_lock = threading.RLock()
_cache_registry = WeakKeyDictionary()
_cache_registry_lock = threading.Lock()


def _decode_json_line(line):
    # 행 경계는 그대로 검증한다. UTF-16/BOM·surrogate 등 기존 허용 입력은 stdlib로 보존한다.
    try:
        return from_json(line, cache_strings="keys")
    except ValueError:
        return json.loads(line)


class _DecodedCache:
    """무게는 전체 canonical 또는 보관 projection의 JSON 길이다. heap/RSS 상한은 아니다."""

    def __init__(self):
        self.entries = OrderedDict()
        self.bytes = 0
        self.lock = threading.Lock()



def export_artifact_table(metadata):
    if "weather_export_artifacts" in metadata.tables:
        return metadata.tables["weather_export_artifacts"]
    table = Table(
        "weather_export_artifacts", metadata,
        Column("digest", String(64), primary_key=True),
        Column("payload", LargeBinary, nullable=False),
        Column("byte_count", Integer, nullable=False),
        Column("point_count", Integer, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        CheckConstraint("byte_count BETWEEN 0 AND 8388608", name="ck_weather_artifact_bytes"),
        CheckConstraint("point_count BETWEEN 0 AND 5000", name="ck_weather_artifact_points"),
        CheckConstraint(
            "octet_length(payload) = byte_count", name="ck_weather_artifact_payload_bytes",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "encode(sha256(payload), 'hex') = digest", name="ck_weather_artifact_payload_digest",
        ).ddl_if(dialect="postgresql"),
    )
    Index("ix_weather_export_artifacts_created_digest", table.c.created_at, table.c.digest)
    return table


def install_artifact_immutability(connection):
    """create_schema 경로도0042 migration과 같은 UPDATE 금지를 설치한다."""
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION weather_export_artifacts_reject_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'weather export artifacts are immutable' USING ERRCODE='23514';
        END;
        $$
    """)
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS trg_weather_export_artifacts_immutable ON weather_export_artifacts"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER trg_weather_export_artifacts_immutable "
        "BEFORE UPDATE ON weather_export_artifacts "
        "FOR EACH ROW EXECUTE FUNCTION weather_export_artifacts_reject_update()"
    )


def export_artifact_store(repository, artifact_root: Path):
    """서비스의 정본 저장소. artifact_root는 이전 파일을 명시적으로 이관할 때만 쓴다."""
    return DatabaseExportArtifactStore(repository)


class DatabaseExportArtifactStore:
    """해시·크기·점 개수 계약은 유지하고 파일 I/O를 제거한다."""

    is_database = True

    def __init__(self, repository):
        self.repository = repository
        self.engine = repository.engine
        from .repository import Base

        self.table = export_artifact_table(Base.metadata)
        with _cache_registry_lock:
            if self.engine not in _cache_registry:
                _cache_registry[self.engine] = _DecodedCache()
            self._decoded = _cache_registry[self.engine]

    def _validate(self, digest, byte_count=None, point_count=None):
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise ExportArtifactError("export artifact digest가 올바르지 않습니다.")
        if byte_count is not None and not 0 <= byte_count <= MAX_CHUNK_BYTES:
            raise ExportArtifactError("export receipt byte 상한이 올바르지 않습니다.")
        if point_count is not None and not 0 <= point_count <= MAX_CHUNK_POINTS:
            raise ExportArtifactError("export receipt point 상한이 올바르지 않습니다.")

    @contextmanager
    def _connection(self, *, write=False):
        held = getattr(_context, "guards", {}).get(self.engine)
        if held is not None:
            if write and held[1]:
                raise ExportArtifactError("공유 artifact guard에서 쓸 수 없습니다.")
            yield held[0]
        else:
            with self.engine.begin() as connection:
                yield connection

    @contextmanager
    def guard(self, *, timeout_seconds=None, shared=False, holder="other"):
        timeout = 5.0 if timeout_seconds is None else timeout_seconds
        if timeout < 0:
            raise ValueError("artifact guard 대기는 음수일 수 없습니다.")
        guards = getattr(_context, "guards", None)
        if guards is None:
            _context.guards = guards = {}
        held = guards.get(self.engine)
        if held is not None:
            if held[1] and not shared:
                raise ExportArtifactError("공유 artifact guard를 승격할 수 없습니다.")
            yield
            return
        deadline = time.monotonic() + timeout
        if self.engine.dialect.name == "sqlite":
            if not _sqlite_lock.acquire(timeout=timeout):
                raise ExportGuardTimeout("DB artifact guard 대기 상한을 넘었습니다.")
            try:
                with self.engine.begin() as connection:
                    guards[self.engine] = (connection, shared)
                    try:
                        yield
                    finally:
                        guards.pop(self.engine, None)
            finally:
                _sqlite_lock.release()
            return
        with self.engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            connection.execute(text("SET LOCAL lock_timeout = '1s'"))
            kind = "pg_try_advisory_xact_lock_shared" if shared else "pg_try_advisory_xact_lock"
            statement = text(f"SELECT {kind}(:namespace, :key)")
            while not connection.scalar(
                statement, {"namespace": _LOCK_NAMESPACE, "key": _LOCK_KEY},
            ):
                if time.monotonic() >= deadline:
                    raise ExportGuardTimeout("DB artifact guard 대기 상한을 넘었습니다.")
                time.sleep(min(.01, max(0, deadline - time.monotonic())))
            guards[self.engine] = (connection, shared)
            try:
                yield
            finally:
                guards.pop(self.engine, None)

    @contextmanager
    def exclusive(self, *, holder, attempts=3, timeout_seconds=5):
        # DB 대기는 한 번의 유한 advisory lock으로 묶는다.
        with self.guard(timeout_seconds=timeout_seconds, holder=holder):
            yield

    @contextmanager
    def verifying(self):
        # DB 행은 UPDATE할 수 없고 각 읽기에서 실제 존재·내용을 확인한다.
        yield

    def _insert(self, connection, chunk):
        self._validate(chunk.digest, len(chunk.payload), chunk.point_count)
        if hashlib.sha256(chunk.payload).hexdigest() != chunk.digest:
            raise ExportArtifactError("export artifact 내용과 digest가 다릅니다.")
        if len(chunk.payload.splitlines()) != chunk.point_count:
            raise ExportArtifactError("export artifact point 수가 다릅니다.")
        insert = pg_insert if self.engine.dialect.name == "postgresql" else sqlite_insert
        connection.execute(insert(self.table).values(
            digest=chunk.digest, payload=chunk.payload, byte_count=len(chunk.payload),
            point_count=chunk.point_count, created_at=datetime.now(UTC),
        ).on_conflict_do_nothing(index_elements=[self.table.c.digest]))
        existing = connection.execute(select(
            self.table.c.byte_count, self.table.c.point_count,
        ).where(self.table.c.digest == chunk.digest)).one()
        if tuple(existing) != (len(chunk.payload), chunk.point_count):
            raise ExportArtifactError("동일 digest의 DB artifact 증거가 다릅니다.")

    def record_in_transaction(self, session, chunk):
        """fact·receipt·snapshot bytes를 같은 DB commit으로 확정한다."""
        connection = session.connection()
        if self.engine.dialect.name == "postgresql":
            deadline = time.monotonic() + 1.0
            statement = text("SELECT pg_try_advisory_xact_lock(:namespace, :key)")
            while not connection.scalar(
                statement, {"namespace": _LOCK_NAMESPACE, "key": _LOCK_KEY},
            ):
                if time.monotonic() >= deadline:
                    raise ExportGuardTimeout("DB artifact 저장 guard 대기 상한을 넘었습니다.")
                time.sleep(.01)
        self._insert(connection, chunk)

    def publish(self, chunk: PreparedExportChunk):
        # 이전 파일의 명시적인 이관·독립 검증 도구도 같은 불변 저장 계약을 쓴다.
        with self.exclusive(holder="publish"), self._connection(write=True) as connection:
            self._insert(connection, chunk)

    def _payload(self, digest, *, byte_count, point_count):
        self._validate(digest, byte_count, point_count)
        with self._connection() as connection:
            row = connection.execute(select(
                self.table.c.payload, self.table.c.byte_count, self.table.c.point_count,
            ).where(self.table.c.digest == digest)).one_or_none()
        from .metrics import observe_database_payload_read

        observe_database_payload_read("single", [] if row is None else [row])
        return self._verify_payload_row(row, digest, byte_count, point_count)

    @staticmethod
    def _verify_payload_row(row, digest, byte_count, point_count):
        if row is None or row.byte_count != byte_count or row.point_count != point_count:
            raise ExportArtifactError("DB export artifact가 없거나 손상됐습니다.")
        payload = bytes(row.payload)
        if len(payload) != byte_count or hashlib.sha256(payload).hexdigest() != digest:
            raise ExportArtifactError("DB export artifact 내용이 손상됐습니다.")
        if len(payload.splitlines()) != point_count:
            raise ExportArtifactError("DB export artifact point 수가 다릅니다.")
        return payload

    def verify(self, digest, *, byte_count, point_count):
        self._payload(digest, byte_count=byte_count, point_count=point_count)

    def present(self, digest, *, byte_count):
        self._validate(digest, byte_count)
        with self._connection() as connection:
            actual = connection.scalar(select(self.table.c.byte_count).where(
                self.table.c.digest == digest,
            ))
        if actual != byte_count:
            raise ExportArtifactError("DB export artifact가 없거나 손상됐습니다.")

    def present_many(self, refs):
        refs = list(refs)
        for start in range(0, len(refs), 256):
            batch = refs[start:start + 256]
            for ref in batch:
                self._validate(ref["artifact_digest"], ref["byte_count"])
            with self._connection() as connection:
                actual = dict(connection.execute(select(
                    self.table.c.digest, self.table.c.byte_count,
                ).where(self.table.c.digest.in_([r["artifact_digest"] for r in batch]))).all())
            if any(actual.get(r["artifact_digest"]) != r["byte_count"] for r in batch):
                raise ExportArtifactError("DB export artifact가 없거나 손상됐습니다.")

    def all_present(self, refs):
        try:
            with self.guard(shared=True):
                self.present_many(refs)
            return True
        except ExportArtifactError:
            return False

    def read(self, digest, *, byte_count, point_count):
        payload = self._payload(digest, byte_count=byte_count, point_count=point_count)
        yield from self._decoded_points(digest, payload, byte_count)

    def _decoded_points(self, digest, payload, byte_count, *, fields=None):
        from .export_artifacts import _DECODED_ARTIFACT_BYTES, _DECODED_ARTIFACT_ENTRIES

        fields = None if fields is None else tuple(fields)
        # engine별 기존 예산을 함께 쓴다. 다른 fieldset이나 전체 읽기를 대체하지 않는다.
        key = digest if fields is None else (digest, frozenset(fields))
        cache = self._decoded
        with cache.lock:
            entry = cache.entries.get(key)
            if entry is not None:
                cache.entries.move_to_end(key)
        if entry is None:
            # projection 여부와 무관하게 첫 읽기는 모든 JSON 행·point 형식을 검증한다.
            parse = (_decode_json_line if sys.get_int_max_str_digits()
                     == sys.int_info.default_max_str_digits else json.loads)
            items = tuple(parse(line) for line in payload.splitlines())
            if any(not isinstance(item, dict) for item in items):
                raise ExportArtifactError("DB export artifact point 형식이 다릅니다.")
            weight = byte_count
            if fields is not None:
                items = tuple(
                    {name: item[name] for name in fields if name in item} for item in items
                )
                try:
                    # ASCII escape 길이는 UTF-8 원문보다 보수적이다. fieldset key도 예산에 센다.
                    weight = (len(json.dumps(items, ensure_ascii=True, separators=(",", ":")))
                              + len(json.dumps((digest, fields), ensure_ascii=True)))
                except (TypeError, ValueError, OverflowError, RecursionError):
                    weight = None  # 보관 크기를 셀 수 없어도 기존 읽기·복사 계약은 유지한다.
            nested_keys = tuple(
                tuple(name for name, value in item.items() if isinstance(value, (dict, list)))
                for item in items
            )
            entry = (items, nested_keys, weight)
            if weight is not None:
                with cache.lock:
                    previous = cache.entries.pop(key, None)
                    if previous is not None:
                        cache.bytes -= previous[2]
                    cache.entries[key] = entry
                    cache.bytes += weight
                    while cache.entries and (cache.bytes > _DECODED_ARTIFACT_BYTES
                                             or len(cache.entries) > _DECODED_ARTIFACT_ENTRIES):
                        _, removed = cache.entries.popitem(last=False)
                        cache.bytes -= removed[2]
        for item, nested_keys in zip(entry[0], entry[1], strict=True):
            # 반환 순서·mutable 격리를 유지한다.
            # payload SHA/bytes/count는 cache hit도 앞서 검산한다.
            point = dict(item) if fields is None else {
                name: item[name] for name in fields if name in item
            }
            for name in nested_keys:
                if name in point:
                    point[name] = deepcopy(item[name])
            yield point

    def read_many(self, refs, *, fields=None):
        """실제 요청 창의 최대8개/8MiB만 같은 연결·guard에서 읽고 모두 검산한다.

        cache hit도 실제 DB payload의 SHA/bytes/point count를 확인한다. 전체 manifest를
        미리 읽거나 DB 불변성 검사를 metadata 존재 확인으로 대체하지 않는다.
        fields는 전체 검산·decode 뒤 보관할 projection을 지정한다. SQL/payload 검산은 줄이지 않는다.
        """
        expected = {}
        for ref in refs:
            digest, size, count = (
                ref["artifact_digest"], ref["byte_count"], ref["point_count"],
            )
            self._validate(digest, size, count)
            if digest in expected and expected[digest] != (size, count):
                raise ExportArtifactError("같은 artifact의 receipt가 서로 다릅니다.")
            expected[digest] = size, count
            if (len(expected) > MAX_ARTIFACT_READ_BATCH
                    or sum(size for size, _ in expected.values()) > MAX_ARTIFACT_READ_BYTES):
                raise ExportArtifactError("artifact batch 읽기 상한을 넘었습니다.")
        if not expected:
            return {}
        with self.guard(shared=True), self._connection() as connection:
            rows = {row.digest: row for row in connection.execute(select(
                self.table.c.digest, self.table.c.payload,
                self.table.c.byte_count, self.table.c.point_count,
            ).where(self.table.c.digest.in_(list(expected))))}
            from .metrics import observe_database_payload_read

            observe_database_payload_read("batch", list(rows.values()))
            if rows.keys() != expected.keys():
                raise ExportArtifactError("DB export artifact가 없거나 손상됐습니다.")
            result = {}
            for digest, (size, count) in expected.items():
                payload = self._verify_payload_row(rows[digest], digest, size, count)
                result[digest] = list(self._decoded_points(digest, payload, size, fields=fields))
            return result

    def read_shared(self, digest, *, byte_count, point_count):
        with self.guard(shared=True):
            return list(self.read(digest, byte_count=byte_count, point_count=point_count))

    def preload(self, refs):
        # public interface: metadata 존재는 한번에 검사한다. 내용은 읽을 때 검증한다.
        self.present_many(refs)

    def delete_many(self, digests, *, created_before):
        held = getattr(_context, "guards", {}).get(self.engine)
        if held is None or held[1]:
            raise ExportArtifactError("DB artifact 삭제에는 배타 guard가 필요합니다.")
        digests = list(digests)
        for digest in digests:
            self._validate(digest)
        with self._connection(write=True) as connection:
            rows = connection.execute(delete(self.table).where(
                self.table.c.digest.in_(list(digests)),
                self.table.c.created_at < created_before,
            ).returning(self.table.c.digest, self.table.c.byte_count)).all()
        return len(rows), sum(row.byte_count for row in rows)
