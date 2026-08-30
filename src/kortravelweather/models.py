"""공용 날씨 도메인 모델.

원본 ``kor-travel-map``의 ``WeatherValue`` 계약을 weather-only 저장소에
독립시켰다. ``forecast_style``와 ``timeline_bucket``을 분리하고, provider
원문을 payload에 보존하는 규칙은 그대로 유지한다.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KST = timezone(timedelta(hours=9))


class ForecastStyle(StrEnum):
    NOWCAST = "nowcast"
    ULTRA_SHORT = "ultra_short"
    SHORT = "short"
    MID = "mid"
    OBSERVED = "observed"


class TimelineBucket(StrEnum):
    ULTRA_SHORT = "ultra_short"
    SHORT = "short"
    MID = "mid"


class WeatherLocation(BaseModel):
    """KMA 격자와 API 소비자용 위치 카탈로그 항목."""

    model_config = ConfigDict(extra="forbid")

    location_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    name: str = Field(min_length=1, max_length=200)
    latitude: float = Field(ge=33.0, le=43.0)
    longitude: float = Field(ge=124.0, le=132.0)
    nx: int | None = Field(default=None, ge=1, le=300)
    ny: int | None = Field(default=None, ge=1, le=300)
    region_code: str | None = Field(default=None, max_length=32)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("latitude", "longitude", mode="after")
    @classmethod
    def _database_precision(cls, value: float) -> float:
        """Normalize coordinates to the six decimals used by Numeric(9, 6).

        KMA anchors are persisted with six fractional digits.  Normalizing at
        the DTO boundary keeps a value loaded from SQLAlchemy equal to the
        original configuration, so the immutable-anchor guard does not treat
        database rounding as a coordinate mutation on every sync.
        """
        if not math.isfinite(value):
            raise ValueError("좌표는 유한한 숫자여야 합니다.")
        return float(Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


class WeatherValue(BaseModel):
    """한 위치·metric·시각의 immutable weather fact."""

    model_config = ConfigDict(extra="forbid")

    location_id: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=120)
    dataset_key: str = Field(min_length=1, max_length=160)
    weather_domain: str = Field(min_length=1, max_length=120)
    forecast_style: ForecastStyle
    timeline_bucket: TimelineBucket | None = None
    metric_key: str = Field(min_length=1, max_length=80)
    metric_name: str | None = Field(default=None, max_length=200)
    source_metric_key: str | None = Field(default=None, max_length=80)
    source_metric_name: str | None = Field(default=None, max_length=200)
    value_number: Decimal | None = None
    value_text: str | None = Field(default=None, max_length=1000)
    unit: str | None = Field(default=None, max_length=32)
    severity: str | None = Field(default=None, max_length=64)
    issued_at: datetime | None = None
    valid_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    observed_at: datetime | None = None
    target_at: datetime | None = None
    known_at: datetime | None = None
    normalization_version: str = "kma-v1"
    payload: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=lambda: datetime.now(KST))
    source_record_key: str | None = Field(default=None, max_length=255)

    @field_validator(
        "issued_at",
        "valid_at",
        "valid_from",
        "valid_until",
        "observed_at",
        "target_at",
        "known_at",
        "collected_at",
    )
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("datetime은 timezone-aware여야 합니다.")
        return value

    @field_validator("value_number", mode="after")
    @classmethod
    def _database_value_precision(cls, value: Decimal | None) -> Decimal | None:
        """Match the NUMERIC(14, 4) storage contract before identity checks."""
        if value is None:
            return None
        if not value.is_finite():
            raise ValueError("value_number는 유한한 숫자여야 합니다.")
        return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    @model_validator(mode="after")
    def _valid_value(self) -> WeatherValue:
        if self.value_number is None and self.value_text is None:
            raise ValueError("value_number 또는 value_text 중 하나는 필요합니다.")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until은 valid_from보다 빠를 수 없습니다.")
        ranges: dict[str, tuple[Decimal | None, Decimal | None]] = {
            "REH": (Decimal("0"), Decimal("100")),
            "POP": (Decimal("0"), Decimal("100")),
            "VEC": (Decimal("0"), Decimal("360")),
            "WSD": (Decimal("0"), None),
            "WSDM": (Decimal("0"), None),
            "RN1": (Decimal("0"), None),
            "PCP": (Decimal("0"), None),
            "SNO": (Decimal("0"), None),
        }
        if self.value_number is not None and self.metric_key in ranges:
            lower, upper = ranges[self.metric_key]
            if lower is not None and self.value_number < lower:
                raise ValueError(f"{self.metric_key} 값은 {lower} 이상이어야 합니다.")
            if upper is not None and self.value_number > upper:
                raise ValueError(f"{self.metric_key} 값은 {upper} 이하여야 합니다.")
        return self

    def identity(
        self,
    ) -> tuple[
        str,
        str,
        str,
        str,
        str,
        str,
        datetime | None,
        str | None,
    ]:
        """ADR-089 fact identity의 논리 축.

        ``issued_at``/``valid_at``/``observed_at``/``known_at``은 원천·bitemporal
        부가 시각이고, 수정 응답은 ``source_record_key``가 다른 append-only
        revision으로 보존된다. ``target_at``은 forecast/observation을 공통으로
        조회하는 canonical target 축이다.
        """
        return (
            self.location_id,
            self.provider,
            self.dataset_key,
            self.weather_domain,
            self.forecast_style.value,
            self.metric_key,
            self.target_at,
            self.source_record_key,
        )

    def identity_key(
        self,
        source_record_key: str | None = None,
        *,
        target_at: datetime | None = None,
    ) -> str:
        """immutable fact id.

        A source response key is a revision discriminator, not a temporal
        observation. Passing it explicitly lets the repository create a stable
        lineage key for legacy/custom values that omitted one.
        """
        revision_key = (
            source_record_key if source_record_key is not None else self.source_record_key
        )
        canonical_target = target_at if target_at is not None else self.target_at
        if canonical_target is not None:
            if canonical_target.tzinfo is None:
                raise ValueError("target_at은 timezone-aware여야 합니다.")
            # PostgreSQL drivers may return a different equivalent offset.
            # Hash the instant, never its presentation offset, so value_id
            # survives a DB round-trip.
            canonical_target_text = canonical_target.astimezone(UTC).isoformat()
        else:
            canonical_target_text = None
        encoded = json.dumps(
            [
                self.location_id,
                self.provider,
                self.dataset_key,
                self.weather_domain,
                self.forecast_style.value,
                self.metric_key,
                canonical_target_text,
                revision_key,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return "wv_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


class SyncRun(BaseModel):
    """provider sync 실행 이력."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    provider: str
    dataset_key: str
    status: str
    started_at: datetime
    heartbeat_at: datetime | None = None
    finished_at: datetime | None = None
    locations_total: int = 0
    grids_fetched: int = 0
    mid_groups_fetched: int = 0
    requests_fetched: int = 0
    values_loaded: int = 0
    error: str | None = None


def kst_now() -> datetime:
    return datetime.now(KST)
