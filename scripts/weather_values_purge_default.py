"""DESTRUCTIVE, owner decision: drop the old rows of the DEFAULT partition.

Dry run is the default and changes nothing; it reports DEFAULT's size, the
retention eligibility, and the foreign-key impact on weather_current_values
(rows pointing below the floor, which must be deleted first, and how many
locations would be left without a current value).  ``--exact`` adds a full
count and the known_at range -- a sequential read of the whole DEFAULT.

``--execute`` is refused unless a validated floor exists
(weather_values_forward_partitions.py) and the floor is at or before the
retention cutoff, i.e. every row in DEFAULT is already past retention.  It then
deletes the pointers below the floor, detaches and drops DEFAULT and creates a
new empty one with the same floor, in one DDL transaction whose locks are taken
up front with a 500 ms timeout and retried.  Every writer waits for that
transaction: DETACH holds ACCESS EXCLUSIVE on weather_values while PostgreSQL
checks that no weather_current_values row references the detached rows (one
read of the projection), and DROP unlinks the 36 GB at commit.  Run it at a
quiet hour.

    docker exec -i kor-travel-weather-dagster-code-server-latest \
        python - [--exact] [--execute] < scripts/weather_values_purge_default.py
"""

from __future__ import annotations

import argparse
import sys

from kortravelweather.default_partition import purge_execute, purge_report
from kortravelweather.models import kst_now
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings


def main(argv: list[str]) -> int:
    settings = WeatherSettings()
    parser = argparse.ArgumentParser(prog="weather_values_purge_default")
    parser.add_argument("--retention-days", type=int, default=settings.retention_days)
    parser.add_argument("--exact", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)

    def log(message: str) -> None:
        print(f"[{kst_now():%H:%M:%S}] {message}", flush=True)

    engine = WeatherRepository(settings.database_url).engine
    if not args.execute:
        log("DRY RUN -- nothing will be changed")
        purge_report(engine, retention_days=args.retention_days, log=log, exact=args.exact)
        return 0
    purge_execute(engine, retention_days=args.retention_days, log=log)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
