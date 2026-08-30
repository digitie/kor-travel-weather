"""Export the checked-in public OpenAPI document."""

from __future__ import annotations

import json
from pathlib import Path

from kortravelweather_api.app import app


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "openapi.json"
    target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
