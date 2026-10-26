"""중기 지역 한 응답의 재해석 증거. 실제 수집·head/REST 연결은 별도다.

SDK는 중기 tmFc를 요청 문맥으로 반환한다. 원문에 없는 발표 시각을 원문 필드라고
꾸미지 않고, public MidForecastItem을 복원해 기존 Weather normalizer를 호출한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from kma import MidForecastItem, PaginationReceipt
from kma.time_utils import base_available_at, latest_mid_fcst_time, parse_kma_datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .export_artifacts import ExportArtifactError
from .export_receipts import source_scope_key
from .models import KST, WeatherValue
from .providers.kma import (
    mid_land_forecast_to_weather_values,
    mid_temperature_to_weather_values,
)


class MidScopeDescriptor(BaseModel):
    """발표 회차는 head revision이며 지역·part·정규화 정책 namespace와 구분한다."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: Literal["python-kma-api"] = "python-kma-api"
    dataset_key: Literal["kma_mid_forecast"] = "kma_mid_forecast"
    entity_type: Literal["weather_response"] = "weather_response"
    entity_id: str = Field(min_length=1, max_length=120)
    source_part: Literal["land", "temperature"]
    region_code: str = Field(min_length=1, max_length=32)
    forecast_style: Literal["mid"] = "mid"
    location_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)
    normalizer_revision: str
    winner_policy_revision: Literal["latest-issued-mid-v1"] = "latest-issued-mid-v1"
    coverage_contract: Literal["kma-mid-region-v1"] = "kma-mid-region-v1"

    @model_validator(mode="after")
    def canonical(self):
        if (
            self.region_code.strip() != self.region_code
            or self.entity_id != f"mid-{self.source_part}:{self.region_code}"
            or self.normalizer_revision != f"kma-mid-{self.source_part}-v1"
            or tuple(sorted(set(self.location_ids))) != self.location_ids
            or any(not key or len(key) > 120 for key in self.location_ids)
        ):
            raise ValueError("중기 source의 지역·part·위치·정규화 정책이 다릅니다.")
        return self

    @property
    def source_scope(self) -> str:
        return source_scope_key(self.provider, self.dataset_key, self.entity_type, self.entity_id)

    @property
    def namespace(self) -> str:
        from .export_heads import _digest

        return _digest(self.model_dump(mode="json"))


@dataclass(frozen=True)
class MidSourceProof:
    revision: datetime
    source_collected_at: datetime
    values: tuple[WeatherValue, ...]


