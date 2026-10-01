"""Destructive (owner-approved 2026-10-01): remove what is past retention from DEFAULT.

Dry run is the default and changes nothing.  It prints DEFAULT's size and row
estimate, the retention cutoff (``KOR_TRAVEL_WEATHER_RETENTION_DAYS``, 16 in
production; ``--retention-days`` overrides it), how many rows are before the
cutoff (planner statistics; ``--exact`` counts them, a full read), and the
foreign-key impact on ``weather_current_values``.

``--execute`` picks the strategy from DEFAULT's floor (added by
``weather_values_forward_partitions.py``, which must run first):

* every row past retention (floor <= cutoff): one short DDL transaction, locks
  taken up front (weather_values then weather_current_values, ACCESS
  EXCLUSIVE) with a statement timeout: drop the projection's foreign key,
  detach and drop DEFAULT, create a new empty one with the same floor, re-add
  the foreign key NOT VALID -- catalog changes only, so writers wait
  milliseconds.  The foreign key is then validated under SHARE UPDATE EXCLUSIVE
  (one read of the 3.6M-row projection; writers carry on).  All the space comes
  back at once; no dead tuples, no vacuum.
* some rows still inside retention: delete only the expired ones, walking the
  heap 2,048 pages (16 MB) per short transaction with ROW EXCLUSIVE -- readers
  and writers carry on -- then a throttled VACUUM (SHARE UPDATE EXCLUSIVE,
  writers carry on).  Refused when statistics show nothing expired.  The files
  keep their size until the swap above.

In both cases projection rows pointing before the cutoff are deleted first by
the same page walk: the foreign key is ON DELETE RESTRICT.

Idempotent and resumable: progress lines print ``--start-block`` to resume a
walk; a range already cleaned deletes nothing.

    docker exec -i kor-travel-weather-dagster-code-server-latest \\
        python - [--exact] < scripts/weather_values_purge_default.py
    docker exec -i kor-travel-weather-dagster-code-server-latest \\
        python - --execute [--start-block N] < scripts/weather_values_purge_default.py
"""

from __future__ import annotations

import argparse
import signal
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
    parser.add_argument("--start-block", type=int, default=0)
    parser.add_argument("--batch-blocks", type=int, default=2048)
    parser.add_argument("--pause", type=float, default=0.2)
    parser.add_argument("--no-vacuum", action="store_true")
    args = parser.parse_args(argv)

    def log(message: str) -> None:
        print(f"[{kst_now():%H:%M:%S}] {message}", flush=True)

    engine = WeatherRepository(settings.database_url).engine
    if not args.execute:
        log("DRY RUN -- nothing will be changed")
        purge_report(engine, retention_days=args.retention_days, log=log, exact=args.exact)
        return 0
    purge_execute(
        engine,
        retention_days=args.retention_days,
        log=log,
        vacuum=not args.no_vacuum,
        start_block=args.start_block,
        batch_blocks=args.batch_blocks,
        pause_seconds=args.pause,
    )
    return 0


def _exit_on_sigterm() -> None:
    """Turn SIGTERM into SystemExit so the cleanup in ``except BaseException`` runs.

    ``docker restart``/redeploy of the code-server sends SIGTERM, which
    otherwise kills Python without unwinding: an unvalidated floor would be
    left behind mid-VALIDATE instead of being removed.
    """
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))


if __name__ == "__main__":
    _exit_on_sigterm()
    sys.exit(main(sys.argv[1:]))
