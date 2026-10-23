"""위치 수와 독립적인 승인 vector/참조 메타데이터 예산.

파일 크기·페이지 크기를 늘리지 않는다. DB cursor도 작은 묶음만 읽고 닫는다.
"""

import json
from collections import defaultdict

from sqlalchemy import Text, case, cast, func, select

from .export_artifacts import ExportArtifactError

MAX_SOURCE_SCOPES = 8192
MAX_GENERATION_REFS = 65536
REF_FETCH_ROWS = 64
MAX_REF_LOCATION_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MANIFEST_STATE_RESERVE = 128


class ExportCapacityExceeded(ExportArtifactError):
    pass


class MetadataBudget:
    """digest와 같은 JSON 정규화의 UTF-8 바이트를 중간 문자열 없이 누적한다."""

    def __init__(self, maximum=MAX_MANIFEST_BYTES):
        self.maximum = maximum
        self.used = 0

    def add(self, value):
        encoder = json.JSONEncoder(
            ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"),
            allow_nan=False,
        )
        try:
            for part in encoder.iterencode(value):
                self.used += len(part.encode("utf-8"))
                if self.used > self.maximum:
                    raise ExportCapacityExceeded("승인 manifest 메타데이터 예산을 넘었습니다.")
        except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
            raise ExportCapacityExceeded("승인 manifest JSON이 올바르지 않습니다.") from exc
        return self.used


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
    statement = (
        select(
            table.c.generation_id, table.c.ordinal, table.c.artifact_digest,
            table.c.byte_count, table.c.point_count,
            case((oversized, None), else_=table.c.location_ids).label("location_ids"),
            oversized.label("oversized_locations"),
        )
        .where(table.c.generation_id.in_(generation_ids))
        .order_by(table.c.generation_id, table.c.ordinal)
        .limit(MAX_GENERATION_REFS + 1)
        .execution_options(stream_results=True, yield_per=REF_FETCH_ROWS,
                           max_row_buffer=REF_FETCH_ROWS)
    )
    budget = MetadataBudget()
    result = connection.execute(statement)
    try:
        for count, row in enumerate(result.mappings(), 1):
            if count > MAX_GENERATION_REFS or row["oversized_locations"]:
                raise ExportCapacityExceeded("승인 artifact 참조 상한을 넘었습니다.")
            ref = {key: value for key, value in row.items() if key != "oversized_locations"}
            budget.add(ref)
            grouped[ref["generation_id"]].append(ref)
    finally:
        result.close()
    return grouped
