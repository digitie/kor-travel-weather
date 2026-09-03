"""Small, provider-neutral helpers for projecting active weather alerts.

KMA's warning-list endpoint is an event feed: a row can announce a warning or
announce that a warning was lifted.  Persisting both events is important for
the append-only lineage, but consumers must not render a lifted/expired event
as an active warning.  This module keeps that projection in the core package
so the repository and API use exactly the same policy.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from .models import WeatherValue

_RELEASE_RE = re.compile(r"(?:해제|취소|종료|소멸|cancel(?:led)?|expired|ended)", re.IGNORECASE)
_ACTION_RE = re.compile(
    r"(?:발표|발령|변경|연장|재발표|해제|취소|종료|소멸|cancel(?:led)?|expired|ended)",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"[·,/、+]+|\s+및\s+")
_NOISE_RE = re.compile(
    r"(?:^\s*\[?특보\]?\s*)|(?:제\s*\d+[-‐‑–]\d+호\s*[:：]?\s*)|"
    r"(?:\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}[.\s-]*\d{1,2}:?\d{0,2})|"
    r"(?:\([^)]*\))",
    re.IGNORECASE,
)


def _payload_value(row: WeatherValue, *names: str) -> Any:
    for name in names:
        value = row.payload.get(name)
        if value not in (None, ""):
            return value
    return None


def _title(row: WeatherValue) -> str:
    value = row.value_text or _payload_value(row, "title", "message", "msg")
    return str(value or "기상특보").strip()


def _station(row: WeatherValue) -> str:
    value = _payload_value(row, "stn_id", "stnId", "station_id", "stationId")
    return str(value).strip() if value not in (None, "") else "unknown"


def _warning_names(title: str) -> tuple[str, ...]:
    """Extract stable warning names from a human-readable KMA title."""
    cleaned = _NOISE_RE.sub(" ", title)
    # The part before the action is the warning identity (for example,
    # ``호우주의보·강풍주의보``).  If no action is present, retain the whole
    # title so custom providers still get deterministic event semantics.
    action_match = _ACTION_RE.search(cleaned)
    if action_match:
        cleaned = cleaned[: action_match.start()]
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" :：-*_")
    names = [part.strip() for part in _SPLIT_RE.split(cleaned) if part.strip()]
    return tuple(dict.fromkeys(names)) or ("__all__",)


def _is_release(row: WeatherValue) -> bool:
    action = _payload_value(row, "alert_action", "action", "status")
    if isinstance(action, str) and action.strip().lower() in {
        "release",
        "released",
        "cancel",
        "cancelled",
        "expired",
        "해제",
        "취소",
        "종료",
    }:
        return True
    return bool(_RELEASE_RE.search(_title(row)))


def active_alert_values(
    rows: Iterable[WeatherValue], *, now: datetime | None = None
) -> list[WeatherValue]:
    """Return only currently active warning facts from an event projection.

    Rows are evaluated newest-first per issuing station and warning name.  A
    newer release therefore suppresses all older announcements while a newer
    announcement re-activates the warning.  Explicit ``valid_until`` values
    are also honoured.  The returned list is deterministic and contains at
    most one latest announcement for each active warning identity.
    """
    instant = now or datetime.now(UTC)
    prepared: list[WeatherValue] = []
    for row in rows:
        if row.valid_until is not None and row.valid_until <= instant:
            continue
        prepared.append(row)
    def value_id(row: WeatherValue) -> str:
        return row.identity_key()

    prepared.sort(
        key=lambda row: (
            row.target_at or row.issued_at or row.observed_at or row.collected_at,
            row.known_at or datetime.min.replace(tzinfo=UTC),
            row.source_record_key,
            value_id(row),
        ),
        reverse=True,
    )

    state: dict[tuple[str, str], bool] = {}
    selected: dict[tuple[str, str], WeatherValue] = {}
    for row in prepared:
        action_release = _is_release(row)
        for name in _warning_names(_title(row)):
            identity = (_station(row), name)
            if identity in state:
                continue
            state[identity] = not action_release
            if not action_release:
                selected[identity] = row

    return sorted(
        selected.values(),
        key=lambda row: (
            row.target_at or row.issued_at or row.observed_at or row.collected_at,
            row.known_at or datetime.min.replace(tzinfo=UTC),
            value_id(row),
        ),
        reverse=True,
    )
