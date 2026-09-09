"""Dagster resources for direct python-kma-api clients and repository access."""

from __future__ import annotations

from typing import Any

from dagster import ConfigurableResource

from kortravelweather.providers import create_configured_provider
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings


class KmaClientResource(ConfigurableResource):
    """Construct the official clients at execution time, never at import time."""

    def create_client(
        self,
        *,
        settings: WeatherSettings | None = None,
        repository: WeatherRepository | None = None,
    ) -> Any:
        from kma import KmaClient

        runtime = settings or WeatherSettings()
        database_key = (
            repository.get_provider_credential(
                "python-kma-api", runtime.optional_credential_encryption_key()
            )
            if repository
            else None
        )
        configured_key = runtime.provider_api_key("python-kma-api")
        key = database_key or configured_key or ""
        if not key:
            raise RuntimeError("KOR_TRAVEL_WEATHER_DATA_GO_KR_SERVICE_KEY가 필요합니다.")
        return KmaClient(
            key,
            timeout=runtime.provider_http_timeout_seconds,
            retries=runtime.provider_retries,
        )

    def create_data_client(
        self,
        *,
        settings: WeatherSettings | None = None,
        repository: WeatherRepository | None = None,
    ) -> Any:
        from kma import DataGoKrClient

        runtime = settings or WeatherSettings()
        database_key = (
            repository.get_provider_credential(
                "python-kma-api", runtime.optional_credential_encryption_key()
            )
            if repository
            else None
        )
        configured_key = runtime.provider_api_key("python-kma-api")
        key = database_key or configured_key or ""
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


class AirKoreaResource(ConfigurableResource):
    """Construct the vendored python-airkorea-api client at run time."""

    def create_client(
        self,
        *,
        settings: WeatherSettings | None = None,
        repository: WeatherRepository | None = None,
    ) -> Any:
        from airkorea import AirKoreaClient

        runtime = settings or WeatherSettings()
        database_key = (
            repository.get_provider_credential(
                "python-airkorea-api", runtime.optional_credential_encryption_key()
            )
            if repository
            else None
        )
        key = database_key or runtime.provider_api_key("python-airkorea-api") or ""
        if not key:
            raise RuntimeError("KOR_TRAVEL_WEATHER_AIRKOREA_API_KEY가 필요합니다.")
        return AirKoreaClient(
            service_key=key,
            timeout=runtime.provider_http_timeout_seconds,
            retries=runtime.provider_retries,
        )


def _provider_key(
    provider: str,
    *,
    settings: WeatherSettings | None = None,
    repository: WeatherRepository | None = None,
) -> str:
    """Resolve a provider credential, preferring the admin-managed one.

    An operator can rotate a key in the admin UI without a redeploy, so the
    database value wins over the environment when both are present.
    """
    runtime = settings or WeatherSettings()
    database_key = (
        repository.get_provider_credential(
            provider, runtime.optional_credential_encryption_key()
        )
        if repository
        else None
    )
    key = database_key or runtime.provider_api_key(provider) or ""
    if not key:
        raise RuntimeError(f"{provider} credential이 설정되지 않았습니다.")
    return key


class KhoaResource(ConfigurableResource):
    """국립해양조사원 client. data.go.kr service key를 쓴다."""

    def create_client(
        self,
        *,
        settings: WeatherSettings | None = None,
        repository: WeatherRepository | None = None,
    ) -> Any:
        from khoa import KhoaClient

        runtime = settings or WeatherSettings()
        return KhoaClient(
            service_key=_provider_key(
                "python-khoa-api", settings=runtime, repository=repository
            ),
            timeout=runtime.provider_http_timeout_seconds,
            retries=runtime.provider_retries,
            # The client reads a .env file by default; deployments inject the
            # key through settings, and reading a stray file would make which
            # key is in use depend on the working directory.
            env_file=None,
        )


class KrexResource(ConfigurableResource):
    """한국도로공사 client. data.go.kr이 아니라 data.ex.co.kr key를 쓴다."""

    def create_client(
        self,
        *,
        settings: WeatherSettings | None = None,
        repository: WeatherRepository | None = None,
    ) -> Any:
        from krex import KrexClient

        runtime = settings or WeatherSettings()
        return KrexClient(
            ex_api_key=_provider_key(
                "python-krex-api", settings=runtime, repository=repository
            ),
            timeout=runtime.provider_http_timeout_seconds,
        )


class KrforestResource(ConfigurableResource):
    """산림청 client는 async 전용이라 adapter가 key만 받아 경계를 소유한다."""

    def api_key(
        self,
        *,
        settings: WeatherSettings | None = None,
        repository: WeatherRepository | None = None,
    ) -> str:
        return _provider_key(
            "python-krforest-api", settings=settings, repository=repository
        )


class ExternalWeatherProviderResource(ConfigurableResource):
    """환경 설정으로 생성되는 Open-Meteo/유료 provider resource."""

    provider_key: str = "open_meteo"
    dataset_key: str = "open_meteo_current"

    def create_provider(
        self,
        *,
        settings: WeatherSettings | None = None,
        repository: WeatherRepository | None = None,
    ) -> Any:
        return create_configured_provider(
            self.provider_key, settings=settings, repository=repository
        )
