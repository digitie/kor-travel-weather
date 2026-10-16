from __future__ import annotations

import re
import sys

import pytest

from kortravelweather.providers.base import _normalized_key, redact_secrets


def test_normalized_key_preserves_unicode_and_mixed_provider_key_contract() -> None:
    def previous(key: object) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).lower().replace("-", "_")

    keys: list[object] = [
        "", "temp", "dt_txt", "3h", "api_key", "apiKey", "serviceKey", "accessToken",
        "api-key", "API_KEY", "aZ", "A", "a-1", "a b", "a_1", "İ", "K", "ß", "한글",
        None, 123, False, ("apiKey",),
    ]
    for key in keys:
        assert _normalized_key(key) == previous(key)
    # ASCII만 단축하므로 Unicode의 case 변환·경계 의미도 전부 기존과 같아야 한다.
    for codepoint in range(sys.maxunicode + 1):
        key = chr(codepoint)
        assert _normalized_key(key) == previous(key)


def test_nested_secret_redaction_keeps_aliases_and_independent_payloads() -> None:
    payload = {
        "main": {"temp": 14, "apiKey": "credential"},
        "rows": [
            {"serviceKey": "credential", "access-token": "credential", "appid": "credential"},
            {"url": "https://fixture.invalid?api_key=credential&x=1"},
        ],
        "tuple": ({"password": "credential"},),
        "wind": {"speed": 2, "deg": 180},
    }
    expected = {
        "main": {"temp": 14, "apiKey": "[REDACTED]"},
        "rows": [
            {"serviceKey": "[REDACTED]", "access-token": "[REDACTED]", "appid": "[REDACTED]"},
            {"url": "https://fixture.invalid?api_key=[REDACTED]&x=1"},
        ],
        "tuple": [{"password": "[REDACTED]"}],
        "wind": {"speed": 2, "deg": 180},
    }
    first = redact_secrets(payload)
    second = redact_secrets(payload)
    assert first == second == expected
    first["main"]["temp"] = 99
    first["rows"][0]["appid"] = "changed"
    first["tuple"][0]["password"] = "changed"
    assert second == expected
    assert payload["main"]["temp"] == 14
    assert payload["rows"][0]["appid"] == "credential"
    assert payload["tuple"][0]["password"] == "credential"


class _SelfReturningKey(str):
    def __str__(self) -> str:
        return self


class _SpoofedPredicateKey(_SelfReturningKey):
    def isascii(self) -> bool:
        return True

    def islower(self) -> bool:
        return True


class _CustomHashKey(_SelfReturningKey):
    def __hash__(self) -> int:
        return 1


class _CustomContainsKey(_SelfReturningKey):
    def __contains__(self, item: object) -> bool:
        return False


class _CustomEqualityKey(_SelfReturningKey):
    __hash__ = str.__hash__

    def __eq__(self, other: object) -> bool:
        return False


@pytest.mark.parametrize(
    "key",
    [
        _SpoofedPredicateKey("apiKey"),
        _CustomHashKey("api_key"),
        _CustomContainsKey("api-key"),
        _CustomEqualityKey("api_key"),
    ],
    ids=["spoofed-predicate", "custom-hash", "custom-contains", "custom-equality"],
)
def test_string_subclass_keys_cannot_bypass_secret_redaction(key: str) -> None:
    result = redact_secrets({key: "credential"})
    assert result[key] == "[REDACTED]"
    # 키 객체는 보존하지만 secret 집합 비교는 이전처럼 기본 str 결과여야 한다.
    assert next(iter(result)) is key
    assert type(_normalized_key(key)) is str
