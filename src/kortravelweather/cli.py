"""`ktwctl` 개발/운영 보조 명령."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from .models import WeatherLocation
from .repository import repository_from_settings
from .settings import get_settings


def _init_db() -> int:
    settings = get_settings()
    if not settings.database_url.startswith("sqlite:"):
        raise RuntimeError(
            "ktwctl init-db는 SQLite 개발 fixture 전용입니다. "
            "PostgreSQL 운영 DB에는 alembic upgrade head를 사용하세요."
        )
    repository = repository_from_settings(settings)
    repository.create_schema()
    for raw in settings.targets:
        provider_fields = {
            "mid_region_code",
            "mid_land_region_code",
            "mid_temperature_region_code",
            "mid_land_reg_id",
            "mid_ta_reg_id",
        }
        provider_values = {
            key: raw[key] for key in provider_fields if raw.get(key) is not None
        }
        payload = {key: value for key, value in raw.items() if key not in provider_fields}
        if provider_values:
            metadata = dict(payload.get("metadata") or {})
            metadata.update(provider_values)
            payload["metadata"] = metadata
        location = WeatherLocation.model_validate(payload)
        # Bootstrap is intentionally insert-only. Existing rows are owned by
        # the admin catalog; rerunning init-db must not resurrect disabled
        # anchors or overwrite coordinates/metadata after facts exist.
        if repository.get_location(location.location_id) is not None:
            continue
        try:
            repository.create_location(location)
        except ValueError:
            # Another bootstrap worker won the insert race.
            continue
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
