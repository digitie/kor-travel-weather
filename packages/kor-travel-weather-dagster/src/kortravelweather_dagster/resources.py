"""Dagster resources for direct python-kma-api clients and repository access."""

from __future__ import annotations

from typing import Any

from dagster import ConfigurableResource

from kortravelweather.providers import create_configured_provider
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings


class KmaClientResource(ConfigurableResource):
    """Construct the official clients at execution time, never at import time."""

    def create_client(self, *, settings: WeatherSettings | None = None) -> Any:
        from kma import KmaClient

        runtime = settings or WeatherSettings()
        configured_key = (
            runtime.data_go_kr_service_key.get_secret_value()
            if runtime.data_go_kr_service_key
            else ""
        )
        key = configured_key
        if not key:
            raise RuntimeError("KOR_TRAVEL_WEATHER_DATA_GO_KR_SERVICE_KEY가 필요합니다.")
        return KmaClient(
            key,
            timeout=runtime.provider_http_timeout_seconds,
            retries=runtime.provider_retries,
        )

    def create_data_client(self, *, settings: WeatherSettings | None = None) -> Any:
        from kma import DataGoKrClient

        runtime = settings or WeatherSettings()
        configured_key = (
            runtime.data_go_kr_service_key.get_secret_value()
            if runtime.data_go_kr_service_key
            else ""
        )
        key = configured_key
        if not key:
            raise RuntimeError("KOR_TRAVEL_WEATHER_DATA_GO_KR_SERVICE_KEY가 필요합니다.")
        return DataGoKrClient(
            key,
            timeout=runtime.provider_http_timeout_seconds,
            retries=runtime.provider_retries,
        )


class WeatherRepositoryResource(ConfigurableResource):
    database_url: str = ""

    def create_repository(self) -> WeatherRepository:
        settings = (
            WeatherSettings(database_url=self.database_url)
            if self.database_url
            else WeatherSettings()
        )
        repository = WeatherRepository(settings.database_url)
        if not settings.is_production:
            repository.create_schema()
        return repository


class ExternalWeatherProviderResource(ConfigurableResource):
    """환경 설정으로 생성되는 Open-Meteo/유료 provider resource."""

    provider_key: str = "open_meteo"
    dataset_key: str = "open_meteo_current"

    def create_provider(self, *, settings: WeatherSettings | None = None) -> Any:
        return create_configured_provider(self.provider_key, settings=settings)
