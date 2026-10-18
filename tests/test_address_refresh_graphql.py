"""수동 실행 문서가 고정 Dagster 버전의 실제 GraphQL schema와 일치한다."""

import ast
import re
from pathlib import Path

from dagster_graphql.schema import create_schema
from graphql import parse, validate


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
    # Dagster는 실행 ID의 외부 주입을 지원하지 않는다. 요청 식별자는 tag로 조회한다.
    metadata = schema.get_type("ExecutionMetadata")
    assert "tags" in metadata.fields and "runId" not in metadata.fields
