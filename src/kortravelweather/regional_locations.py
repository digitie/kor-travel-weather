"""저배율 지도용 실제 대표 지점 선택. 기상 코드나 기온은 평균내지 않는다."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any


def representative_locations(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str | None], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        sido, sigungu = row.get("sido_name"), row.get("sigungu_name")
        if not isinstance(sido, str) or not sido.strip():
            continue
        lon, lat = float(row["longitude"]), float(row["latitude"])
        if not (
            math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90
        ):
            continue
        groups[("sido", sido, None)].append(row)
        if isinstance(sigungu, str) and sigungu.strip():
            groups[("sigungu", sido, sigungu)].append(row)
    result = []
    for (level, sido, sigungu), members in sorted(groups.items()):
        lon = math.fsum(float(row["longitude"]) for row in members) / len(members)
        lat = math.fsum(float(row["latitude"]) for row in members) / len(members)
        # 그룹 전체 정렬이나 가상 중심 마커 대신 가장 가까운 실제 ID를 선형 선택한다.
        chosen = min(
            members,
            key=lambda row: (
                (float(row["longitude"]) - lon) ** 2 + (float(row["latitude"]) - lat) ** 2,
                row["location_id"],
            ),
        )
        result.append(
            {
                "level": level,
                "region_key": json.dumps(
                    [sido, sigungu], ensure_ascii=False, separators=(",", ":")
                ),
                "label": sido if sigungu is None else f"{sido} {sigungu}",
                "sido_name": sido,
                "sigungu_name": sigungu,
                "location_id": chosen["location_id"],
                "location_updated_at": chosen["updated_at"],
                "coordinate_key": chosen["coordinate_key"],
                "member_count": len(members),
            }
        )
    return result
