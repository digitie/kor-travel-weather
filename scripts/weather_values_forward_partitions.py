"""One-time, non-destructive: give the DEFAULT partition a floor and create
the dated partitions from it forward.

Production's fresh database (2026-09-20) never had a dated partition, so every
fact went to ``weather_values_default`` (36 GB by 2026-10-01).  Until a
validated floor (``CHECK (known_at < F)``) proves DEFAULT holds nothing from F
on, creating any partition scans all of DEFAULT under an ACCESS EXCLUSIVE lock;
the nightly job therefore never could.  This script adds the floor, validates
it while writers keep running, then creates F .. today+ahead.  After it, the
nightly maintenance job extends the window on its own.

Run inside the deployed code-server (it uses the deployed library and DSN)::

    docker exec -i kor-travel-weather-dagster-code-server-latest \
        python - --dry-run < scripts/weather_values_forward_partitions.py
    docker exec -i kor-travel-weather-dagster-code-server-latest \
        python - < scripts/weather_values_forward_partitions.py

Options: ``--floor-day YYYY-MM-DD`` (default: tomorrow, KST), ``--ahead N``
(default: KOR_TRAVEL_WEATHER_RETENTION_AHEAD_DAYS), ``--force`` (start even
within 3 h of the floor), ``--dry-run``.  Idempotent; safe to re-run.

Today's partition cannot be created: DEFAULT already holds today's rows, and
moving them is exactly the scan this avoids.  Today's rows stay in DEFAULT and
are removed with it by ``weather_values_purge_default.py`` once past retention.

Run it off-peak (the VALIDATE reads all of DEFAULT) and at least 3 hours before
the floor -- the default floor is the next KST midnight, so early morning KST
gives the most room.  It holds the retention job's advisory lock while it runs.

Steps and locks (see ``kortravelweather.default_partition.establish_floor``):

1. ADD CONSTRAINT ... NOT VALID: ACCESS EXCLUSIVE on DEFAULT, milliseconds.
2. One transaction: VALIDATE (SHARE UPDATE EXCLUSIVE, inserts continue; one
   sequential read of the 21 GB heap, tens of minutes), then -- in a savepoint
   retried until it gets its locks -- ACCESS EXCLUSIVE on weather_values and
   SHARE ROW EXCLUSIVE on weather_current_values and one CREATE ... PARTITION OF
   per day, milliseconds each because the floor proves DEFAULT empty for them.

A validated floor therefore always comes with its partitions.  Every lock wait
is 3 x deadlock_timeout, so an autovacuum in the way is cancelled rather than
outwaited; any failure removes the unvalidated floor, and a rerun (or the
nightly job) removes one an interrupted run left behind.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from kortravelweather.default_partition import describe, establish_floor
from kortravelweather.models import kst_now
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings


def main(argv: list[str]) -> int:
    settings = WeatherSettings()
    parser = argparse.ArgumentParser(prog="weather_values_forward_partitions")
    parser.add_argument("--floor-day", type=date.fromisoformat)
    parser.add_argument("--ahead", type=int, default=settings.retention_ahead_days)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    floor_day = args.floor_day or kst_now().date() + timedelta(days=1)

    def log(message: str) -> None:
        print(f"[{kst_now():%H:%M:%S}] {message}", flush=True)

    engine = WeatherRepository(settings.database_url).engine
    describe(engine, log)
    establish_floor(
        engine,
        floor_day=floor_day,
        ahead_days=args.ahead,
        log=log,
        dry_run=args.dry_run,
        force=args.force,
    )
    describe(engine, log)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
