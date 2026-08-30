from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def remove_fields(value: Any, exclude_fields: list[str]) -> Any:
    """snapshot 비교 전에 변동 필드를 재귀적으로 제거합니다."""

    if isinstance(value, Mapping):
        return {
            key: remove_fields(item, exclude_fields)
            for key, item in value.items()
            if str(key) not in exclude_fields
        }
    if isinstance(value, list):
        return [remove_fields(item, exclude_fields) for item in value]
    return value


def assert_case(actual: Any, expected: Any, assertion: Mapping[str, Any]) -> None:
    """fixture assertion mode에 맞춰 replay 결과를 검증합니다."""

    mode = assertion.get("mode", "snapshot")

    if mode == "snapshot":
        exclude_fields = [str(field) for field in assertion.get("exclude_fields", [])]
        assert remove_fields(actual, exclude_fields) == remove_fields(expected, exclude_fields)
        return

    if mode == "schema_only":
        assert actual is not None
        return

    if mode == "required_fields":
        required_fields = [str(field) for field in assertion.get("required_fields", [])]
        for field in required_fields:
            assert _has_field(actual, field)
        return

    if mode == "count":
        assert _count(actual) == _expected_count(expected, assertion)
        return

    raise ValueError(f"Unknown assertion mode: {mode}")


def _has_field(value: Any, field_path: str) -> bool:
    if isinstance(value, list):
        return all(_has_field(item, field_path) for item in value)
    current = value
    for key in field_path.split("."):
        if not isinstance(current, Mapping) or key not in current:
            return False
        current = current[key]
    return True


def _count(value: Any) -> int | None:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, Mapping):
        raw_count = value.get("count")
        return int(raw_count) if raw_count is not None else None
    return None


def _expected_count(expected: Any, assertion: Mapping[str, Any]) -> int | None:
    if "count" in assertion:
        return int(assertion["count"])
    return _count(expected)
