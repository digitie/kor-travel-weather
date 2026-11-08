"""위치 수와 독립적인 승인 vector/참조 메타데이터 예산.

파일 크기·페이지 크기를 늘리지 않는다. DB cursor도 작은 묶음만 읽고 닫는다.
"""

import hashlib
import json
from collections import defaultdict

from sqlalchemy import String, Text, any_, bindparam, case, cast, func, select
from sqlalchemy.dialects.postgresql import ARRAY

from .export_artifacts import ExportArtifactError

MAX_SOURCE_SCOPES = 8192
MAX_GENERATION_REFS = 65536
# 작은64행 fetch는 실제3305개 plan/ref에서108회 왕복했다. 별도 고정 묶음으로 줄이되
# SQL이 반환하는 JSON의 단건·누적 거부 상한은 그대로다(투영 JSON 최대16/32MiB/묶음).
# Python 객체·드라이버 overhead까지 포함한 RSS 상한을 뜻하지 않는다.
REF_FETCH_ROWS = 256
PLAN_FETCH_ROWS = 128
MAX_REF_LOCATION_BYTES = 64 * 1024
MAX_SCOPE_PLAN_BYTES = 256 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MANIFEST_STATE_RESERVE = 128
FAST_FETCH_ROWS = 1024
FAST_FETCH_BYTES = 8 * 1024 * 1024


def _metadata_fetch_rows(connection, table, column, predicate, count, maximum, fallback):
    """같은 RR에서 비압축 JSON 전체가 8MiB 이하면 왕복만 묶는다.

    압축 TOAST 크기는 메모리 크기의 상한이 아니므로 작은 기존 buffer로 돌아간다.
    이 예비 조회는 JSON을 해석/전송하지 않는다. 거부·누적 예산 검사는 기존 경로 그대로다.
    READ COMMITTED에서는 두 문장 사이의 변경 때문에 크기 증명을 재사용하지 않는다.
    """
    if (count <= FAST_FETCH_ROWS // 2 or connection.dialect.name != "postgresql"
            or connection.get_execution_options().get("isolation_level")
            not in {"REPEATABLE READ", "SERIALIZABLE"}):
        return fallback
    sizes = select(
        (func.coalesce(func.pg_column_size(column), 0) + 4).label("size"),
        func.pg_column_compression(column).label("compression"),
    ).select_from(table).where(predicate).limit(maximum + 1).subquery()
    row = connection.execute(select(
        func.coalesce(func.sum(sizes.c.size), 0),
        func.count(), func.bool_or(sizes.c.compression.is_not(None)),
        func.current_setting("server_encoding"),
    )).one()
    if row[0] <= FAST_FETCH_BYTES and row[1] <= maximum and not row[2] and row[3] == "UTF8":
        return FAST_FETCH_ROWS
    return fallback


class ExportCapacityExceeded(ExportArtifactError):
    pass


class MetadataBudget:
    """digest와 같은 JSON 정규화의 UTF-8 바이트를 중간 문자열 없이 누적한다."""

    def __init__(self, maximum=MAX_MANIFEST_BYTES):
        self.maximum = maximum
        self.used = 0
        self.encoder = json.JSONEncoder(
            ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"),
            allow_nan=False,
        )

    def add(self, value):
        try:
            for part in self.encoder.iterencode(value):
                self.used += len(part.encode("utf-8"))
                if self.used > self.maximum:
                    raise ExportCapacityExceeded("승인 manifest 메타데이터 예산을 넘었습니다.")
        except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
            raise ExportCapacityExceeded("승인 manifest JSON이 올바르지 않습니다.") from exc
        return self.used

    def add_bounded_record(self, value, maximum):
        """SQL에서 개별 크기를 제한한 record만 C encoder로 빠르게 계산한다."""
        try:
            size = len(self.encoder.encode(value).encode("utf-8"))
        except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
            raise ExportCapacityExceeded("승인 manifest JSON이 올바르지 않습니다.") from exc
        self.used += size
        if size > maximum or self.used > self.maximum:
            raise ExportCapacityExceeded("승인 manifest 메타데이터 예산을 넘었습니다.")
        return self.used


def bounded_manifest_digest(value, maximum):
    """단건·누적 예산을 검사한 manifest를 C encoder 한 번으로 제한·해시한다.

    SQL에서 단건을 제한하고 호출자가 누적 예산을 검사한 트리만 받는다. 최종 구조의
    괄호·키까지 UTF-8 byte 상한에 포함하며 기존 정규 JSON digest와 같은 값을 낸다.
    """
    try:
        payload = MetadataBudget().encoder.encode(value).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
        raise ExportCapacityExceeded("승인 manifest JSON이 올바르지 않습니다.") from exc
    if len(payload) > maximum:
        raise ExportCapacityExceeded("승인 manifest 메타데이터 예산을 넘었습니다.")
    return hashlib.sha256(payload).hexdigest()


