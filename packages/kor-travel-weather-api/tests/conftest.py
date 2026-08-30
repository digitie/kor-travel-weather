from __future__ import annotations

import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "src"))

from fastapi.testclient import TestClient

from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings
from kortravelweather_api.app import create_app


@pytest.fixture
def api_client(tmp_path: Path) -> TestClient:
    settings = WeatherSettings(database_url=f"sqlite:///{tmp_path / 'weather.db'}")
    repository = WeatherRepository(settings.database_url)
    return TestClient(create_app(settings, repository))
