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
#: Duplicates the keys in ``providers.catalog.PROVIDER_CATALOG``, which cannot
#: be imported here: ``providers/__init__`` pulls in the factory, and the
#: factory imports this module.  ``test_provider_registries_agree`` fails if the
#: two ever drift, along with the credential map in ``provider_api_key``.
SUPPORTED_PROVIDER_KEYS = {
    "python-kma-api",
    "python-airkorea-api",
    "python-khoa-api",
    "python-krforest-api",
    "python-krex-api",
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

ADMIN_TOKEN_MIN_LENGTH = 16
METRICS_TOKEN_MIN_LENGTH = 16
_WEAK_ADMIN_TOKENS = {
    "admin",
    "change-me",
    "change-this-token",
    "changeme",
    "password",
    "placeholder",
    "replace-me",
    "secret",
    "test-token",
    "your-token",
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
    credential_encryption_key: SecretStr | None = Field(
        default=None,
        validation_alias="KOR_TRAVEL_WEATHER_CREDENTIAL_ENCRYPTION_KEY",
    )
    metrics_token: SecretStr | None = Field(
        default=None,
        validation_alias="KOR_TRAVEL_WEATHER_METRICS_TOKEN",
    )
    data_go_kr_service_key: SecretStr | None = Field(
        default=None, validation_alias="KOR_TRAVEL_WEATHER_DATA_GO_KR_SERVICE_KEY"
    )
    airkorea_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "KOR_TRAVEL_WEATHER_AIRKOREA_API_KEY",
            "AIRKOREA_API_KEY",
            "KOR_TRAVEL_WEATHER_DATA_GO_KR_SERVICE_KEY",
        ),
    )
    # 한국도로공사만 data.ex.co.kr 키를 쓴다. data.go.kr 키로는 인증되지 않으므로
    # 공유 키로 fallback 하지 않는다 -- 그랬다면 매 실행이 401로 실패한다.
    krex_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "KOR_TRAVEL_WEATHER_KREX_API_KEY",
            "KEX_EX_API_KEY",
        ),
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
        default_factory=lambda: [
            "python-kma-api",
            "python-airkorea-api",
            "python-khoa-api",
            "python-krforest-api",
            # python-krex-api stays off by default: it authenticates with a
            # data.ex.co.kr key that most deployments will not have, and
            # enabling it buys a failing schedule twice a day.  The adapter is
            # tested; add it to KOR_TRAVEL_WEATHER_ENABLED_PROVIDERS with the
            # key to turn it on.
            "open_meteo",
            "weatherapi",
            "openweathermap",
            "visual_crossing",
            "tomorrow_io",
            "weatherbit",
            "weatherstack",
            "accuweather",
            "wttr_in",
        ],
        validation_alias=AliasChoices("KOR_TRAVEL_WEATHER_ENABLED_PROVIDERS", "WEATHER_PROVIDERS"),
    )
    provider_http_timeout_seconds: float = Field(
        default=20.0,
        validation_alias="KOR_TRAVEL_WEATHER_PROVIDER_HTTP_TIMEOUT_SECONDS",
        gt=0,
        le=120,
    )
    provider_retries: int = Field(
        default=3, validation_alias="KOR_TRAVEL_WEATHER_PROVIDER_RETRIES", ge=0, le=5
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
    # How many of the ~1,400 catalog locations each external provider may sweep.
    # Unlisted providers sweep all of them. Each cap is that vendor's published
    # free-tier quota, less a 20% margin, divided by the provider's dataset count
    # and the eight sweeps a day its three-hourly schedule performs:
    #
    #   weatherapi     100,000/mo -> 150 locations =  72,000/mo
    #   open_meteo      10,000/day -> 300 locations = 7,200 weighted/day
    #   openweathermap 1,000,000/mo -> uncapped    = 685,440/mo for all 1,428
    #
    # Open-Meteo bills weighted calls, not requests: its published formula is
    # max(1, variables/10) * max(1, days/7) * locations, and this project's
    # query asks for 15 variables over the default 7 days, so every request
    # costs 1.5. Sizing it as 450 locations by raw request count put it at
    # 10,800 weighted/day against a 10,000 ceiling, and it began failing with
    # "provider rate limit" once the daily counter caught up.
    #
    # These sit near 70% of each ceiling rather than exactly at the 80% the
    # margin allows, because a retried request spends quota too: one location
    # can cost up to four calls, so planning to the last permitted call leaves
    # nothing for the failures the retries exist to absorb.
    #
    # Exceeding a quota does not fail cleanly: the vendor throttles, every
    # request then takes ~15s instead of ~0.3s, and the run outlives its own
    # schedule until the queue fills with runs that will never finish.
    provider_location_caps: dict[str, int] = Field(
        default_factory=lambda: {"weatherapi": 150, "open_meteo": 300},
        validation_alias="KOR_TRAVEL_WEATHER_PROVIDER_LOCATION_CAPS",
    )
    # Minimum seconds between two requests to the same provider. Monthly quota
    # is not the only ceiling: OpenWeatherMap also publishes 60 calls/minute,
    # and a 2,856-request sweep issued back to back runs at roughly 200/minute
    # once responses are fast, which is throttled even though the month is well
    # inside budget. 1.25s holds it at 48/minute, a 20% margin under the limit.
    provider_min_request_interval_seconds: dict[str, float] = Field(
        default_factory=lambda: {"openweathermap": 1.25},
        validation_alias="KOR_TRAVEL_WEATHER_PROVIDER_MIN_REQUEST_INTERVAL_SECONDS",
    )
    max_response_rows_per_run: int = Field(
        default=1_000_000,
        validation_alias="KOR_TRAVEL_WEATHER_MAX_RESPONSE_ROWS_PER_RUN",
        gt=0,
        le=10_000_000,
    )
    max_values_per_run: int = Field(
        default=8_000_000,
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
    # 전국 관측망(해양·산악·고속도로)은 한 번의 호출이 전국을 돌려주므로
    # location 예산이 아니라 record 예산으로 제한한다.
    regional_max_records: int = Field(
        default=1000,
        validation_alias="KOR_TRAVEL_WEATHER_REGIONAL_MAX_RECORDS",
        gt=0,
        le=20_000,
    )
    #: 청정넷 readings land on ten-minute marks, so an hour is six per station.
    #: A window rather than "since last run" because the vendor's date filter is
    #: day-granular and exclusive at both ends; the trim happens client-side.
    regional_dust_hours: int = Field(
        default=3,
        validation_alias="KOR_TRAVEL_WEATHER_REGIONAL_DUST_HOURS",
        gt=0,
        le=168,
    )
    regional_dust_max_records: int = Field(
        default=20_000,
        validation_alias="KOR_TRAVEL_WEATHER_REGIONAL_DUST_MAX_RECORDS",
        gt=0,
        le=500_000,
    )
    retention_days: int = Field(
        default=2,
        validation_alias="KOR_TRAVEL_WEATHER_RETENTION_DAYS",
        gt=0,
        le=3650,
    )
    #: How far ahead the nightly job creates partitions.  It runs once a day, so
    #: it has to look further forward than back: a day with no partition sends
    #: its rows to DEFAULT, where retention can never reach them again.
    retention_ahead_days: int = Field(
        default=7,
        validation_alias="KOR_TRAVEL_WEATHER_RETENTION_AHEAD_DAYS",
        gt=0,
        le=365,
    )
    metrics_port: int | None = Field(
        default=None,
        validation_alias="KOR_TRAVEL_WEATHER_METRICS_PORT",
        ge=1024,
        le=65535,
    )
    airkorea_max_stations: int = Field(
        default=1000,
        validation_alias="KOR_TRAVEL_WEATHER_AIRKOREA_MAX_STATIONS",
        gt=0,
        le=5000,
    )
    kma_alert_station_id: str = Field(
        default="108",
        validation_alias="KOR_TRAVEL_WEATHER_KMA_ALERT_STATION_ID",
        min_length=1,
        max_length=16,
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

    @field_validator("provider_min_request_interval_seconds", mode="before")
    @classmethod
    def _parse_request_intervals(cls, value: Any) -> Any:
        if value is None or value == "":
            return {}
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "KOR_TRAVEL_WEATHER_PROVIDER_MIN_REQUEST_INTERVAL_SECONDS는 "
                    '{"provider": 초} 형태의 JSON object여야 합니다.'
                ) from exc
            if not isinstance(parsed, dict):
                raise ValueError(
                    "KOR_TRAVEL_WEATHER_PROVIDER_MIN_REQUEST_INTERVAL_SECONDS는 "
                    "JSON object여야 합니다."
                )
            return parsed
        return value

    @field_validator("provider_min_request_interval_seconds")
    @classmethod
    def _validate_request_intervals(cls, value: dict[str, float]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for provider, interval in value.items():
            key = PROVIDER_KEY_ALIASES.get(
                str(provider).strip().lower(), str(provider).strip().lower()
            )
            if key not in SUPPORTED_PROVIDER_KEYS:
                raise ValueError(f"지원하지 않는 provider가 있습니다: {provider}")
            if isinstance(interval, bool) or not isinstance(interval, (int, float)):
                raise ValueError(f"{key}의 요청 간격은 숫자여야 합니다: {interval!r}")
            if interval < 0 or interval > 60:
                raise ValueError(f"{key}의 요청 간격은 0~60초여야 합니다: {interval!r}")
            normalized[key] = float(interval)
        return normalized

    @field_validator("provider_location_caps", mode="before")
    @classmethod
    def _parse_location_caps(cls, value: Any) -> Any:
        if value is None or value == "":
            return {}
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "KOR_TRAVEL_WEATHER_PROVIDER_LOCATION_CAPS는 "
                    '{"provider": 개수} 형태의 JSON object여야 합니다.'
                ) from exc
            if not isinstance(parsed, dict):
                raise ValueError(
                    "KOR_TRAVEL_WEATHER_PROVIDER_LOCATION_CAPS는 JSON object여야 합니다."
                )
            return parsed
        return value

    @field_validator("provider_location_caps")
    @classmethod
    def _validate_location_caps(cls, value: dict[str, int]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for provider, cap in value.items():
            key = PROVIDER_KEY_ALIASES.get(
                str(provider).strip().lower(), str(provider).strip().lower()
            )
            if key not in SUPPORTED_PROVIDER_KEYS:
                raise ValueError(f"지원하지 않는 provider가 있습니다: {provider}")
            if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
                # A cap of 0 would silently collect nothing, which reads as a
                # broken provider rather than a deliberate one; remove the entry
                # or disable the provider instead.
                raise ValueError(f"{key}의 위치 상한은 1 이상의 정수여야 합니다: {cap!r}")
            normalized[key] = cap
        return normalized

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
        "credential_encryption_key",
        "metrics_token",
        "data_go_kr_service_key",
        "airkorea_api_key",
        "krex_api_key",
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
        value = self.admin_token.get_secret_value().strip()
        if self.is_production:
            normalized = value.lower().replace("_", "-").replace(" ", "-")
            if len(value) < ADMIN_TOKEN_MIN_LENGTH or normalized in _WEAK_ADMIN_TOKENS:
                raise RuntimeError(
                    "production에서는 KOR_TRAVEL_WEATHER_ADMIN_TOKEN에 "
                    f"{ADMIN_TOKEN_MIN_LENGTH}자 이상의 무작위 값을 설정해야 합니다."
                )
        return value

    def require_metrics_token(self) -> str | None:
        """Return the dedicated scrape bearer token in a production profile."""
        value = self.metrics_token.get_secret_value().strip() if self.metrics_token else None
        if not self.is_production:
            return value
        if not value:
            raise RuntimeError(
                "production에서는 KOR_TRAVEL_WEATHER_METRICS_TOKEN이 필요합니다."
            )
        normalized = value.lower().replace("_", "-").replace(" ", "-")
        admin = self.admin_token.get_secret_value().strip() if self.admin_token else ""
        if (
            len(value) < METRICS_TOKEN_MIN_LENGTH
            or normalized in _WEAK_ADMIN_TOKENS
            or (admin and value == admin)
        ):
            raise RuntimeError(
                "production에서는 KOR_TRAVEL_WEATHER_METRICS_TOKEN에 "
                f"{METRICS_TOKEN_MIN_LENGTH}자 이상의 admin token과 다른 무작위 값을 "
                "설정해야 합니다."
            )
        return value

    def require_credential_encryption_key(self) -> str:
        """Return the Fernet key used for database-backed provider secrets.

        The setting remains optional so deployments which only use environment
        credentials do not fail during process startup.  Any code that reads
        or writes an encrypted database credential must call this method and
        therefore fails closed when the key is absent or malformed.
        """
        value = (
            self.credential_encryption_key.get_secret_value()
            if self.credential_encryption_key
            else None
        )
        if not value:
            raise RuntimeError(
                "KOR_TRAVEL_WEATHER_CREDENTIAL_ENCRYPTION_KEY가 필요합니다."
            )
        try:
            from cryptography.fernet import Fernet

            Fernet(value.encode("ascii"))
        except (ImportError, UnicodeError, TypeError, ValueError) as exc:
            raise RuntimeError(
                "KOR_TRAVEL_WEATHER_CREDENTIAL_ENCRYPTION_KEY가 올바른 Fernet key가 아닙니다."
            ) from exc
        return value

    def optional_credential_encryption_key(self) -> str | None:
        """Return the configured Fernet key for internal provider resolution."""
        return (
            self.credential_encryption_key.get_secret_value()
            if self.credential_encryption_key
            else None
        )

    def provider_api_key(self, provider: str) -> str | None:
        """provider key를 SecretStr 외부로 노출하지 않고 필요한 순간에만 반환한다."""
        fields = {
            "python-kma-api": "data_go_kr_service_key",
            "python-airkorea-api": "airkorea_api_key",
            "python-khoa-api": "data_go_kr_service_key",
            "python-krforest-api": "data_go_kr_service_key",
            "python-krex-api": "krex_api_key",
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
        # AirKorea's public-data service uses the same Data.go.kr service key
        # as the KMA adapter.  Compose intentionally exposes the dedicated
        # AirKorea variable as optional, so an empty override must not mask the
        # shared key already configured for the vendored client.
        if provider == "python-airkorea-api" and not value:
            value = self.data_go_kr_service_key
        return value.get_secret_value() if value else None


@lru_cache(maxsize=1)
def get_settings() -> WeatherSettings:
    return WeatherSettings()