def _weather_description(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
        return False
    if value.strip().casefold() in {"null", "none", "n/a", "na", "-", "--"}:
        return False
    try:
        Decimal(value.strip())
    except InvalidOperation:
        return True
    # 중기 SKY는 서술형이다. 숫자·숫자 sentinel·NaN/Inf는 하늘 상태 증거가 아니다.
    return False


def _revision(value: Any, *, operation: str, fetched: datetime) -> datetime:
    if not isinstance(value, str) or len(value) != 12 or not value.isascii() or not value.isdigit():
        raise ExportArtifactError("중기 tmFc의 SDK 시각 증거가 올바르지 않습니다.")
    issued = parse_kma_datetime(value[:8], value[8:])
    # 발표시간·지연을 직접 계산하지 않고 SDK의 공개 정책으로 유효 회차를 확인한다.
    available = base_available_at(operation, value[:8], value[8:])
    if latest_mid_fcst_time(available) != value or available.astimezone(UTC) > fetched:
        raise ExportArtifactError("중기 tmFc가 SDK의 유효 발표 회차가 아닙니다.")
    return issued.astimezone(UTC)


#: KMA 중기 응답의 마지막 예보일과 반나절(오전/오후) 구간의 마지막 날.
_MID_LAST_DAY = 10
_MID_HALF_DAY_LAST = 7
#: 기간 확대(2024-11-28) 이전 응답의 첫 예보일. 이전 응답은 발표 시각과 무관하게 D+3부터다.
_MID_LEGACY_FIRST_DAY = 3
#: normalizer가 읽는 원문 key(camel/snake)만 센다. ``taMax10High`` 같은 범위 key는 제외한다.
_MID_DAY_KEYS = {
    "land": re.compile(r"^(?:wf|rnSt)(\d+)(?:Am|Pm)?$|^(?:wf|rn_st)_(\d+)(?:_am|_pm)?$"),
    "temperature": re.compile(r"^ta(?:Min|Max)(\d+)$|^ta_(?:min|max)_(\d+)$"),
}


def _published_mid_days(raw: Mapping[str, Any], part: str) -> set[int]:
    """원문이 실제로 공개한 예보일을 key에서 읽는다. 값의 완결은 정규화 결과로 따로 본다."""
    days: set[int] = set()
    for key in raw:
        match = _MID_DAY_KEYS[part].match(str(key))
        if match:
            days.add(int(match.group(1) or match.group(2)))
    return days


def _mid_first_days(revision: datetime) -> tuple[int, int]:
    """그 발표 회차가 공개할 수 있는 첫 예보일(이전 형식, 현재 형식).

    KMA 단기예보 기간 확대 뒤 단기예보가 02~14시 발표는 D+3, 17~23시 발표는 D+4까지
    덮으므로 중기 응답은 06시 발표가 D+4, 18시 발표가 D+5부터 시작한다
    (운영 실측: 2026-10-26 18시 taMin5~10, 2026-10-27 06시 taMin4~10).
    """
    current = 4 if revision.astimezone(KST).hour < 12 else 5
    return _MID_LEGACY_FIRST_DAY, current


def _mid_template(part: str, first: int) -> dict[str, set[tuple[int, str | None]]]:
    """공개 첫 예보일에서 metric별로 반드시 있어야 하는 (예보일, 구간) 전체 집합."""
    if part == "land":
        slots = {
            (day, period)
            for day in range(first, _MID_HALF_DAY_LAST + 1)
            for period in ("am", "pm")
        } | {(day, None) for day in range(_MID_HALF_DAY_LAST + 1, _MID_LAST_DAY + 1)}
        return {"SKY": slots, "POP": slots}
    days = {(day, None) for day in range(first, _MID_LAST_DAY + 1)}
    return {"TMN": days, "TMX": days}


def _checked_values(descriptor, item, record, revision):
    normalizer = (
        mid_land_forecast_to_weather_values
        if descriptor.source_part == "land"
        else mid_temperature_to_weather_values
    )
    # 응답이 공개한 예보일 범위를 원문에서 읽고, 그 범위 전체가 빠짐없이 있어야 한다.
    # 첫 예보일은 그 발표 회차에 KMA가 공개하는 날만 받고 끝은 항상 D+10이다.
    published = _published_mid_days(item.raw, descriptor.source_part)
    first = min(published, default=0)
    if (
        first not in _mid_first_days(revision)
        or published != set(range(first, _MID_LAST_DAY + 1))
    ):
        raise ExportArtifactError("중기 응답의 공개 예보일 범위가 KMA 공개 범위와 다릅니다.")
    expected = _mid_template(descriptor.source_part, first)
    result = []
    for location in descriptor.location_ids:
        values = normalizer(
            [item],
            location_id=location,
            source_record_key=record["source_record_key"],
            known_at=record["fetched_at"],
        )
        identities = set()
        metrics = {key: [] for key in expected}
        slots = {key: set() for key in expected}
        for value in values:
            identity = (value.metric_key, value.target_at)
            if (
                value.metric_key not in metrics
                or identity in identities
                or value.issued_at.astimezone(UTC) != revision
                or value.target_at <= value.issued_at
            ):
                raise ExportArtifactError("중기 정규화의 시각·metric·identity가 다릅니다.")
            identities.add(identity)
            metrics[value.metric_key].append(value)
            slots[value.metric_key].add(
                (value.payload.get("day_offset"), value.payload.get("period"))
            )
            if value.metric_key == "SKY":
                # 기존 normalizer는 원문을 str로 변환한다. 그 전에 선택한 원문 필드가
                # 실제 문자열이었는지는 normalizer가 보존한 payload로 따로 증명한다.
                raw_weather = value.payload.get("wf")
                if (
                    not _weather_description(raw_weather)
                    or not value.value_text
                    or not value.value_text.strip()
                    or len(value.value_text) > 120
                ):
                    raise ExportArtifactError("중기 하늘 상태가 비어 있거나 상한을 넘었습니다.")
            else:
                number = value.value_number
                low, high = (0, 100) if value.metric_key == "POP" else (-90, 65)
                if number is None or not number.is_finite() or not low <= number <= high:
                    raise ExportArtifactError("중기 기온·강수확률의 정상 숫자 증거가 없습니다.")
        if any(
            len(metrics[key]) != len(template) or slots[key] != template
            for key, template in expected.items()
        ):
            raise ExportArtifactError("중기 지역 응답의 필수 전체 구간이 빠졌습니다.")
        if descriptor.source_part == "temperature":
            minima = {v.target_at: v.value_number for v in metrics["TMN"]}
            maxima = {v.target_at: v.value_number for v in metrics["TMX"]}
            if minima.keys() != maxima.keys() or any(minima[at] > maxima[at] for at in minima):
                raise ExportArtifactError("중기 최저·최고기온의 구간이나 순서가 다릅니다.")
        result.extend(values)
    return tuple(result)


def _prove_mid_source(descriptor: MidScopeDescriptor, record: Mapping[str, Any]) -> MidSourceProof:
    """한 지역의 완전한 1행을 검증한다. 전국 집합·SDK pagination 완결을 주장하지 않는다."""
    try:
        if any(
            record[key] != getattr(descriptor, field)
            for key, field in (
                ("provider", "provider"),
                ("dataset_key", "dataset_key"),
                ("source_entity_type", "entity_type"),
                ("source_entity_id", "entity_id"),
            )
        ):
            raise ExportArtifactError("중기 원천이 사전 선언 scope와 다릅니다.")
        fetched = record["fetched_at"]
        if (
            not isinstance(fetched, datetime)
            or fetched.tzinfo is None
            or fetched.utcoffset() is None
        ):
            raise ExportArtifactError("중기 조회 시각은 timezone-aware여야 합니다.")
        fetched = fetched.astimezone(UTC)
        payload = record["payload"]
        rows, metadata = payload["rows"], payload["response_metadata"]
        if (
            payload["dataset_key"] != descriptor.dataset_key
            or payload["location_id"] != descriptor.entity_id
            or not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], Mapping)
        ):
            raise ExportArtifactError("중기 지역 응답은 선언한 단일 완결 원문이어야 합니다.")
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
        )
        if (
            record["source_record_key"]
            != "sr_" + hashlib.sha256(canonical.encode()).hexdigest()[:48]
        ):
            raise ExportArtifactError("중기 원문과 source record key가 다릅니다.")
        operation = "getMidLandFcst" if descriptor.source_part == "land" else "getMidTa"
        if (
            metadata["provider"] != "data.go.kr"
            or metadata["service_name"] != "MidFcstInfoService"
            or metadata["endpoint"] != f"MidFcstInfoService/{operation}"
        ):
            raise ExportArtifactError("중기 SDK endpoint 증거가 다릅니다.")
        request = metadata["request_params"]
        if (
            request["regId"] != descriptor.region_code
            or type(request["pageNo"]) is not int
            or request["pageNo"] != 1
            or type(request["numOfRows"]) is not int
            or not 1 <= request["numOfRows"] <= 1000
        ):
            raise ExportArtifactError("중기 지역·첫 페이지 요청 증거가 다릅니다.")
        revision = _revision(request["tmFc"], operation=operation, fetched=fetched)
        if revision > fetched or fetched - revision > timedelta(hours=18):
            raise ExportArtifactError("중기 발표가 조회 이후이거나 수용 기간을 벗어납니다.")
        if (metadata.get("base_date") is not None or metadata.get("base_time") is not None) and (
            metadata.get("base_date") != request["tmFc"][:8]
            or metadata.get("base_time") != request["tmFc"][8:]
        ):
            raise ExportArtifactError("중기 base와 요청 발표 회차가 다릅니다.")
        receipt = metadata.get("pagination")
        if receipt is not None:
            parsed = PaginationReceipt.model_validate(receipt, strict=True)
            if (
                not parsed.complete
                or parsed.received_item_count != 1
                or parsed.page_count != 1
                or parsed.expected_total_count not in (None, 1)
            ):
                raise ExportArtifactError("중기 pagination이 단일 완결 원문과 다릅니다.")
        raw = rows[0]
        if raw.get("regId", raw.get("reg_id")) != descriptor.region_code:
            raise ExportArtifactError("중기 원문 지역이 사전 선언과 다릅니다.")
        echoed = raw.get("tmFc", raw.get("tm_fc"))
        if echoed is not None and echoed != request["tmFc"]:
            raise ExportArtifactError("중기 원문 발표 회차가 SDK 요청과 다릅니다.")
        item = MidForecastItem(
            operation=operation,
            tm_fc=request["tmFc"],
            reg_id=descriptor.region_code,
            raw=dict(raw),
        )
        return MidSourceProof(
            revision, fetched, _checked_values(descriptor, item, record, revision)
        )
    except ExportArtifactError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        raise ExportArtifactError("중기 원천의 요청·응답 재해석 증명이 불완전합니다.") from exc


def prove_mid_source(descriptor: MidScopeDescriptor, record: Mapping[str, Any]) -> MidSourceProof:
    """소수 위치의 전체 증거. 큰 지역은 template과 streaming 경로를 사용한다."""
    if len(descriptor.location_ids) > 50:
        raise ExportArtifactError("중기 전체 값 복원은 50곳까지 허용합니다. template을 사용하세요.")
    return _prove_mid_source(descriptor, record)


def prove_mid_template(descriptor: MidScopeDescriptor, record: Mapping[str, Any]) -> MidSourceProof:
    """지역 공유 원문의 값(D+3 시작 26/16개, D+4 시작 22/14개)만 복원한다.

    위치별 전체 목록을 만들지 않는다.
    """
    return _prove_mid_source(
        descriptor.model_copy(update={"location_ids": descriptor.location_ids[:1]}), record
    )


def normalize_mid_source(descriptor: MidScopeDescriptor, record: Mapping[str, Any]):
    template = prove_mid_template(descriptor, record)
    for location in descriptor.location_ids:
        for value in template.values:
            yield value.model_copy(update={"location_id": location})
