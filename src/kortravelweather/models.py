"""공용 날씨 도메인 모델.

원본 ``kor-travel-map``의 ``WeatherValue`` 계약을 weather-only 저장소에
독립시켰다. ``forecast_style``와 ``timeline_bucket``을 분리하고, provider
원문을 payload에 보존하는 규칙은 그대로 유지한다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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

    @model_validator(mode="after")
    def _valid_value(self) -> WeatherValue:
        if self.value_number is None and self.value_text is None:
            raise ValueError("value_number 또는 value_text 중 하나는 필요합니다.")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until은 valid_from보다 빠를 수 없습니다.")
        if self.value_number is not None and not self.value_number.is_finite():
            raise ValueError("value_number는 유한한 숫자여야 합니다.")
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
        datetime | None,
        datetime | None,
    ]:
        """provider와 발표/유효 시각으로 정의하는 자연키.

        ``known_at``은 수신 시각이고 ``target_at``은 bitemporal 질의 축이므로
        재수집 때 자연키를 바꾸지 않는다. 원천 응답의 수정본을 append-only로
        보존하기 위해 실제 ``value_id``(아래 ``identity_key``)에는
        ``target_at``과 ``source_record_key``를 revision 축으로 추가한다.
        """
        return (
            self.location_id,
            self.provider,
            self.dataset_key,
            self.weather_domain,
            self.forecast_style.value,
            self.metric_key,
            self.issued_at,
            self.valid_at,
            self.observed_at,
        )

    def identity_key(self, source_record_key: str | None = None) -> str:
        """immutable fact id.

        A source response key is a revision discriminator, not a temporal
        observation. Passing it explicitly lets the repository create a stable
        lineage key for legacy/custom values that omitted one.
        """
        revision_key = (
            source_record_key if source_record_key is not None else self.source_record_key
        )
        encoded = json.dumps(
            [
                self.location_id,
                self.provider,
                self.dataset_key,
                self.weather_domain,
                self.forecast_style.value,
                self.metric_key,
                self.issued_at.isoformat() if self.issued_at else None,
                self.valid_at.isoformat() if self.valid_at else None,
                self.observed_at.isoformat() if self.observed_at else None,
                self.target_at.isoformat() if self.target_at else None,
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
    finished_at: datetime | None = None
    locations_total: int = 0
    grids_fetched: int = 0
    values_loaded: int = 0
    error: str | None = None


def kst_now() -> datetime:
    return datetime.now(KST)
