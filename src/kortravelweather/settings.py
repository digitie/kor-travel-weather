"""환경변수 기반 core 설정.

원본 ``kor-travel-map``의 env prefix/secret 분리 원칙을 날씨 전용 이름으로
옮겼다. API와 Dagster가 같은 settings 객체를 사용하므로 실행 surface마다
다른 기본값이 생기지 않는다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WeatherSettings(BaseSettings):
    """kor-travel-weather 런타임 설정."""

    model_config = SettingsConfigDict(
        env_prefix="KOR_TRAVEL_WEATHER_",
        env_file=(".env",),
        extra="forbid",
        case_sensitive=False,
        populate_by_name=True,
    )

    environment: str = Field(default="development", validation_alias="KOR_TRAVEL_WEATHER_ENV")
    database_url: str = Field(
        default="sqlite:///./data/weather.db",
        validation_alias="KOR_TRAVEL_WEATHER_DATABASE_URL",
    )
    git_commit: str | None = Field(default=None, validation_alias="KOR_TRAVEL_WEATHER_GIT_COMMIT")
    admin_token: SecretStr | None = Field(
        default=None, validation_alias="KOR_TRAVEL_WEATHER_ADMIN_TOKEN"
    )
    data_go_kr_service_key: SecretStr | None = Field(
        default=None, validation_alias="KOR_TRAVEL_WEATHER_DATA_GO_KR_SERVICE_KEY"
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
    cors_origins: list[str] = Field(
        default_factory=list, validation_alias="KOR_TRAVEL_WEATHER_CORS_ORIGINS"
    )

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
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("admin_token", "data_go_kr_service_key", mode="before")
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


@lru_cache(maxsize=1)
def get_settings() -> WeatherSettings:
    return WeatherSettings()
