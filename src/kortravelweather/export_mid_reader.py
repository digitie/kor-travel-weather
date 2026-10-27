"""같은 발표의 중기 육상·기온을 위치별 frozen 배열로 조합한다."""

import json
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select, text

from .export_artifacts import MAX_CHUNK_BYTES, MAX_CHUNK_POINTS, ExportArtifactError
from .export_forecasts import FrozenForecastExports
from .export_heads import _digest
from .export_inventory_selection import (
    approved_source_selection,
    catalog_source_columns,
    source_max_age,
)
from .export_snapshots import (
    MAX_PAGE_BYTES,
    MAX_SNAPSHOT_LOCATIONS,
    PAGE_META_RESERVE,
    ExportCursorInvalid,
    ExportSnapshotUnavailable,
)

MID_SOURCE = ("python-kma-api", "kma_mid_forecast")


def _at(value):
    clock = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ExportSnapshotUnavailable("중기 원천 시각에는 timezone이 필요합니다.")
    return clock.astimezone(UTC)


def paired_locations(scopes):
    """raw scope vector를 바꾸지 않고 같은 회차의 두 역할을 위치별로 인덱싱한다."""
    locations = defaultdict(dict)
    for index, scope in enumerate(scopes):
        part = scope.get("source_part")
        if (
            scope.get("provider") != MID_SOURCE[0]
            or scope.get("dataset_key") != MID_SOURCE[1]
            or scope.get("forecast_style") != "mid"
            or part not in {"land", "temperature"}
        ):
            raise ExportSnapshotUnavailable("중기 승인 vector의 제공처·역할이 다릅니다.")
        _at(scope["data_revision_at"])
        _at(scope["source_collected_at"])
        _at(scope["published_at"])
        for key in scope["location_ids"]:
            if part in locations[key]:
                raise ExportSnapshotUnavailable("같은 위치의 중기 역할이 중복됐습니다.")
            locations[key][part] = index
    if not 1 <= len(locations) <= MAX_SNAPSHOT_LOCATIONS:
        raise ExportSnapshotUnavailable("중기 승인 위치가 비었거나 상한을 넘었습니다.")
    result = []
    for key, parts in sorted(locations.items()):
        if set(parts) != {"land", "temperature"}:
            raise ExportSnapshotUnavailable("중기 육상·기온의 두 승인이 모두 필요합니다.")
        land, temperature = scopes[parts["land"]], scopes[parts["temperature"]]
        issued = _at(land["data_revision_at"])
        if issued != _at(temperature["data_revision_at"]):
            raise ExportSnapshotUnavailable("중기 육상·기온 발표 회차가 서로 다릅니다.")
        result.append({
            "location_id": key,
            **parts,
            "issued_at": issued.isoformat(),
            "published_at": max(_at(land["published_at"]),
                                _at(temperature["published_at"])).isoformat(),
        })
    return result


def _location_ref(scope, key):
    refs = [ref for ref in scope["refs"] if key in ref["location_ids"]]
    if len(refs) != 1:
        raise ExportSnapshotUnavailable("중기 위치가 단일 bounded chunk에 있지 않습니다.")
    ref = refs[0]
    if not (
        1 <= ref["point_count"] <= MAX_CHUNK_POINTS
        and 1 <= ref["byte_count"] <= MAX_CHUNK_BYTES
    ):
        raise ExportSnapshotUnavailable("중기 chunk의 point/byte 상한이 다릅니다.")
    return ref


