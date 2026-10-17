from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from kortravelweather.providers import base

STAMP = datetime(2026, 10, 17, 12, tzinfo=timezone(timedelta(hours=9)))


def _legacy_record(**kwargs: Any) -> dict[str, Any]:
    """변경 전 make_source_record/hash 경계를 독립 oracle로 보존한다."""
    payload = base.redact_secrets(dict(kwargs["payload"]))
    metadata = {"endpoint": base.redact_secrets(kwargs["endpoint"])}
    if kwargs.get("request_params"):
        metadata["request_params"] = base.redact_secrets(dict(kwargs["request_params"]))
    if kwargs.get("status_code") is not None:
        metadata["status"] = kwargs["status_code"]
    full = {"rows": [payload], "response_metadata": metadata}
    canonical = json.dumps(
        [kwargs["provider"], kwargs["dataset_key"], kwargs["location_id"],
         base.redact_secrets(full)],
        ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"),
    )
    return {
        "source_record_key": "sr_" + hashlib.sha256(canonical.encode()).hexdigest()[:48],
        "provider": kwargs["provider"], "dataset_key": kwargs["dataset_key"],
        "source_entity_type": "weather_response", "source_entity_id": kwargs["location_id"],
        "payload": full, "fetched_at": kwargs["fetched_at"],
    }


def _arguments(**overrides: Any) -> dict[str, Any]:
    return {
        "provider": "fixture", "dataset_key": "forecast", "location_id": "서울:1",
        "payload": {
            "hourly": {"time": ["2026-10-17T03:00:00Z"], "temperature_2m": [Decimal("18.50")]},
            "apiKey": "synthetic-a",
            "nested": ({"access-token": "synthetic-b", "지역": "서울 😀"},),
            "url": "https://fixture.invalid?service_key=synthetic-c&area=서울",
            "date": date(2026, 10, 17), "time": time(12, 30), "stamp": STAMP,
        },
        "endpoint": "https://fixture.invalid?api_key=synthetic-d&항목=기온",
        "request_params": {"serviceKey": "synthetic-e", "region": "서울", "at": STAMP},
        "status_code": 200, "fetched_at": STAMP,
        **overrides,
    }


@pytest.mark.parametrize("status", [None, 200, True, 200.0, Decimal("200"), "token=synthetic"])
def test_make_source_record_matches_original_identity_and_payload(status: Any) -> None:
    kwargs = _arguments(status_code=status)
    original = copy.deepcopy(kwargs)
    assert base.make_source_record(**kwargs) == _legacy_record(**kwargs)
    assert kwargs == original


def test_make_source_record_skips_second_full_payload_redaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = base.redact_secrets
    full_visits = 0

    def observed(value: Any) -> Any:
        nonlocal full_visits
        if isinstance(value, dict) and set(value) == {"rows", "response_metadata"}:
            full_visits += 1
        return original(value)

    monkeypatch.setattr(base, "redact_secrets", observed)
    record = base.make_source_record(**_arguments())
    assert full_visits == 0
    # 공개 raw 경계는 최적화된 내부 경로로 우회하지 않는다.
    assert base.source_record_key("fixture", "forecast", "서울:1", record["payload"]) == (
        record["source_record_key"]
    )
    assert full_visits == 1


def test_public_source_key_still_redacts_nested_raw_credentials() -> None:
    first = {"apiKey": "synthetic-a", "rows": [{"url": "x?token=synthetic-b&x=1"}]}
    second = {"apiKey": "synthetic-c", "rows": [{"url": "x?token=synthetic-d&x=1"}]}
    original = copy.deepcopy(first)
    assert base.source_record_key("fixture", "x", "서울", first) == (
        base.source_record_key("fixture", "x", "서울", second)
    )
    assert first == original


def test_record_payloads_are_independent_of_inputs_and_each_other() -> None:
    kwargs = _arguments()
    original = copy.deepcopy(kwargs)
    first = base.make_source_record(**kwargs)
    second = base.make_source_record(**kwargs)
    first["payload"]["rows"][0]["hourly"]["temperature_2m"].append(99)
    first["payload"]["response_metadata"]["request_params"]["region"] = "changed"
    assert kwargs == original
    assert second == _legacy_record(**kwargs)


def test_unicode_url_order_time_and_decimal_identity_preserve_legacy() -> None:
    for payload in (
        {"K": "İ", "ß": "한글", "😀": "x?KEY=synthetic&normal=1"},
        {"b": [1, None, True], "a": {"Decimal": Decimal("1.00"), "at": STAMP.astimezone(UTC)}},
        {"a": {"at": STAMP.astimezone(UTC), "Decimal": Decimal("1.00")}, "b": [1, None, True]},
    ):
        kwargs = _arguments(payload=payload)
        assert base.make_source_record(**kwargs) == _legacy_record(**kwargs)
