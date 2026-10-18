"""운영/API 접근 없이 1,450행 DB→위치 DTO→수집 target 비용을 측정한다.

PYTHONPATH=src python tools/benchmark_location_catalog.py
legacy/candidate를 별도 프로세스에서 반복 호출한다. SQLite 결과는 운영 PG 실측이 아니다.
"""

import argparse
import gc
import hashlib
import json
import resource
import time
import tracemalloc
from datetime import UTC, datetime

from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import sessionmaker

from kortravelweather.providers.base import ProviderLocation
from kortravelweather.repository import WeatherLocationRow, WeatherRepository

parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=("legacy", "candidate"), required=True)
args = parser.parse_args()
engine = create_engine("sqlite://")
WeatherLocationRow.__table__.create(engine)
timestamp = datetime.now(UTC)
with engine.begin() as connection:
    connection.execute(
        insert(WeatherLocationRow),
        [
            dict(
                location_id=f"station-{i:04d}",
                name=f"Station {i:04d}",
                latitude=37,
                longitude=127,
                enabled=True,
                created_at=timestamp,
                updated_at=timestamp,
                metadata={
                    "measurement_point": {"id": i, "source": "fixture"},
                    "annotation": "측정소" * 80,
                },
            )
            for i in range(1450)
        ],
    )
repo = object.__new__(WeatherRepository)
repo._session_factory = sessionmaker(engine)
gc.collect()
tracemalloc.start()
cpu = time.process_time_ns()
wall = time.perf_counter_ns()
if args.mode == "legacy":
    with repo._session_factory() as session:
        statement = (
            select(WeatherLocationRow)
            .where(WeatherLocationRow.enabled.is_(True))
            .order_by(WeatherLocationRow.name, WeatherLocationRow.location_id)
        )
        locations = [repo._location_model(row) for row in session.scalars(statement).all()]
else:
    locations = repo.list_locations(enabled_only=True, limit=None)
targets = [
    ProviderLocation(
        location_id=row.location_id,
        latitude=row.latitude,
        longitude=row.longitude,
        metadata=row.metadata,
    )
    for row in locations
    if isinstance(row.metadata.get("measurement_point"), dict)
]
elapsed_cpu = time.process_time_ns() - cpu
elapsed_wall = time.perf_counter_ns() - wall
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
digest = hashlib.sha256(
    json.dumps([row.model_dump() for row in locations], sort_keys=True).encode()
).hexdigest()
print(
    json.dumps(
        dict(
            mode=args.mode,
            rows=len(locations),
            targets=len(targets),
            cpu_ms=elapsed_cpu / 1e6,
            wall_ms=elapsed_wall / 1e6,
            peak_bytes=peak,
            max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            output_sha256=digest,
        )
    )
)
engine.dispose()