class FrozenMidForecastExports(FrozenForecastExports):
    """기존 cursor/TTL/delta/ETag를 사용하며 두 part의 완전 vector만 새로 발급한다."""

    STYLE_DATASETS = {**FrozenForecastExports.STYLE_DATASETS, "mid": MID_SOURCE[1]}

    def __init__(self, *args, style="mid", **kwargs):
        if style != "mid":
            raise ExportSnapshotUnavailable("중기 reader는 mid style만 지원합니다.")
        contracts = {
            "providers": (MID_SOURCE[0],), "source_inventory": True,
            "expected_sources": (MID_SOURCE,), "optional_sources": (),
        }
        for key, expected in contracts.items():
            actual = kwargs.pop(key, expected)
            if (actual != expected if key == "source_inventory" else tuple(actual) != expected):
                raise ExportSnapshotUnavailable("중기 제공처/source 선택 계약이 다릅니다.")
        super().__init__(*args, style="mid", **contracts, **kwargs)
        self.selection["mid_pairing"] = 1
        self.resource = f"forecasts:mid:{_digest(self.selection)}"

    def page(self, *, limit, cursor):
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise ExportCursorInvalid("중기예보 page limit은 1~5,000입니다.")
        return super().page(limit=limit, cursor=cursor)

    def _previous_forecasts(self):
        frozen = super()._previous_forecasts()
        frozen["_mid_fallback"] = True
        return frozen

    def _prepare_manifest(self, manifest):
        if manifest.get("unconfirmed_sources"):
            raise ExportSnapshotUnavailable("중기 최신 수집의 전체 승인이 아직 불확실합니다.")
        pairs = paired_locations(manifest["scopes"])
        watermark = _at(manifest["next_since"])
        if any(_at(pair["published_at"]) > watermark for pair in pairs):
            raise ExportSnapshotUnavailable("중기 publication이 snapshot watermark 이후입니다.")
        # 공개 위치 ID 순서는 계약하지 않는다. 같은 물리 chunk를 이어 읽어 두 part의
        # bounded cache만으로 여러 기온 지역이 섞인 경우의 반복 JSON decode를 줄인다.
        pairs.sort(key=lambda pair: (*(
            _location_ref(manifest["scopes"][pair[part]], pair["location_id"])["artifact_digest"]
            for part in ("land", "temperature")
        ), pair["location_id"]))
        return {**manifest, "mid_locations": pairs}

    def _chunk(self, scope, key, cache):
        ref = _location_ref(scope, key)
        identity = (scope["generation_id"], ref["artifact_digest"])
        if cache.get("identity") != identity:
            points = defaultdict(list)
            for point in self.artifacts.read(
                ref["artifact_digest"], byte_count=ref["byte_count"],
                point_count=ref["point_count"],
            ):
                if (
                    point["provider"] != MID_SOURCE[0]
                    or point["forecast_style"] != "mid"
                    or point["dataset_key"] != MID_SOURCE[1]
                    or point["location_id"] not in ref["location_ids"]
                    or _at(point["issued_at"]) != _at(scope["data_revision_at"])
                    or not (_at(scope["data_revision_at"]) <= _at(point["known_at"])
                            <= _at(scope["source_collected_at"]))
                ):
                    raise ExportSnapshotUnavailable("중기 artifact와 승인된 원천이 다릅니다.")
                allowed = {"SKY", "POP"} if scope["source_part"] == "land" else {"TMN", "TMX"}
                if point["metric_key"] not in allowed:
                    raise ExportSnapshotUnavailable("중기 artifact의 metric 역할이 다릅니다.")
                points[point["location_id"]].append(point)
            cache.clear()
            cache.update(identity=identity, points=points)
        if not cache["points"].get(key):
            raise ExportSnapshotUnavailable("승인된 중기 위치의 값이 없습니다.")
        return cache["points"][key]

    def _page_items(self, manifest, state, limit, now):
        pairs, scopes = manifest["mid_locations"], manifest["scopes"]
        if state["p"] != 0 or state["g"] > len(pairs):
            raise ExportCursorInvalid("중기 위치 cursor의 범위가 올바르지 않습니다.")
        output, size = [], 0
        caches = {"land": {}, "temperature": {}}
        while state["g"] < len(pairs):
            pair = pairs[state["g"]]
            if self.since is not None and _at(pair["published_at"]) <= self.since:
                state["g"] += 1
                continue
            if len(output) >= limit:
                return output, True
            key = pair["location_id"]
            points = [point for part in ("land", "temperature") for point in self._chunk(
                scopes[pair[part]], key, caches[part],
            )]
            future = [point for point in points if _at(point["target_at"]) > now]
            if not future:
                # 지난 발표만 남은 위치 하나가 style 전체를 막지 않게 빼고 stale로 알린다.
                self._expired_omitted = True
                state["g"] += 1
                continue
            targets = sorted({_at(point["target_at"]) for point in future})
            metrics = defaultdict(dict)
            for point in future:
                metric, target = point["metric_key"], _at(point["target_at"])
                if target in metrics[metric]:
                    raise ExportSnapshotUnavailable("중기 위치·metric·시각이 중복됐습니다.")
                metrics[metric][target] = (
                    float(point["value_number"]) if point["value_number"] is not None
                    else point["value_text"]
                )
            item = {
                "location_id": key, "provider": MID_SOURCE[0], "forecast_style": "mid",
                # 같은 의미의 재확인은 과거 파일을 재사용한다. fact known_at은
                # 그대로 반환하고 새 수집 시각은 meta.source_collected_at으로 구분한다.
                "known_at": max(_at(point["known_at"]) for point in points).isoformat(),
                "published_at": pair["published_at"],
                "target_at": [target.isoformat() for target in targets],
                "metrics": {metric: [values.get(target) for target in targets]
                            for metric, values in sorted(metrics.items())},
            }
            item_size = len(json.dumps(item, ensure_ascii=False).encode()) + 1
            if item_size > MAX_PAGE_BYTES - PAGE_META_RESERVE:
                raise ExportSnapshotUnavailable(
                    "한 위치의 중기예보가 페이지 상한을 넘었습니다."
                )
            if len(output) >= limit or size + item_size > MAX_PAGE_BYTES - PAGE_META_RESERVE:
                return output, True
            output.append(item)
            size += item_size
            state["g"] += 1
        return output, False

    def _inventory_forecast_health(self, manifest, frozen, now):
        """기존 cursor도 같은 RR에서 canonical 위치·native 건강·현재 두 회차를 확인한다."""
        stale = frozen.get("_mid_fallback", False) or bool(
            manifest.get("unconfirmed_sources")
        ) or any(
            now - _at(scope[key]) > source_max_age(scope["dataset_key"])
            for scope in manifest["scopes"]
            for key in ("source_collected_at", "data_revision_at")
        )
        with self.repository.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection, connection.begin():
            connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            catalog = self.heads.metadata.tables["weather_locations"]
            rows = connection.execute(select(
                catalog.c.location_id, catalog.c.latitude, catalog.c.longitude,
                catalog.c.nx, catalog.c.ny, *catalog_source_columns(catalog),
            ).where(catalog.c.enabled.is_(True)).limit(
                MAX_SNAPSHOT_LOCATIONS + 1,
            )).mappings().all()
            if len(rows) > MAX_SNAPSHOT_LOCATIONS:
                return True
            try:
                selected = approved_source_selection(
                    connection, self.heads.metadata, rows, expected=self.expected_sources,
                )
                stale |= selected.unconfirmed
                current = [{
                    "provider": item.descriptor.provider,
                    "dataset_key": item.descriptor.dataset_key,
                    "forecast_style": item.descriptor.forecast_style,
                    "source_part": item.descriptor.source_part,
                    "location_ids": item.location_ids,
                    "data_revision_at": item.data_revision_at,
                    "source_collected_at": item.source_collected_at,
                    "published_at": item.published_at,
                } for item in selected.scopes]
                paired_locations(current)
                actual = {
                    (item.descriptor.namespace, item.location_ids, item.generation_id)
                    for item in selected.scopes
                }
                stale |= actual != {
                    (scope["namespace"], tuple(scope["location_ids"]), scope["generation_id"])
                    for scope in manifest["scopes"]
                }
            except (
                ExportArtifactError, ExportSnapshotUnavailable, KeyError, ValueError, TypeError,
            ):
                return True
        return stale
