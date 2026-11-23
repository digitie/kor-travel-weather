"""운영 규모 current export 순회의 페이지당 SQL 문장 수·시간을 잰다.

로컬/CI PostgreSQL 전용 측정 도구다. 운영 DB·API에 쓰지 않는다.

    # 1) 시험 DB에 운영 규모(기본 grid 2,925·위치 3,434 → point 27,472) 승인 세대를 만든다.
    uv run python benchmarks/export_current_pages.py seed
    # 2) Map과 같은 순서로 첫 페이지 + 모든 cursor 페이지를 읽고 표를 낸다.
    uv run python benchmarks/export_current_pages.py walk --limit 2000 --repeat 3

DB는 ``KOR_TRAVEL_WEATHER_TEST_DATABASE_URL``(없으면 ``KOR_TRAVEL_WEATHER_DATABASE_URL``)이다.
seed는 그 DB의 weather 표를 비운다. 실제 수집 경로(``run_weather_sync`` + mock KMA HTTP)와
실제 publisher로 세대를 만들므로 inventory·plan·head·owner·attempt·run 행이 운영과 같은 모양이다.
운영은 6개 provider(KMA 1,078·AirKorea 644·산림 518·OWM 350·KREX 185·WeatherAPI 150
scope)이고 이 fixture는 KMA 격자만 쓴다. scope·point 개수와 SQL 문장 구조를 맞춘 것이며
odroid의 디스크·CPU 경합은 재현하지 않는다. 시간은 같은 호스트의 전후 비교에만 쓴다.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in ("src", "packages/kor-travel-weather-api/src",
              "packages/kor-travel-weather-dagster/src"):
    sys.path.insert(0, str(ROOT / extra))

TOKEN = "benchmark-service-token"
# 운영 odroid에서 5초 statement 상한에 걸린 큰 표(시도 1.06M·plan 1.06M·세대 0.70M 행)와
# 승인 inventory·소유자 조회. 페이지당 몇 번 닿는지를 따로 센다.
HEAVY_TABLES = ("weather_export_attempts", "weather_export_generations",
                "weather_export_scope_plans", "weather_export_scope_owners",
                "weather_export_approved_locations")
METRICS = [("T1H", "18.4"), ("REH", "50"), ("RN1", "0"), ("UUU", "0"),
           ("VVV", "0"), ("PTY", "0"), ("VEC", "120"), ("WSD", "1")]


def _database_url() -> str:
    url = os.environ.get("KOR_TRAVEL_WEATHER_TEST_DATABASE_URL") or os.environ.get(
        "KOR_TRAVEL_WEATHER_DATABASE_URL"
    )
    if not url:
        raise SystemExit("KOR_TRAVEL_WEATHER_TEST_DATABASE_URL이 필요합니다.")
    return url


def _repository():
    from kortravelweather.repository import WeatherRepository

    repository = WeatherRepository(_database_url())
    repository.create_schema()
    return repository


def _truncate(repository) -> None:
    from sqlalchemy import text

    with repository.engine.begin() as connection:
        tables = connection.scalars(text(
            "SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND ("
            "tablename LIKE 'weather_export_%' OR tablename IN ("
            "'weather_sync_run_sources','weather_values','weather_source_records',"
            "'weather_sync_runs','weather_locations','reverse_address_entries'))"
        )).all()
        quote = connection.dialect.identifier_preparer.quote
        connection.exec_driver_sql(
            "TRUNCATE TABLE " + ", ".join(quote(name) for name in tables)
            + " RESTART IDENTITY CASCADE"
        )


def seed(args) -> None:
    import httpx
    from kma import KmaClient
    from kortravelweather_dagster.kma_weather import (
        KMA_ULTRA_SHORT_NOWCAST,
        WeatherTarget,
        run_weather_sync,
    )

    from kortravelweather.export_collector import publish_pending_exports
    from kortravelweather.models import WeatherLocation

    repository = _repository()
    _truncate(repository)
    clock = datetime.now(UTC) - timedelta(minutes=50)

    class Client(KmaClient):
        async def now(self, **kwargs):
            return await super().now(**kwargs, when=clock)

    def respond(request):
        q = request.url.params
        rows = [dict(baseDate=q["base_date"], baseTime=q["base_time"], nx=int(q["nx"]),
                     ny=int(q["ny"]), category=key, obsrValue=value) for key, value in METRICS]
        return httpx.Response(200, json={"response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
            "body": {"pageNo": 1, "numOfRows": 1000, "totalCount": len(rows),
                     "items": {"item": rows}},
        }})

    # grid마다 위치 1곳, 앞 extra개 grid에는 위치를 하나 더 둔다(운영 point 수에 맞춤).
    grids = [(1 + index % 140, 1 + index // 140) for index in range(args.grids)]
    targets = []
    for index, (nx, ny) in enumerate(grids):
        for copy in range(2 if index < args.extra_locations else 1):
            targets.append(WeatherTarget(WeatherLocation(
                location_id=f"bench-{index:05d}-{copy}", name=f"측정 위치 {index}-{copy}",
                latitude=33 + ny * 0.01, longitude=124 + nx * 0.01, nx=nx, ny=ny,
            )))
    for target in targets:
        repository.create_location(target.location)
    root = Path(args.artifact_root)
    started = time.monotonic()
    for offset in range(0, len(targets), args.batch):
        client = Client("bench-key", session=httpx.AsyncClient(
            transport=httpx.MockTransport(respond)), retries=0, max_rps=100_000)
        result = run_weather_sync(
            repository=repository, client=client, targets=targets[offset:offset + args.batch],
            base_datasets=frozenset({KMA_ULTRA_SHORT_NOWCAST}), export_artifact_root=root,
            max_grids=args.batch, max_targets=args.batch,
        )
        if result["status"] != "success":
            raise SystemExit(f"수집 실패: {result['status']}")
        published = 0
        while True:
            outcome = publish_pending_exports(repository, root, limit=64, budget_seconds=60)
            count = outcome.results.get("PUBLISHED", 0)
            published += count
            if not count:
                break
        batch = targets[offset:offset + args.batch]
        print(f"batch {offset // args.batch + 1}: targets {len(batch)}"
              f" published {published} ({time.monotonic() - started:.0f}s)", flush=True)
    from sqlalchemy import text

    with repository.engine.connect() as connection:
        heads = connection.scalar(text("SELECT count(*) FROM weather_export_heads"))
        connection.exec_driver_sql("ANALYZE")
    print(json.dumps({"grids": args.grids, "locations": len(targets), "heads": heads,
                      "points": len(targets) * len(METRICS)}))


def walk(args) -> None:
    from fastapi.testclient import TestClient
    from kortravelweather_api.app import create_app
    from sqlalchemy import event

    from kortravelweather.settings import WeatherSettings

    repository = _repository()
    results = []
    for attempt in range(args.repeat):
        # 매 회 새 앱(빈 프로세스 캐시)으로 Map run 하나를 흉내 낸다.
        client = TestClient(create_app(WeatherSettings(
            environment="development", admin_token=TOKEN, enabled_providers=["python-kma-api"],
            service_exports_enabled=True, service_export_artifact_root=Path(args.artifact_root),
        ), repository))
        statements = []

        def capture(_c, _cur, sql, *_rest, statements=statements):
            statements.append(sql)

        event.listen(repository.engine, "before_cursor_execute", capture)
        rows = []
        try:
            cursor, first = None, True
            run_started = time.monotonic()
            while first or cursor:
                statements.clear()
                params = {"limit": args.limit, **({"cursor": cursor} if cursor else {})}
                started = time.monotonic()
                response = client.get("/v1/service/exports/current", params=params,
                                      headers={"x-admin-token": TOKEN})
                wall = time.monotonic() - started
                if response.status_code != 200:
                    raise SystemExit(f"{response.status_code}: {response.text[:300]}")
                body = response.json()
                data = [s for s in statements if not s.lstrip().upper().startswith(
                    ("SET ", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"))]
                rows.append({"page": "first" if first else "cursor", "wall": wall,
                             "sql": len(data), "sql_all": len(statements),
                             "heavy": sum(any(t in s for t in HEAVY_TABLES) for s in data),
                             "items": len(body["items"]),
                             "bytes": len(response.content),
                             "status": body["meta"]["source_status"],
                             "proof": response.headers.get("x-export-status-proof", "-")})
                cursor, first = body["meta"]["next_cursor"], False
            run_wall = time.monotonic() - run_started
        finally:
            event.remove(repository.engine, "before_cursor_execute", capture)
        results.append({"run": attempt + 1, "run_wall": run_wall, "pages": rows})
        cursor_rows = [row for row in rows if row["page"] == "cursor"]
        print(json.dumps({
            "run": attempt + 1, "pages": len(rows), "run_wall_s": round(run_wall, 3),
            "first_wall_s": round(rows[0]["wall"], 3), "first_sql": rows[0]["sql"],
            "cursor_wall_p50_s": round(statistics.median(r["wall"] for r in cursor_rows), 3)
            if cursor_rows else None,
            "cursor_wall_max_s": round(max(r["wall"] for r in cursor_rows), 3)
            if cursor_rows else None,
            "cursor_sql_mean": round(statistics.mean(r["sql"] for r in cursor_rows), 1)
            if cursor_rows else None,
            "first_sql_all": rows[0]["sql_all"],
            "cursor_sql_all_mean": round(statistics.mean(r["sql_all"] for r in cursor_rows), 1)
            if cursor_rows else None,
            "run_sql_all": sum(r["sql_all"] for r in rows),
            "first_heavy": rows[0]["heavy"],
            "run_heavy": sum(r["heavy"] for r in rows),
            "items": sum(r["items"] for r in rows),
            "max_page_bytes": max(r["bytes"] for r in rows),
            "statuses": sorted({r["status"] for r in rows}),
            "cursor_proofs": sorted({r["proof"] for r in cursor_rows}),
        }, ensure_ascii=False), flush=True)
    if args.output:
        Path(args.output).write_text(json.dumps(results, ensure_ascii=False, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    seeding = commands.add_parser("seed")
    seeding.add_argument("--grids", type=int, default=2925)
    seeding.add_argument("--extra-locations", type=int, default=509)
    seeding.add_argument("--batch", type=int, default=1000)
    walking = commands.add_parser("walk")
    walking.add_argument("--limit", type=int, default=2000)
    walking.add_argument("--repeat", type=int, default=3)
    walking.add_argument("--output")
    for command in (seeding, walking):
        command.add_argument("--artifact-root", default=str(Path(tempfile.gettempdir())
                                                           / "weather-export-bench"))
    args = parser.parse_args()
    Path(args.artifact_root).mkdir(parents=True, exist_ok=True)
    {"seed": seed, "walk": walk}[args.command](args)


if __name__ == "__main__":
    main()
