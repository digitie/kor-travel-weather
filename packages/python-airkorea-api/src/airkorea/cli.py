"""airkorea 명령줄 진입점."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any, cast

from pydantic import BaseModel

from airkorea.client import AirKoreaClient


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="airkorea")
    parser.add_argument(
        "--service-key",
        help="AirKorea decoded service key. Defaults to DATA_GO_KR_SERVICE_KEY.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    station_parser = subparsers.add_parser("station", help="station measurements")
    station_parser.add_argument("--station-name", required=True)
    station_parser.add_argument("--data-term", default="DAILY")
    station_parser.add_argument("--num-of-rows", type=int, default=1)

    sido_parser = subparsers.add_parser("sido", help="measurements by 시도")
    sido_parser.add_argument("--sido-name", required=True)
    sido_parser.add_argument("--num-of-rows", type=int, default=100)

    stations_parser = subparsers.add_parser("stations", help="station metadata")
    stations_parser.add_argument("--addr")
    stations_parser.add_argument("--station-name")
    stations_parser.add_argument("--num-of-rows", type=int, default=100)

    nearby_parser = subparsers.add_parser("nearby", help="nearby stations")
    _add_location_args(nearby_parser)

    forecast_parser = subparsers.add_parser("forecast", help="forecast notices")
    forecast_parser.add_argument("--search-date")
    forecast_parser.add_argument("--inform-code")

    args = parser.parse_args(argv)
    with (
        AirKoreaClient(service_key=args.service_key)
        if args.service_key
        else AirKoreaClient()
    ) as client:
        result: Any
        if args.command == "station":
            result = client.station_measurements(
                args.station_name,
                data_term=args.data_term,
                num_of_rows=args.num_of_rows,
            )
        elif args.command == "sido":
            result = client.sido_measurements(args.sido_name, num_of_rows=args.num_of_rows)
        elif args.command == "stations":
            result = client.stations(
                addr=args.addr,
                station_name=args.station_name,
                num_of_rows=args.num_of_rows,
            )
        elif args.command == "nearby":
            location = _location_kwargs(args)
            if "lat" in location:
                result = client.nearby_stations(lat=location["lat"], lon=location["lon"])
            else:
                result = client.nearby_stations(tm_x=location["tm_x"], tm_y=location["tm_y"])
        else:
            result = client.forecast_notices(
                search_date=args.search_date,
                inform_code=args.inform_code,
            )

    print(json.dumps(_jsonable(result), ensure_ascii=False, indent=2))
    return 0


def _add_location_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--lat", type=float)
    group.add_argument("--tm-x", type=float)
    parser.add_argument("--lon", type=float)
    parser.add_argument("--tm-y", type=float)


def _location_kwargs(args: argparse.Namespace) -> dict[str, float]:
    if args.lat is not None:
        if args.lon is None:
            raise SystemExit("--lon is required with --lat")
        return {"lat": args.lat, "lon": args.lon}
    if args.tm_y is None:
        raise SystemExit("--tm-y is required with --tm-x")
    return {"tm_x": args.tm_x, "tm_y": args.tm_y}


def _jsonable(value: Any) -> Any:
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump())
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(cast(Any, value)))
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


if __name__ == "__main__":
    raise SystemExit(main())
