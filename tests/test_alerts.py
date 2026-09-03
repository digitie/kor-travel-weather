from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kortravelweather.alerts import active_alert_values
from kortravelweather.models import ForecastStyle, WeatherValue


def _alert(
    *,
    now: datetime,
    title: str,
    source_record_key: str,
    target_offset: timedelta,
    valid_until: datetime | None = None,
) -> WeatherValue:
    event_at = now + target_offset
    return WeatherValue(
        location_id="alert-location",
        provider="python-kma-api",
        dataset_key="kma_weather_alerts",
        weather_domain="weather_alert",
        forecast_style=ForecastStyle.OBSERVED,
        metric_key="ALERT",
        target_at=event_at,
        known_at=event_at,
        valid_until=valid_until,
        value_text=title,
        payload={"stn_id": "108", "title": title},
        source_record_key=source_record_key,
    )


def test_active_alert_projection_expires_old_announcement_without_end_time() -> None:
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)
    old = _alert(
        now=now,
        title="호우주의보 발표",
        source_record_key="old-announcement",
        target_offset=-timedelta(days=4),
    )

    assert active_alert_values([old], now=now) == []


def test_expired_release_tombstone_suppresses_older_announcement() -> None:
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)
    announcement = _alert(
        now=now,
        title="호우주의보 발표",
        source_record_key="announcement",
        target_offset=-timedelta(days=1),
    )
    # The release's validity may already have elapsed by the time it is
    # projected, but the event is still required to cancel the announcement.
    release = _alert(
        now=now,
        title="호우주의보 해제",
        source_record_key="release",
        target_offset=-timedelta(hours=1),
        valid_until=now - timedelta(minutes=1),
    )

    assert active_alert_values([announcement, release], now=now) == []


def test_recent_announcement_remains_active() -> None:
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)
    recent = _alert(
        now=now,
        title="강풍주의보 발표",
        source_record_key="recent-announcement",
        target_offset=-timedelta(hours=1),
    )

    assert active_alert_values([recent], now=now) == [recent]


def test_future_validity_overrides_default_age_cutoff() -> None:
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)
    still_valid = _alert(
        now=now,
        title="폭염주의보 발표",
        source_record_key="still-valid",
        target_offset=-timedelta(days=4),
        valid_until=now + timedelta(days=2),
    )

    assert active_alert_values([still_valid], now=now) == [still_valid]
