"""중기 두 원천의 불변 index와 bounded 위치 조합. DB 없는 순수 계약 회귀."""

from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from kortravelweather.export_mid_reader import FrozenMidForecastExports, paired_locations
from kortravelweather.export_snapshots import ExportCursorInvalid, ExportSnapshotUnavailable

NOW = datetime(2026, 10, 23, 10, tzinfo=UTC)
ISSUED = NOW - timedelta(hours=1)


def scopes(keys=("a", "b")):
    return [{
        "provider": "python-kma-api", "dataset_key": "kma_mid_forecast",
        "forecast_style": "mid", "source_part": part, "location_ids": list(keys),
        "namespace": part, "generation_id": part,
        "source_scope": part, "data_revision_at": ISSUED.isoformat(),
        "source_collected_at": (ISSUED + timedelta(minutes=5 + index)).isoformat(),
        "published_at": (ISSUED + timedelta(minutes=10 + index)).isoformat(),
        "refs": [{"artifact_digest": part, "point_count": 2 * len(keys), "byte_count": 100,
                  "location_ids": list(keys)}],
    } for index, part in enumerate(("land", "temperature"))]


def point(scope, key, metric, day, *, number=None, description=None):
    return {
        "location_id": key, "provider": scope["provider"],
        "dataset_key": scope["dataset_key"], "forecast_style": "mid",
        "issued_at": scope["data_revision_at"], "known_at": scope["source_collected_at"],
        "target_at": (NOW + timedelta(days=day)).isoformat(), "metric_key": metric,
        "value_number": number, "value_text": description,
    }


class Files:
    def __init__(self, selected):
        self.reads = []
        self.points = {}
        for scope in selected:
            self.points[scope["source_part"]] = [
                value for key in scope["location_ids"] for value in (
                    [point(scope, key, "SKY", 1, description="구름많고 비"),
                     point(scope, key, "POP", 1, number="20")]
                    if scope["source_part"] == "land" else
                    [point(scope, key, "TMN", 1, number="5"),
                     point(scope, key, "TMX", 2, number="20")]
                )
            ]

    def guard(self, **_):
        return nullcontext()

    def read(self, digest, **_):
        self.reads.append(digest)
        return iter(self.points[digest])


def reader(selected):
    return FrozenMidForecastExports(SimpleNamespace(), Files(selected), signing_key="test-only")


def manifest(selected):
    return {"scopes": selected, "next_since": NOW.isoformat(), "unconfirmed_sources": False}


def test_pair_index_retains_whole_raw_vector_and_uses_late_part_watermark():
    selected = scopes()
    original = deepcopy(selected)
    frozen = reader(selected)._prepare_manifest(manifest(selected))
    assert frozen["scopes"] == original == selected
    assert [item["location_id"] for item in frozen["mid_locations"]] == ["a", "b"]
    assert all(item["published_at"] == selected[1]["published_at"]
               for item in frozen["mid_locations"])


@pytest.mark.parametrize("failure", ["missing", "different_issue", "duplicate", "unconfirmed"])
def test_incomplete_pairs_never_become_new_snapshot(failure):
    selected = scopes()
    proposed = manifest(selected)
    if failure == "missing":
        selected.pop()
    elif failure == "different_issue":
        selected[1]["data_revision_at"] = (ISSUED - timedelta(hours=12)).isoformat()
    elif failure == "duplicate":
        selected.append(deepcopy(selected[0]))
    else:
        proposed["unconfirmed_sources"] = True
    with pytest.raises(ExportSnapshotUnavailable):
        reader(selected)._prepare_manifest(proposed)


def test_future_union_alignment_preserves_sky_and_source_clock_and_two_chunk_cache():
    selected = scopes()
    handle = reader(selected)
    frozen = handle._prepare_manifest(manifest(selected))
    state = {"g": 0, "p": 0}
    output, more = handle._page_items(frozen, state, 5000, NOW)
    assert len(output) == 2 and not more
    assert handle.artifacts.reads == ["land", "temperature"]
    first = output[0]
    assert first["known_at"] == selected[1]["source_collected_at"]
    assert first["metrics"] == {
        "POP": [20.0, None], "SKY": ["구름많고 비", None],
        "TMN": [5.0, None], "TMX": [None, 20.0],
    }
    assert first["target_at"] == sorted(first["target_at"])


def test_pair_delta_waits_for_both_and_location_cursor_can_exceed_raw_scope_count():
    selected = scopes(tuple(f"p-{i:03}" for i in range(10)))
    handle = reader(selected)
    frozen = handle._prepare_manifest(manifest(selected))
    handle.since = ISSUED + timedelta(minutes=10, seconds=30)
    state = {"g": 5, "p": 0}
    output, more = handle._page_items(frozen, state, 1, NOW)
    assert output[0]["location_id"] == "p-005" and state["g"] == 6 and more
    handle.since = ISSUED + timedelta(minutes=11)
    assert handle._page_items(frozen, {"g": 0, "p": 0}, 1, NOW) == ([], False)


@pytest.mark.parametrize("limit", [0, -1, 5001, True])
def test_public_limit_is_bounded_without_accessing_database(limit):
    with pytest.raises(ExportCursorInvalid):
        reader(scopes()).page(limit=limit, cursor=None)


def test_chunk_byte_bound_and_actual_source_clock_are_checked_before_output():
    selected = scopes()
    handle = reader(selected)
    frozen = handle._prepare_manifest(manifest(selected))
    selected[0]["refs"][0]["byte_count"] = 8 * 1024 * 1024 + 1
    with pytest.raises(ExportSnapshotUnavailable):
        handle._page_items(frozen, {"g": 0, "p": 0}, 1, NOW)
    selected[0]["refs"][0]["byte_count"] = 100
    handle.artifacts.points["land"][0]["known_at"] = NOW.isoformat()
    with pytest.raises(ExportSnapshotUnavailable):
        handle._page_items(frozen, {"g": 0, "p": 0}, 1, NOW)


def test_cursor_uses_supported_mid_resource_and_cannot_change_selection():
    handle = reader(scopes())
    state = {"v": 1, "s": "00000000-0000-0000-0000-000000000001",
             "r": handle.resource, "l": 1, "g": 0, "p": 0}
    assert handle._decode(handle._encode(state)) == state
    with pytest.raises(ExportSnapshotUnavailable):
        FrozenMidForecastExports(SimpleNamespace(), Files(scopes()), signing_key="test-only",
                                 providers=["open_meteo"])


def test_pair_requires_aware_issue_and_nonempty_inventory():
    with pytest.raises(ExportSnapshotUnavailable):
        paired_locations([])
    selected = scopes()
    selected[0]["data_revision_at"] = "2026-10-23T09:00:00"
    with pytest.raises(ExportSnapshotUnavailable):
        paired_locations(selected)
