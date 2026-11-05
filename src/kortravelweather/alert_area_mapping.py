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
    if len(code) == 2:
        return code in data["known_province_codes"]
    if len(code) == 5:
        return code in data["known_district_codes"]
    if code.endswith("00000000"):
        return code[:2] in data["known_province_codes"]
    if code.endswith("00000"):
        return code[:5] in data["known_district_codes"]
    return code[5:] in data["known_bjd_suffixes"].get(code[:5], "").split(",")


def candidate_areas(bjd_code: str) -> list[str]:
    if not known_bjd(bjd_code):
        return []
    if len(bjd_code) == 10:
        bjd_code = bjd_code[:2] if bjd_code.endswith("00000000") else bjd_code[:5]
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
    if (
        area is None
        or area["scope_status"] != "verified"
        or issued_at is None
        or issued_at.tzinfo is None
    ):
        return None
    mapping_start = datetime.fromisoformat(area_mapping()["mapping_valid_from"])
    mapping_end = area_mapping().get("mapping_valid_until")
    if issued_at < mapping_start or (
        mapping_end is not None and issued_at >= datetime.fromisoformat(mapping_end)
    ):
        return None
    start = source_time(area.get("code_valid_from"))
    end = source_time(area.get("code_valid_until"))
    if start is None or end is None or not start <= issued_at < end:
        return None
    return area
