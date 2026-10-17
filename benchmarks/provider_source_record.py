"""소유 synthetic payload의 source-record 작업량·CPU·Python peak 비교. 외부 I/O 없음."""
from __future__ import annotations

import argparse
import ast
import copy
import gc
import hashlib
import json
import re
import statistics
import sys
import time
import tracemalloc
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

FUNCTIONS = {
    "_normalized_key", "redact_secrets", "_canonical_json",
    "source_record_key", "_source_record_key_from_redacted_payload", "make_source_record",
}
STAMP = datetime(2026, 10, 17, 3, tzinfo=UTC)


def load_functions(path: Path, *, prototype: bool = False) -> dict[str, Any]:
    text = path.read_text()
    selected = []
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            selected.append(node)
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_SECRET_KEYS"
            for target in node.targets
        ):
            selected.append(node)
    namespace: dict[str, Any] = {
        "Mapping": Mapping, "Any": Any, "hashlib": hashlib, "json": json, "re": re,
        "datetime": datetime, "kst_now": lambda: STAMP,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])),
                 str(path), "exec"), namespace)
    if prototype:
        # 제품 구현 전 같은 helper 경계의 후보만 메모리에서 비교한다.
        def safe_key(provider, dataset_key, location_id, payload):
            canonical = namespace["_canonical_json"]([provider, dataset_key, location_id, payload])
            return "sr_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:48]
        namespace["source_record_key"] = safe_key
    return namespace


def fixtures() -> dict[str, dict[str, Any]]:
    times = [(STAMP + timedelta(hours=i)).isoformat() for i in range(336)]
    hourly: dict[str, Any] = {"time": times}
    for name in (
        "temperature_2m", "relative_humidity_2m", "apparent_temperature",
        "precipitation_probability", "precipitation", "wind_speed_10m",
        "wind_direction_10m", "weather_code",
    ):
        hourly[name] = [round(10 + (i % 31) / 10, 1) for i in range(336)]
    return {
        "open_meteo_hourly336": {
            "latitude": 37.5, "longitude": 127.0, "timezone": "UTC",
            "hourly": hourly,
        },
        "raw6000": {"items": [
            {"station": str(i % 100), "time": times[i % 336], "temperature": 18.5,
             "humidity": 55, "wind": {"speed": 2.0, "direction": 180},
             "weather": "맑음", "apiKey": "synthetic-credential",
             "url": "https://fixture.invalid?api_key=synthetic&area=서울"}
            for i in range(6000)
        ]},
    }


def measure(function, kwargs: dict[str, Any], *, calls: int) -> dict[str, Any]:
    gc.collect()
    started = time.process_time()
    for _ in range(calls):
        function(**kwargs)
    cpu = (time.process_time() - started) / calls
    gc.collect()
    tracemalloc.start()
    function(**kwargs)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"cpu_seconds_per_call": cpu, "python_peak_bytes": peak}


def run(baseline: Path, candidate: Path, *, prototype: bool, calls: int) -> dict[str, Any]:
    old = load_functions(baseline)["make_source_record"]
    new = load_functions(candidate, prototype=prototype)["make_source_record"]
    results = {}
    for name, payload in fixtures().items():
        kwargs = dict(provider="fixture", dataset_key=name, location_id="fixture-seoul",
                      payload=payload, endpoint="https://fixture.invalid?token=synthetic",
                      request_params={"apiKey": "synthetic", "name": "서울"},
                      status_code=200, fetched_at=STAMP)
        untouched = copy.deepcopy(kwargs)
        assert old(**kwargs) == new(**kwargs)
        assert kwargs == untouched
        samples = {"old": [], "new": []}
        # 같은 입력·호출수로 ABBA를 3회, interpreter cache warmup도 대칭으로 수행한다.
        old(**kwargs)
        new(**kwargs)
        for _ in range(3):
            for label, function in (("old", old), ("new", new), ("new", new), ("old", old)):
                samples[label].append(measure(function, kwargs, calls=calls))
        assert kwargs == untouched
        results[name] = {
            "payload_json_bytes": len(json.dumps(payload, ensure_ascii=False).encode()),
            "calls_per_cpu_sample": calls, "paired_outputs_identical": True,
            "input_unchanged": True, "samples": samples,
            "median": {label: {
                key: statistics.median(row[key] for row in rows)
                for key in ("cpu_seconds_per_call", "python_peak_bytes")
            } for label, rows in samples.items()},
        }
    return {
        "scope": "synthetic source-record only; no provider/DB/SDK/network",
        "python": sys.version,
        "baseline_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "prototype": prototype,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--prototype", action="store_true")
    parser.add_argument("--calls", type=int, default=3)
    args = parser.parse_args()
    if args.calls < 1 or args.calls > 100:
        parser.error("calls는 1..100이어야 합니다.")
    print(json.dumps(run(args.baseline, args.candidate,
                         prototype=args.prototype, calls=args.calls), indent=2))


if __name__ == "__main__":
    main()
