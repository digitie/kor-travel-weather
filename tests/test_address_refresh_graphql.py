"""수동 실행 문서가 고정 Dagster 버전의 실제 GraphQL schema와 일치한다."""

import ast
import re
from pathlib import Path

from graphql import parse, validate
from kortravelcommon.dagster_webserver import create_schema


def test_address_refresh_documents_match_installed_dagster_schema():
    path = Path(
        "packages/kor-travel-weather-admin/frontend/lib/dagster-scope.ts"
    )
    source = path.read_text()
    documents = re.findall(r"export const ADDRESS_REFRESH_\w+ = ('[^\n]+');", source)
    assert len(documents) == 3
    schema = create_schema().graphql_schema
    for raw in documents:
        errors = validate(schema, parse(ast.literal_eval(raw)))
        assert not errors, [error.message for error in errors]
    # 기존 native 입력은 보존하고, 새 mutation이 요청 ID를 native 실행 ID로 변환한다.
    metadata = schema.get_type("ExecutionMetadata")
    assert "tags" in metadata.fields and "runId" not in metadata.fields