def read_generation_refs(connection, table, generation_ids):
    """같은 MVCC에서 server cursor로 참조만 읽는다. 파일 I/O는 호출자가 나중에 한다."""
    generation_ids = tuple(dict.fromkeys(generation_ids))
    if len(generation_ids) > MAX_SOURCE_SCOPES:
        raise ExportCapacityExceeded("승인 source vector 상한을 넘었습니다.")
    grouped = defaultdict(list)
    if not generation_ids:
        return grouped
    # 잘못된 큰 JSON을 드라이버의 64행 버퍼에 먼저 적재하지 않는다.
    oversized = func.octet_length(cast(table.c.location_ids, Text)) > MAX_REF_LOCATION_BYTES
    predicate = (any_of(table.c.generation_id, generation_ids)
                 if connection.dialect.name == "postgresql"
                 else table.c.generation_id.in_(generation_ids))
    # 참조는 고정 작은 묶음 유지: 큰 refs 표의 크기 예비 조회가 실제로 더 느렸다.
    fetch_rows = REF_FETCH_ROWS
    statement = (
        select(
            table.c.generation_id, table.c.ordinal, table.c.artifact_digest,
            table.c.byte_count, table.c.point_count,
            case((oversized, None), else_=table.c.location_ids).label("location_ids"),
            oversized.label("oversized_locations"),
        )
        .where(predicate)
        .order_by(table.c.generation_id, table.c.ordinal)
        .limit(MAX_GENERATION_REFS + 1)
        .execution_options(stream_results=True, yield_per=fetch_rows,
                           max_row_buffer=fetch_rows)
    )
    budget = MetadataBudget()
    result = connection.execute(statement)
    try:
        for count, row in enumerate(result.mappings(), 1):
            if count > MAX_GENERATION_REFS or row["oversized_locations"]:
                raise ExportCapacityExceeded("승인 artifact 참조 상한을 넘었습니다.")
            ref = {key: value for key, value in row.items() if key != "oversized_locations"}
            budget.add_bounded_record(ref, MAX_REF_LOCATION_BYTES + 512)
            grouped[ref["generation_id"]].append(ref)
    finally:
        result.close()
    return grouped


def any_of(column, values):
    """``column = ANY(배열 파라미터 하나)``. 수천 개짜리 ``IN`` 목록은 psycopg가 값마다 파라미터로
    바꾸느라 요청마다 수 초를 썼다(2026-11-02 odroid: 7문장에 3.0초)."""
    return column == any_(bindparam(None, list(values), type_=ARRAY(String()), unique=True))


def read_scope_plans(connection, table, attempt_ids, *, byte_sizes=None):
    """descriptor를 attempt마다 한 번 읽고 선택적으로 같은 검산의 UTF-8 바이트 수도 돌려준다."""
    attempt_ids = tuple(dict.fromkeys(attempt_ids))
    if len(attempt_ids) > MAX_SOURCE_SCOPES:
        raise ExportCapacityExceeded("승인 source plan 수가 상한을 넘었습니다.")
    if not attempt_ids:
        return {}
    oversized = func.octet_length(cast(table.c.descriptor, Text)) > MAX_SCOPE_PLAN_BYTES
    predicate = any_of(table.c.attempt_id, attempt_ids)
    fetch_rows = _metadata_fetch_rows(
        connection, table, table.c.descriptor, predicate, len(attempt_ids),
        MAX_SOURCE_SCOPES, PLAN_FETCH_ROWS,
    )
    statement = select(
        table.c.attempt_id,
        case((oversized, None), else_=table.c.descriptor).label("descriptor"),
        oversized.label("oversized_descriptor"),
    ).where(predicate).order_by(table.c.attempt_id).limit(
        MAX_SOURCE_SCOPES + 1,
    ).execution_options(
        stream_results=True, yield_per=fetch_rows, max_row_buffer=fetch_rows,
    )
    budget, plans = MetadataBudget(), {}
    result = connection.execute(statement)
    try:
        for row in result.mappings():
            if row["oversized_descriptor"] or row["descriptor"] is None:
                raise ExportCapacityExceeded("승인 source plan 메타데이터 상한을 넘었습니다.")
            before = budget.used
            budget.add_bounded_record(row["descriptor"], MAX_SCOPE_PLAN_BYTES)
            if byte_sizes is not None:
                byte_sizes[row["attempt_id"]] = budget.used - before
            plans[row["attempt_id"]] = row["descriptor"]
    finally:
        result.close()
    if set(plans) != set(attempt_ids):
        raise ExportCapacityExceeded("승인 source의 불변 plan이 유실됐습니다.")
    return plans
