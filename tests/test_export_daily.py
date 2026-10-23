from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from kortravelweather.export_daily import merge_observations, observation, summarize

KST = ZoneInfo("Asia/Seoul")
KEY = ("a", "2026-10-23", "morning")
NOW = datetime(2026, 10, 23, 12, tzinfo=KST)


def point(hour, metric="T1H", value="10", provider="python-kma-api", **changes):
    return {
        "location_id": "a",
        "provider": provider,
        "dataset_key": "kma_ultra_short_nowcast",
        "forecast_style": "nowcast",
        "metric_key": metric,
        "unit": {"T1H": "deg_c", "TEMP": "deg_c", "RN1": "mm", "PRECIP": "mm"}.get(metric, "code"),
        "target_at": datetime(2026, 10, 23, hour, tzinfo=KST).isoformat(),
        "known_at": NOW.isoformat(),
        "value_number": Decimal(value),
        **changes,
    }


def test_hour_winners_deduplicate_providers_replays_and_metric_rows():
    points = [
        point(6, value="12"),
        point(6, "RN1", "2"),
        point(7, "RN1", "3"),
        point(6, "TEMP", "99", provider="weatherapi"),
        point(6, "PRECIP", "200", provider="open_meteo"),
        point(7, value="14"),
    ]
    state = merge_observations({}, points, key=KEY, now=NOW)
    replay = merge_observations(state, points, key=KEY, now=NOW)
    assert replay == state
    summary = summarize(*KEY, replay, now=NOW)
    assert summary["temp_min"] == 12 and summary["temp_max"] == 14
    assert summary["temp_avg"] == 13 and summary["precip_sum_mm"] == 5
    assert summary["sample_count"] == 3  # rain[05,06), rain+temp[06,07), temp[07,08)
    assert summary["source_providers"] == ["python-kma-api"]
    assert summary["precip_occurred"] is True and summary["partial"] is False


def test_kst_slot_boundary_rain_interval_and_future_forecasts_are_not_observations():
    assert observation(point(5, "RN1", "1")) is None  # [04,05)는 morning 밖
    assert observation(point(11, "RN1", "1"))[0] == KEY  # [10,11)는 morning
    assert observation(point(11))[0][2] == "noon"
    state = merge_observations(
        {},
        [point(6, forecast_style="ultra_short"), point(13), point(6, location_id="other")],
        key=KEY,
        now=NOW,
    )
    assert state == {}
    empty = summarize(*KEY, state, now=NOW)
    assert empty["sample_count"] == 0 and empty["source_providers"] == []
    assert all(
        empty[k] is None
        for k in (
            "temp_avg",
            "humidity_avg",
            "precip_sum_mm",
            "precip_occurred",
            "sky_code",
            "wind_speed_avg",
        )
    )
    assert summarize(*KEY, {}, now=datetime(2026, 10, 23, 10, tzinfo=KST))["partial"] is True


def test_order_independent_provider_winner_and_cumulative_rain_exclusion():
    points = [
        point(6, value="10"),
        point(6, "TEMP", "30", provider="weatherapi"),
        point(6, "RN1", "20", provider="python-krforest-api"),
        point(6, "RN1", "999", unit="mm/day"),
    ]
    forward = merge_observations({}, points, key=KEY, now=NOW)
    reverse = merge_observations({}, reversed(points), key=KEY, now=NOW)
    assert forward == reverse
    result = summarize(*KEY, forward, now=NOW)
    assert result["temp_avg"] == 10 and result["precip_sum_mm"] is None
    assert result["source_providers"] == ["python-kma-api"]
