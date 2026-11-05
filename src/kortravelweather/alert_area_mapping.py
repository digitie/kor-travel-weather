"""공식 특보 구역과 법정동의 검증된 전체 행정구역 교집합."""

from __future__ import annotations

import json
import re
from datetime import datetime
from functools import cache
from importlib.resources import files
from typing import Any

from .alert_events import source_time


@cache
def area_mapping() -> dict[str, Any]:
    return json.loads(
        files("kortravelweather").joinpath("reference/alert_areas.json").read_text(encoding="utf-8")
    )


@cache
def area_index() -> dict[str, dict[str, Any]]:
    return {area["area_code"]: area for area in area_mapping()["areas"]}


def known_bjd(code: str) -> bool:
    if not re.fullmatch(r"(?:[0-9]{2}|[0-9]{5}|[0-9]{10})", code):
        return False
    data = area_mapping()
    return (
        code in data["known_province_codes"]
        if len(code) == 2
        else code[:5] in data["known_district_codes"]
    )


def candidate_areas(bjd_code: str) -> list[str]:
    if not known_bjd(bjd_code):
        return []
    return [
        code
        for code, area in area_index().items()
        if area["scope_status"] == "verified"
        and any(
            bjd_code.startswith(prefix) or prefix.startswith(bjd_code)
            for prefix in area["bjd_prefixes"]
        )
    ]


def scope_at(area_code: str, issued_at: datetime | None) -> dict[str, Any] | None:
    area = area_index().get(area_code)
    if area is None or area["scope_status"] != "verified" or issued_at is None:
        return None
    start = source_time(area.get("valid_from"))
    end = source_time(area.get("valid_until"))
    if start is None or end is None or not start <= issued_at < end:
        return None
    return area
