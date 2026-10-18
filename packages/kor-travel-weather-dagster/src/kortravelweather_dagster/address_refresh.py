"""6개월 주소 갱신과 수동 배치 재개."""

from types import SimpleNamespace

from kortravelcommon.address_dagster import address_refresh_definitions
from kortravelcommon.reverse_address import address_engine

from kortravelweather.address_refresh import address_source, configured, run_address_refresh
from kortravelweather.settings import get_settings


def _run(force):
    settings = get_settings()
    repository = SimpleNamespace(
        engine=address_engine(
            settings.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        )
    )
    try:
        return run_address_refresh(repository, settings, force=force)
    finally:
        repository.engine.dispose()


def _pending():
    settings = get_settings()
    if not configured(settings):
        return False
    repository = SimpleNamespace(
        engine=address_engine(
            settings.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        )
    )
    try:
        return address_source(repository).has_work(automatic=settings.address_refresh_auto_enabled)
    finally:
        repository.engine.dispose()


address_refresh_job, address_refresh_sensor = address_refresh_definitions(
    project="weather",
    location_name="kortravelweather_dagster.definitions",
    run=_run,
    pending=_pending,
)
