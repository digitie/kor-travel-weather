"""snapshot 발급 TX 안의 참조를 순서 그대로 작은 문장으로 저장한다."""

from sqlalchemy import String, bindparam, func, literal, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session


def insert_snapshot_refs(connection, table, snapshot_id, generation_ids):
    generation_ids = tuple(generation_ids)
    bind = connection.get_bind(clause=table) if isinstance(connection, Session) else connection
    if generation_ids and bind.dialect.name == "postgresql":
        # 큰 FK 검사 한 문장이 5초 SQL 상한을 넘었다. TX/파일 guard는 나누지 않아
        # 뒤 묶음이 실패해도 snapshot과 앞 묶음의 참조가 함께 rollback된다.
        for start in range(0, len(generation_ids), 512):
            source = func.unnest(bindparam(
                None, list(generation_ids[start:start + 512]),
                type_=ARRAY(String()), unique=True,
            )).table_valued("generation_id", with_ordinality="ordinal").render_derived()
            connection.execute(table.insert().from_select(
                ["snapshot_id", "ordinal", "generation_id"],
                select(literal(snapshot_id), source.c.ordinal + start - 1, source.c.generation_id),
            ))
    else:
        # 빈 집합의 기존 INSERT 오류·비 PG 동작도 바꾸지 않는다.
        connection.execute(table.insert(), [
            {"snapshot_id": snapshot_id, "ordinal": ordinal, "generation_id": generation}
            for ordinal, generation in enumerate(generation_ids)
        ])
