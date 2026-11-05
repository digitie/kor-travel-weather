from datetime import timedelta

import pytest

from kortravelweather.alert_events import normalize_alert


def row(**changes):
    return {
        "stnId": "108",
        "tmFc": "202611051100",
        "tmSeq": "12",
        "areaCode": "L1082500",
        "areaName": "부산동부",
        "warnVar": "2",
        "warnStress": "0",
        "command": "1",
        "cancel": "0",
        "startTime": "202611051200",
        **changes,
    }


@pytest.mark.parametrize(
    ("command", "action"),
    [
        ("1", "issued"),
        ("2", "released"),
        ("3", "extended"),
        ("6", "corrected"),
        ("7", "changed_issue"),
        ("8", "changed_release"),
    ],
)
def test_official_commands_remain_distinct(command, action):
    event = normalize_alert(row(command=command))
    assert event.action == action
    assert event.occurrence_id is None
    assert event.correlation_status == "unresolved"
    assert event.issued_at.utcoffset() == timedelta(hours=9)


def test_release_without_start_does_not_invent_occurrence():
    raw = row(command="2", endTime="202611051300")
    raw.pop("startTime")
    event = normalize_alert(raw)
    assert event.action == "released" and event.starts_at is None
    assert event.ends_at.hour == 13
    assert event.occurrence_id is None


def test_cancellation_is_not_a_release_and_has_same_announcement():
    original = normalize_alert(row())
    cancelled = normalize_alert(row(cancel="1"))
    assert cancelled.action == "cancelled"
    assert cancelled.announcement_id == original.announcement_id
    assert cancelled.event_id != original.event_id


def test_identity_does_not_depend_on_page_order_or_extra_metadata():
    first = normalize_alert(row())
    again = normalize_alert(dict(reversed(list(row(extra="ignored").items()))))
    assert first.event_id == again.event_id
    assert first.announcement_id == again.announcement_id
    assert normalize_alert(row(tmSeq="13")).event_id != first.event_id
    assert normalize_alert(row(startTime="202611051230")).event_id != first.event_id


def test_latest_official_tropical_night_major_warning():
    event = normalize_alert(row(warnVar="13", warnStress="2"))
    assert (event.warning_name, event.stress_name, event.action) == (
        "열대야",
        "중대경보",
        "issued",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"cancel": None},
        {"cancel": True},
        {"warnVar": "99"},
        {"warnStress": "99"},
        {"command": "9"},
        {"tmFc": "202602301100"},
        {"tmSeq": ""},
        {"areaCode": "11B10101"},
        {"stnId": {}},
    ],
)
def test_unknown_fields_never_become_known_issue_or_release(changes):
    assert normalize_alert(row(**changes)).action == "unknown"
