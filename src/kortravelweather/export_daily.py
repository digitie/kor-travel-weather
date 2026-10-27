"""승인된 관측만 시간 bucket으로 축약하는 일간 요약 정본.

재조회/여러 제공처를 합산하지 않는다. 공개 reader는 미리 계산한 작은 summary만
읽고, 이 비공개 시간별 winner 상태를 다시 집계하지 않는다.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .export_heads import _digest

KST = ZoneInfo("Asia/Seoul")
SLOTS = {"morning": (5, 11), "noon": (11, 17), "evening": (17, 23)}
PROVIDERS = (
    "python-kma-api",
    "python-krforest-api",
    "python-krex-api",
    "python-khoa-api",
    "open_meteo",
    "weatherapi",
    "openweathermap",
    "visual_crossing",
    "tomorrow_io",
    "weatherbit",
    "weatherstack",
    "accuweather",
    "wttr_in",
    "python-airkorea-api",
)


def _time(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("관측 요약 시각에는 timezone이 필요합니다.")
    return result.astimezone(UTC)


def observation(point: dict[str, Any]):
    """지원하는 관측의 정량 단위와 시간 구간만 인정한다."""
    if point["forecast_style"] not in {"observed", "nowcast"}:
        return None
    target = _time(point["target_at"])
    number = point.get("value_number")
    if number is None:
        return None
    number = Decimal(str(number))
    if not number.is_finite():
        return None
    metric, unit = point["metric_key"], point.get("unit")
    if metric in {"T1H", "TEMP"} and unit in {"deg_c", "℃"} and -90 <= number <= 65:
        field = "temp"
    elif metric in {"REH", "HUMIDITY"} and unit == "%" and 0 <= number <= 100:
        field = "humidity"
    elif metric in {"WSD", "WIND_SPEED"} and unit in {"m_s", "m/s", "km/h"}:
        field = "wind"
        if unit == "km/h":
            number /= Decimal("3.6")
        if not 0 <= number <= 150:
            return None
    elif (
        point["provider"] == "python-kma-api"
        and metric == "RN1"
        and point["dataset_key"] == "kma_ultra_short_nowcast"
        and unit == "mm"
        and target.minute == target.second == target.microsecond == 0
        and 0 <= number <= 1000
    ):
        # 실제 관측 RN1은 발표/예보 PCP와 달리 지난 1시간의 양이다. 11시
        # 관측 RN1은 [10,11) morning에 귀속하며 누적 강수는 여기 들어오지 않는다.
        field = "precip"
        target -= timedelta(hours=1)
    elif metric == "SKY" and unit == "code" and number in {1, 3, 4}:
        field = "sky"
    else:
        return None
    local = target.astimezone(KST)
    slot = next((key for key, (start, end) in SLOTS.items() if start <= local.hour < end), None)
    if slot is None:
        return None
    known = _time(point["known_at"])
    priority = (
        PROVIDERS.index(point["provider"]) if point["provider"] in PROVIDERS else len(PROVIDERS)
    )
    # provider가 같은 시간에 여러 번 관측하면 가장 늦은 관측, 재수집이면 가장
    # 늦게 알게 된 값을 택한다. 완전 동률은 내용 hash로 결정하여 실행순서와 무관하다.
    rank = [priority, -target.timestamp(), -known.timestamp(), _digest(point)]
    cell = {"value": str(number), "provider": point["provider"], "rank": rank}
    if field == "precip":
        # 기존 normalizer는 '1mm 미만'을 숫자0+원문 qualifier로 보존한다.
        # 정확히0mm라고 단정하거나 비가 없었다고 바꾸지 않는다.
        cell["unknown_amount"] = "미만" in str(point.get("value_text") or "")
    return (
        (point["location_id"], local.date().isoformat(), slot),
        str(local.hour),
        field,
        cell,
    )


def merge_observations(state: dict, points, *, key: tuple[str, str, str], now: datetime):
    """한 위치/날짜/slot의 최대 6시간×5개 winner만 유지한다."""
    result = json.loads(json.dumps(state))
    now = _time(now)
    for point in points:
        if _time(point["target_at"]) > now.astimezone(UTC):
            continue
        parsed = observation(point)
        if parsed is None or parsed[0] != key:
            continue
        _, hour, field, candidate = parsed
        current = result.setdefault(hour, {}).get(field)
        if current is None or candidate["rank"] < current["rank"]:
            result[hour][field] = candidate
    return result


def summarize(location_id, day, slot, state, *, now: datetime):
    """sample_count는 metric행 수가 아닌 채택된 관측 시간 bucket 수다."""
    now = _time(now)
    values = {
        field: [Decimal(hour[field]["value"]) for hour in state.values() if field in hour]
        for field in ("temp", "humidity", "wind", "precip", "sky")
    }

    def avg(field):
        return float(sum(values[field]) / len(values[field])) if values[field] else None

    sky = Counter(values["sky"])
    representative = min(sky, key=lambda v: (-sky[v], v)) if sky else None
    rain = [hour["precip"] for hour in state.values() if "precip" in hour]
    unknown_rain = any(cell.get("unknown_amount") for cell in rain)
    # 구간 합계와 '강수 없음'은 구간의 모든 시간 RN1이 있을 때만 확정한다. 순환 수집으로
    # 일부 시간만 있으면 부분 합을 구간 합계로, 몇 시간의 0을 무강수로 내보내지 않는다.
    start, end_hour = SLOTS[slot]
    covered = len(rain) == end_hour - start
    wet = unknown_rain or any(v > 0 for v in values["precip"])
    end = datetime.fromisoformat(day).replace(hour=end_hour, tzinfo=KST)
    return {
        "location_id": location_id,
        "date": day,
        "slot": slot,
        "temp_min": float(min(values["temp"])) if values["temp"] else None,
        "temp_max": float(max(values["temp"])) if values["temp"] else None,
        "temp_avg": avg("temp"),
        "humidity_avg": avg("humidity"),
        "precip_sum_mm": float(sum(values["precip"])) if covered and not unknown_rain else None,
        "precip_occurred": True if wet else (False if covered else None),
        "sky_code": int(representative) if representative is not None else None,
        "wind_speed_avg": avg("wind"),
        "sample_count": len(state),
        "source_providers": sorted(
            {cell["provider"] for hour in state.values() for cell in hour.values()}
        ),
        "partial": now.astimezone(UTC) < end.astimezone(UTC),
    }
