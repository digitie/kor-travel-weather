"""Settings에서 provider adapter를 안전하게 생성하는 경계."""

from __future__ import annotations

from typing import Any

from kortravelweather.settings import WeatherSettings

from .base import CredentialError, WeatherProvider
from .catalog import provider_spec
from .external import create_provider


def create_configured_provider(
    provider_key: str,
    *,
    settings: WeatherSettings | None = None,
    **overrides: Any,
) -> WeatherProvider:
    runtime = settings or WeatherSettings()
    if provider_key not in runtime.enabled_providers:
        raise ValueError(f"provider가 비활성화되어 있습니다: {provider_key}")
    spec = provider_spec(provider_key)
    if spec.auth_required and runtime.provider_api_key(provider_key) is None:
        raise CredentialError(provider_key)
    kwargs: dict[str, Any] = {
        "api_key": runtime.provider_api_key(provider_key),
        "timeout": runtime.provider_http_timeout_seconds,
        "retries": runtime.provider_retries,
    }
    if provider_key == "open_meteo":
        kwargs["base_url"] = runtime.open_meteo_base_url
    elif provider_key == "wttr_in":
        kwargs["base_url"] = runtime.wttr_in_base_url
    elif provider_key == "visual_crossing":
        kwargs["base_url"] = runtime.visual_crossing_base_url
    kwargs.update(overrides)
    return create_provider(provider_key, **kwargs)
