"""공식 getPwnCd 발표 관측. 회차·최종 해제를 원문 밖에서 추정하지 않는다."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from kma.time_utils import parse_kma_datetime
from pydantic import BaseModel, ConfigDict

ALERT_DATASET = "kma_regional_alert_events"
ALERT_PROVIDER = "python-kma-api"
ALERT_VERSION = "datagokr-pwncd-260601-v1"
AlertAction = Literal[
    "issued",
    "released",
    "extended",
    "corrected",
    "changed_issue",
    "changed_release",
    "cancelled",
    "unknown",
]
ACTIONS: dict[str, AlertAction] = {
    "1": "issued",
    "2": "released",
    "3": "extended",
    "6": "corrected",
    "7": "changed_issue",
    "8": "changed_release",
}
WARNING_NAMES = {
    "1": "강풍",
    "2": "호우",
    "3": "한파",
    "4": "건조",
    "5": "폭풍해일",
    "6": "풍랑",
    "7": "태풍",
    "8": "대설",
    "9": "황사",
    "12": "폭염",
    "13": "열대야",
}
STRESS_NAMES = {"0": "주의보", "1": "경보", "2": "중대경보"}


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def source_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{12}", value.strip()):
        return None
    value = value.strip()
    try:
        return parse_kma_datetime(value[:8], value[8:])
    except ValueError:
        return None


class AlertEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    announcement_id: str
    area_code: str
    area_name: str
    warn_var: str
    warning_name: str | None
    warn_stress: str
    stress_name: str | None
    command: str
    cancel: str
    action: AlertAction
    issued_at: datetime | None
    starts_at: datetime | None
    ends_at: datetime | None
    all_ends_at: datetime | None
    occurrence_id: None = None
    correlation_status: Literal["unresolved"] = "unresolved"
    normalization_version: str = ALERT_VERSION


def normalize_alert(raw: Mapping[str, Any]) -> AlertEvent:
    """동일 발표 관측은 페이지·수집 시각·추가 필드와 무관하게 같은 식별자를 갖는다."""

    def field(key: str) -> str:
        value = raw.get(key)
        return (
            str(value).strip()
            if isinstance(value, (str, int)) and not isinstance(value, bool)
            else ""
        )

    keys = ("stnId", "tmFc", "tmSeq", "areaCode", "warnVar", "warnStress", "command")
    identity = {key: field(key) for key in keys}
    # 취소·발효시각 정정은 별도 관측이며 같은 공식 발표 아래 묶을 수 있다.
    observation = {
        **identity,
        **{
            key: field(key)
            for key in (
                "cancel",
                "startTime",
                "endTime",
                "allEndTime",
            )
        },
    }
    issued_at = source_time(identity["tmFc"])
    action: AlertAction = ACTIONS.get(identity["command"], "unknown")
    if (
        not issued_at
        or not re.fullmatch(r"[LS][0-9]{7}", identity["areaCode"])
        or identity["warnVar"] not in WARNING_NAMES
        or identity["warnStress"] not in STRESS_NAMES
        or not re.fullmatch(r"[0-9]+", identity["tmSeq"])
        or not re.fullmatch(r"[0-9]+", identity["stnId"])
        or observation["cancel"] not in {"0", "1"}
    ):
        action = "unknown"
    elif observation["cancel"] == "1":
        action = "cancelled"
    return AlertEvent(
        event_id="wae_" + canonical_hash(observation),
        announcement_id="waa_" + canonical_hash(identity),
        area_code=identity["areaCode"],
        area_name=field("areaName")[:200],
        warn_var=identity["warnVar"],
        warning_name=WARNING_NAMES.get(identity["warnVar"]),
        warn_stress=identity["warnStress"],
        stress_name=STRESS_NAMES.get(identity["warnStress"]),
        command=identity["command"],
        cancel=observation["cancel"],
        action=action,
        issued_at=issued_at,
        starts_at=source_time(observation["startTime"]),
        ends_at=source_time(observation["endTime"]),
        all_ends_at=source_time(observation["allEndTime"]),
    )


# provider 오류 원문·URL 대신 공개할 수 있는 고정 분류만 남긴다.
ALERT_SAFE_ERROR_CODES = frozenset(
    {
        "ALERT_PAGE_METADATA",
        "ALERT_PAGE_ROWS",
        "ALERT_PAGE_TOTAL_CHANGED",
        "ALERT_PAGE_DUPLICATE",
        "ALERT_TOO_MANY_ROWS",
        "ALERT_RESPONSE_TOO_LARGE",
        "ALERT_FIRST_PAGE_CHANGED",
        "ALERT_RUN_OWNERSHIP_LOST",
        "ALERT_TIMEOUT",
        "ALERT_PROVIDER_AUTH",
        "ALERT_PROVIDER_RATE_LIMIT",
        "ALERT_PROVIDER_UNAVAILABLE",
        "ALERT_COLLECTION_FAILED",
    }
)
