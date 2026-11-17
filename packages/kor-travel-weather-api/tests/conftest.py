from __future__ import annotations

import os

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

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)


@pytest.fixture
def api_client() -> TestClient:
    settings = WeatherSettings(environment="development", database_url=TEST_DATABASE_URL)
    repository = WeatherRepository(settings.database_url)
    return TestClient(create_app(settings, repository))


@pytest.fixture
def filesystem_exports(monkeypatch):
    """파일 guard/GC/손상 계약 시험만 명시적으로 legacy store를 주입한다.

    production factory는 항상 DB다. 파일을 생성/훼손하는 시험이 우연히 DB를 읽지 않게 한다.
    """
    from kortravelweather_api.routers import service_exports

    from kortravelweather import export_collector, export_gc
    from kortravelweather.export_artifacts import ExportArtifactStore

    def explicit_file_store(repository, artifact_root):
        return ExportArtifactStore(artifact_root)

    monkeypatch.setattr(export_collector, "export_artifact_store", explicit_file_store)
    monkeypatch.setattr(service_exports, "export_artifact_store", explicit_file_store)
    monkeypatch.setattr(
        export_gc, "collect_export_artifacts", export_gc.collect_legacy_export_artifacts,
    )


@pytest.fixture
def repository(weather_test_database_url):
    """API 공용 DB fixture. 루트 격리 fixture와 같은 시험 DB만 사용한다."""
    instance = WeatherRepository(weather_test_database_url)
    instance.create_schema()
    try:
        yield instance
    finally:
        instance.engine.dispose()
