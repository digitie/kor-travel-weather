from __future__ import annotations

import os
from pathlib import Path

# The application intentionally fails closed when no production admin token is
# configured.  Test collection imports the module-level ASGI app before the
# fixture can construct development settings, so opt the test process into the
# explicit local profile first.
os.environ.setdefault("KOR_TRAVEL_WEATHER_ENV", "development")

import pytest
from fastapi.testclient import TestClient  # noqa: E402
from kortravelweather_api.app import create_app  # noqa: E402

from kortravelweather.repository import WeatherRepository  # noqa: E402
from kortravelweather.settings import WeatherSettings  # noqa: E402


@pytest.fixture
def api_client(tmp_path: Path) -> TestClient:
    settings = WeatherSettings(
        environment="development", database_url=f"sqlite:///{tmp_path / 'weather.db'}"
    )
    repository = WeatherRepository(settings.database_url)
    return TestClient(create_app(settings, repository))
