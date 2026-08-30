"""환경변수 기반 core 설정.

원본 ``kor-travel-map``의 env prefix/secret 분리 원칙을 날씨 전용 이름으로
옮겼다. API와 Dagster가 같은 settings 객체를 사용하므로 실행 surface마다
다른 기본값이 생기지 않는다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ROOT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
SUPPORTED_PROVIDER_KEYS = {
    "python-kma-api",
    "weatherapi",
    "openweathermap",
    "open_meteo",
    "visual_crossing",
    "tomorrow_io",
    "weatherbit",
    "weatherstack",
    "accuweather",
    "wttr_in",
}
PROVIDER_KEY_ALIASES = {
    "open-meteo": "open_meteo",
    "visualcrossing": "visual_crossing",
    "tomorrow.io": "tomorrow_io",
    "wttr.in": "wttr_in",
}


class WeatherSettings(BaseSettings):
    """kor-travel-weather 런타임 설정."""

    model_config = SettingsConfigDict(
        env_prefix="KOR_TRAVEL_WEATHER_",
        # Resolve the repository root first so package-local `uv run` commands
        # use the same credentials/database as root-level API and Dagster runs.
        env_file=(ROOT_ENV_FILE, ".env"),
        # The root .env is also consumed by compose for PostgreSQL/UI secrets.
        # Ignore those service-scoped keys here; all KOR_TRAVEL_WEATHER_ fields
        # remain explicitly declared and validated below.
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # Fail closed when a deployment forgets to set an environment profile.
    # Local development is explicit in `.env.example` (development).
    environment: str = Field(default="production", validation_alias="KOR_TRAVEL_WEATHER_ENV")
    database_url: str = Field(
        default="postgresql+psycopg://weather@127.0.0.1:14100/weather",
        validation_alias="KOR_TRAVEL_WEATHER_DATABASE_URL",
    )
    git_commit: str | None = Field(default=None, validation_alias="KOR_TRAVEL_WEATHER_GIT_COMMIT")
    admin_token: SecretStr | None = Field(
        default=None, validation_alias="KOR_TRAVEL_WEATHER_ADMIN_TOKEN"
    )
    data_go_kr_service_key: SecretStr | None = Field(
        default=None, validation_alias="KOR_TRAVEL_WEATHER_DATA_GO_KR_SERVICE_KEY"
    )
    weatherapi_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "KOR_TRAVEL_WEATHER_WEATHERAPI_API_KEY", "WEATHERAPI_API_KEY", "WEATHER_API_KEY"
        ),
    )
    openweathermap_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "KOR_TRAVEL_WEATHER_OPENWEATHERMAP_API_KEY",
            "OPENWEATHERMAP_API_KEY",
            "OPENWEATHER_API_KEY",
            "OWM_API_KEY",
        ),
    )
    visual_crossing_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "KOR_TRAVEL_WEATHER_VISUAL_CROSSING_API_KEY",
            "VISUAL_CROSSING_API_KEY",
            "VISUALCROSSING_API_KEY",
        ),
    )
    tomorrow_io_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "KOR_TRAVEL_WEATHER_TOMORROW_IO_API_KEY", "TOMORROW_IO_API_KEY", "TOMORROW_API_KEY"
        ),
    )
    weatherbit_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "KOR_TRAVEL_WEATHER_WEATHERBIT_API_KEY", "WEATHERBIT_API_KEY"
        ),
    )
    weatherstack_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "KOR_TRAVEL_WEATHER_WEATHERSTACK_API_KEY", "WEATHERSTACK_API_KEY"
        ),
    )
    accuweather_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "KOR_TRAVEL_WEATHER_ACCUWEATHER_API_KEY", "ACCUWEATHER_API_KEY", "ACCUWEATHER_KEY"
        ),
    )
    open_meteo_base_url: str = Field(
        default="https://api.open-meteo.com/v1",
        validation_alias="KOR_TRAVEL_WEATHER_OPEN_METEO_BASE_URL",
    )
    wttr_in_base_url: str = Field(
        default="https://wttr.in", validation_alias="KOR_TRAVEL_WEATHER_WTTR_IN_BASE_URL"
    )
    visual_crossing_base_url: str = Field(
        default="https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services",
        validation_alias="KOR_TRAVEL_WEATHER_VISUAL_CROSSING_BASE_URL",
    )
    enabled_providers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["python-kma-api", "open_meteo", "wttr_in"],
        validation_alias=AliasChoices("KOR_TRAVEL_WEATHER_ENABLED_PROVIDERS", "WEATHER_PROVIDERS"),
    )
    provider_http_timeout_seconds: float = Field(
        default=15.0,
        validation_alias="KOR_TRAVEL_WEATHER_PROVIDER_HTTP_TIMEOUT_SECONDS",
        gt=0,
        le=120,
    )
    provider_retries: int = Field(
        default=1, validation_alias="KOR_TRAVEL_WEATHER_PROVIDER_RETRIES", ge=0, le=5
    )
    targets: list[dict[str, Any]] = Field(
        default_factory=list, validation_alias="KOR_TRAVEL_WEATHER_TARGETS"
    )
    extra_points: str | None = Field(
        default=None, validation_alias="KOR_TRAVEL_WEATHER_EXTRA_POINTS"
    )
    max_grids_per_run: int = Field(
        default=300, validation_alias="KOR_TRAVEL_WEATHER_MAX_GRIDS_PER_RUN", gt=0, le=5000
    )
    max_targets_per_run: int = Field(
        default=10_000, validation_alias="KOR_TRAVEL_WEATHER_MAX_TARGETS_PER_RUN", gt=0, le=100_000
    )
    max_response_rows_per_run: int = Field(
        default=1_000_000,
        validation_alias="KOR_TRAVEL_WEATHER_MAX_RESPONSE_ROWS_PER_RUN",
        gt=0,
        le=10_000_000,
    )
    max_values_per_run: int = Field(
        default=500_000,
        validation_alias="KOR_TRAVEL_WEATHER_MAX_VALUES_PER_RUN",
        gt=0,
        le=10_000_000,
    )
    max_payload_bytes_per_run: int = Field(
        default=16 * 1024 * 1024,
        validation_alias="KOR_TRAVEL_WEATHER_MAX_PAYLOAD_BYTES_PER_RUN",
        gt=0,
        le=256 * 1024 * 1024,
    )
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="KOR_TRAVEL_WEATHER_CORS_ORIGINS"
    )
    api_base_url: str = Field(
        default="http://127.0.0.1:14101",
        validation_alias="KOR_TRAVEL_WEATHER_API_BASE_URL",
    )

    @field_validator("database_url")
    @classmethod
    def _postgresql_only(cls, value: str) -> str:
        if not value.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError(
                "KOR_TRAVEL_WEATHER_DATABASE_URL은 postgresql:// 또는 "
                "postgresql+psycopg:// DSN이어야 합니다."
            )
        return value

    @field_validator("targets", mode="before")
    @classmethod
    def _parse_targets(cls, value: Any) -> Any:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            parsed = json.loads(value)
            if not isinstance(parsed, list):
                raise ValueError("KOR_TRAVEL_WEATHER_TARGETS는 JSON 배열이어야 합니다.")
            return parsed
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_origins(cls, value: Any) -> Any:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                # ``NoDecode`` lets conventional comma-separated deployment
                # values reach this validator as well as the documented JSON
                # array form.
                return [part.strip() for part in value.split(",") if part.strip()]
            if isinstance(parsed, list):
                return parsed
            raise ValueError("KOR_TRAVEL_WEATHER_CORS_ORIGINS는 JSON 배열이어야 합니다.")
        return value

    @field_validator("enabled_providers", mode="before")
    @classmethod
    def _parse_provider_list(cls, value: Any) -> Any:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return [part.strip() for part in value.split(",") if part.strip()]
            if not isinstance(parsed, list):
                raise ValueError(
                    "enabled providers는 JSON 배열 또는 comma-separated 값이어야 합니다."
                )
            return parsed
        return value

    @field_validator("enabled_providers")
    @classmethod
    def _validate_provider_list(cls, value: list[str]) -> list[str]:
        if any(not isinstance(item, str) for item in value):
            raise ValueError("enabled providers는 문자열 목록이어야 합니다.")
        normalized = [
            PROVIDER_KEY_ALIASES.get(item.strip().lower(), item.strip().lower()) for item in value
        ]
        unknown = sorted(set(normalized) - SUPPORTED_PROVIDER_KEYS)
        if unknown:
            raise ValueError(f"지원하지 않는 provider가 있습니다: {', '.join(unknown)}")
        return list(dict.fromkeys(normalized))

    @field_validator(
        "admin_token",
        "data_go_kr_service_key",
        "weatherapi_api_key",
        "openweathermap_api_key",
        "visual_crossing_api_key",
        "tomorrow_io_api_key",
        "weatherbit_api_key",
        "weatherstack_api_key",
        "accuweather_api_key",
        mode="before",
    )
    @classmethod
    def _empty_secret_is_missing(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}

    def require_admin_token(self) -> str | None:
        """production admin write가 사용할 token을 반환한다."""
        if self.admin_token is None:
            if self.is_production:
                raise RuntimeError("production에서는 KOR_TRAVEL_WEATHER_ADMIN_TOKEN이 필요합니다.")
            return None
        return self.admin_token.get_secret_value()

    def provider_api_key(self, provider: str) -> str | None:
        """provider key를 SecretStr 외부로 노출하지 않고 필요한 순간에만 반환한다."""
        fields = {
            "python-kma-api": "data_go_kr_service_key",
            "weatherapi": "weatherapi_api_key",
            "openweathermap": "openweathermap_api_key",
            "visual_crossing": "visual_crossing_api_key",
            "tomorrow_io": "tomorrow_io_api_key",
            "weatherbit": "weatherbit_api_key",
            "weatherstack": "weatherstack_api_key",
            "accuweather": "accuweather_api_key",
        }
        field_name = fields.get(provider)
        if field_name is None:
            return None
        value = getattr(self, field_name)
        return value.get_secret_value() if value else None


@lru_cache(maxsize=1)
def get_settings() -> WeatherSettings:
    return WeatherSettings()
