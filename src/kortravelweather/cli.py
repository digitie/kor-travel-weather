"""`ktwctl` 개발/운영 보조 명령."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime

from .models import WeatherLocation, kst_now
from .repository import repository_from_settings
from .retention import run_short_forecast_retention, short_forecast_cutoff
from .settings import get_settings


def _init_db() -> int:
    settings = get_settings()
    if not settings.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError(
            "ktwctl init-db는 PostgreSQL 전용입니다. "
            "운영 DB는 alembic upgrade head를 사용하세요."
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


def _purge_short_forecast(args: argparse.Namespace) -> int:
    """기본은 읽기이며 실행한 batch별 intent/commit 숫자만 기록한다."""
    latest = short_forecast_cutoff(kst_now())
    cutoff = datetime.fromisoformat(args.cutoff) if args.cutoff else latest
    if cutoff.tzinfo is None or cutoff.utcoffset() is None or cutoff > latest:
        raise ValueError("cutoff는 timezone-aware이며 현재시각-6시간 이하여야 합니다.")
    repository = repository_from_settings(get_settings())
    try:
        report = run_short_forecast_retention(
            repository, cutoff=cutoff, dry_run=not args.execute,
            batch_size=args.batch_size, max_batches=args.max_batches,
            budget_seconds=args.budget_seconds,
            on_progress=lambda value: print(json.dumps(value), flush=True),
        )
    finally:
        repository.engine.dispose()
    print(json.dumps({"phase": "SHORT_FORECAST_RETENTION_RESULT", **report}), flush=True)
    return 0 if report["status"] in {"dry_run", "complete_snapshot"} else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ktwctl")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="schema를 생성하고 env target을 등록")
    purge = subparsers.add_parser(
        "purge-short-forecast", help="KMA 단기예보 valid_at의 지난6시간 밖을 유한 정리",
    )
    purge.add_argument("--execute", action="store_true", help="기본 dry-run 대신 실제 삭제")
    purge.add_argument("--cutoff", help="dry-run과 실행이 공유할 timezone-aware 고정 경계")
    purge.add_argument("--batch-size", type=int, default=1000)
    purge.add_argument("--max-batches", type=int, default=100)
    purge.add_argument("--budget-seconds", type=float, default=60)
    args = parser.parse_args(argv)
    if args.command == "purge-short-forecast":
        try:
            return _purge_short_forecast(args)
        except Exception as exc:
            print(json.dumps({
                "phase": "SHORT_FORECAST_RETENTION_FAILED",
                "error_class": type(exc).__name__,
            }), flush=True)
            return 1
    if args.command == "init-db":
        return _init_db()
    parser.error(f"알 수 없는 명령: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
