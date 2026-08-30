"""Export the checked-in public OpenAPI document."""

from __future__ import annotations

import json
import os
from pathlib import Path

# OpenAPI generation is a documentation build, not a production server
# startup.  Force the explicit local profile before importing the module-level
# ASGI app so a clean checkout can export the contract without a live admin
# secret (production deployments still fail closed in ``create_app``).
os.environ["KOR_TRAVEL_WEATHER_ENV"] = "development"

from kortravelweather_api.app import app


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "openapi.json"
    target.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(target)


if __name__ == "__main__":
    main()
