"""한 preflight의 key 메모가 검증·파일 격리·출력을 보존하는지 확인한다."""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.mmap_dict import MmapedDict
from prometheus_client.multiprocess import MultiProcessCollector

from kortravelweather import metrics


def _key(name: str) -> str:
    return json.dumps([name, name + "_total", {}, "검증 날씨 ☀"], ensure_ascii=False)


def _files(tmp_path: Path, keys: list[str], count: int = 2) -> list[str]:
    paths = []
    for index in range(count):
        path = tmp_path / f"counter_{990000000 + index}.db"
        mmap = MmapedDict(str(path))
        try:
            for key in keys:
                mmap.write_value(key, 1.0, 0.0)
        finally:
            mmap.close()
        paths.append(str(path))
    return paths


def _count_validation(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    calls: Counter[str] = Counter()
    original = metrics._valid_metric_key

    def validate(key: str) -> bool:
        calls[key] += 1
        return original(key)

    monkeypatch.setattr(metrics, "_valid_metric_key", validate)
    return calls


def test_duplicate_raw_keys_validate_once_per_preflight(tmp_path, monkeypatch):
    keys = [_key("weather_probe"), _key("weather_other")]
    paths = _files(tmp_path, keys, count=3)
    calls = _count_validation(monkeypatch)
    assert set(metrics._readable_multiprocess_files(str(tmp_path))) == set(paths)
    assert calls == Counter(dict.fromkeys(keys, 1))
    assert set(metrics._readable_multiprocess_files(str(tmp_path))) == set(paths)
    assert calls == Counter(dict.fromkeys(keys, 2))


@pytest.mark.parametrize("entries,characters", [(0, 100000), (1, 100000), (100, 0)])
def test_full_memo_keeps_original_validation_for_every_new_key(
    tmp_path, monkeypatch, entries, characters,
):
    first, second = _key("first"), _key("second")
    paths = _files(tmp_path, [first, second])
    monkeypatch.setattr(metrics, "_VALIDATED_KEYS_MAX_ENTRIES", entries)
    monkeypatch.setattr(metrics, "_VALIDATED_KEYS_MAX_CHARS", characters)
    calls = _count_validation(monkeypatch)
    assert set(metrics._readable_multiprocess_files(str(tmp_path))) == set(paths)
    assert calls[first] == (1 if entries and characters else 2)
    assert calls[second] == 2


@pytest.mark.parametrize("extra,expected_calls", [(-1, 2), (0, 1)])
def test_character_budget_boundary_does_not_skip_validation(
    tmp_path, monkeypatch, extra, expected_calls,
):
    key = _key("unicode_한글")
    paths = _files(tmp_path, [key])
    monkeypatch.setattr(metrics, "_VALIDATED_KEYS_MAX_CHARS", len(key) + extra)
    calls = _count_validation(monkeypatch)
    assert set(metrics._readable_multiprocess_files(str(tmp_path))) == set(paths)
    assert calls[key] == expected_calls


def test_late_invalid_key_still_isolates_only_bad_file(tmp_path, monkeypatch):
    key = _key("valid")
    paths = _files(tmp_path, [key])
    bad = MmapedDict(paths[1])
    bad.write_value("null", 1.0, 0.0)
    bad.close()

    def no_writer(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(metrics.os, "kill", no_writer)
    calls = _count_validation(monkeypatch)
    assert metrics._readable_multiprocess_files(str(tmp_path)) == [paths[0]]
    assert calls == Counter({key: 1, "null": 1})
    assert not Path(paths[1]).exists()


def test_next_preflight_checks_changed_key_with_same_inode_size_and_mtime(tmp_path, monkeypatch):
    valid = '["a", "a_total", {}, "h"]'
    invalid = valid.replace("{}", "[]")
    path = Path(_files(tmp_path, [valid], count=1)[0])
    before = path.stat()
    calls = _count_validation(monkeypatch)
    assert metrics._readable_multiprocess_files(str(tmp_path)) == [str(path)]
    raw = path.read_bytes()
    assert raw.count(valid.encode()) == 1
    path.write_bytes(raw.replace(valid.encode(), invalid.encode()))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_ino, before.st_size, before.st_mtime_ns,
    )

    def no_writer(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(metrics.os, "kill", no_writer)
    assert metrics._readable_multiprocess_files(str(tmp_path)) == []
    assert calls == Counter({valid: 1, invalid: 1})
    assert not path.exists()


@pytest.mark.parametrize("memo_entries", [0, 4096])
def test_real_mmap_and_upstream_merge_output_is_identical(tmp_path, monkeypatch, memo_entries):
    _files(tmp_path, [_key("weather_probe"), _key("weather_other")], count=3)
    monkeypatch.setattr(metrics, "_VALIDATED_KEYS_MAX_ENTRIES", memo_entries)
    expected, actual = CollectorRegistry(), CollectorRegistry()
    MultiProcessCollector(expected, path=str(tmp_path))
    metrics._CleaningMultiProcessCollector(actual, path=str(tmp_path))
    assert generate_latest(actual) == generate_latest(expected)
