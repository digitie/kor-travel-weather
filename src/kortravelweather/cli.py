"""`ktwctl` 개발/운영 보조 명령."""

from __future__ import annotations

import argparse
import json
from typing import Sequence

from .models import WeatherLocation
from .repository import repository_from_settings
from .settings import get_settings


def _init_db() -> int:
    settings = get_settings()
    repository = repository_from_settings(settings)
    repository.create_schema()
    for raw in settings.targets:
        location = WeatherLocation.model_validate(raw)
        repository.upsert_location(location)
    print(json.dumps({"database": settings.database_url, "targets_loaded": len(settings.targets)}))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ktwctl")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="schema를 생성하고 env target을 등록")
    args = parser.parse_args(argv)
    if args.command == "init-db":
        return _init_db()
    parser.error(f"알 수 없는 명령: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
