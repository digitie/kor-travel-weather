"""외부 단일 응답의 위치·완결·기존 정규화 증거. 발표 revision은 추측하지 않는다."""

from collections.abc import Mapping
from datetime import UTC, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .export_artifacts import ExportArtifactError
from .export_receipts import source_scope_key
from .providers.base import ProviderLocation, parse_datetime
from .providers.external import OpenMeteoProvider, OpenWeatherMapProvider, WeatherApiProvider

ADAPTERS = {
    "open_meteo": OpenMeteoProvider,
    "weatherapi": WeatherApiProvider,
    "openweathermap": OpenWeatherMapProvider,
}
CORE = frozenset({"TEMP", "HUMIDITY", "WIND_SPEED"})


class RetrievalScopeDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: Literal["open_meteo", "weatherapi", "openweathermap"]
    dataset_key: str = Field(min_length=1, max_length=120)
    entity_type: Literal["weather_response"] = "weather_response"
    entity_id: str = Field(min_length=1, max_length=120)
    forecast_style: Literal["observed", "short"]
    location_ids: tuple[str, ...] = Field(min_length=1, max_length=1)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    normalizer_revision: str = Field(min_length=1, max_length=64)
    winner_policy_revision: Literal["latest-retrieval-v1"] = "latest-retrieval-v1"
    coverage_contract: Literal["external-location-v1"] = "external-location-v1"

    @model_validator(mode="after")
    def canonical(self):
        suffix = "current" if self.forecast_style == "observed" else "forecast"
        if (
            self.location_ids != (self.entity_id,)
            or self.dataset_key != f"{self.provider}_{suffix}"
            or self.normalizer_revision != f"{self.provider}-v1"
        ):
            raise ValueError("외부 원천의 위치/dataset/style/정규화 정책이 다릅니다.")
        return self

    @property
    def source_scope(self):
        return source_scope_key(self.provider, self.dataset_key, self.entity_type, self.entity_id)

    @property
    def namespace(self):
        from .export_heads import _digest

        return _digest(self.model_dump(mode="json"))


class _NoNetwork:
    def request(self, *args, **kwargs):
        raise ExportArtifactError("원천 증명은 HTTP를 다시 호출할 수 없습니다.")


def _coordinates(provider, raw, request, descriptor):
    """요청 좌표는 정확히, 응답 격자/인근 관측점은 명시 0.5도 이내로 검증한다."""
    if provider == "weatherapi":
        wanted = [float(v) for v in str(request["q"]).split(",")]
        actual = [raw["location"]["lat"], raw["location"]["lon"]]
    elif provider == "open_meteo":
        wanted = [request["latitude"], request["longitude"]]
        actual = [raw["latitude"], raw["longitude"]]
    else:
        wanted = [request["lat"], request["lon"]]
        coord = raw["city"]["coord"] if descriptor.forecast_style == "short" else raw["coord"]
        actual = [coord["lat"], coord["lon"]]
    selected = [descriptor.latitude, descriptor.longitude]
    if len(wanted) != 2 or any(float(a) != b for a, b in zip(wanted, selected, strict=True)):
        raise ExportArtifactError("원천 요청 좌표가 사전 선언과 다릅니다.")
    if any(not abs(float(a) - b) <= 0.5 for a, b in zip(actual, selected, strict=True)):
        raise ExportArtifactError("원천 응답 격자/관측점이 선택 위치와 너무 멉니다.")


def _raw_times(provider, raw, request, forecast):
    """스키마/길이만 검증한다. 값 해석은 기존 adapter의 정규화에 맡긴다."""
    if provider == "open_meteo":
        if forecast:
            block = raw["hourly"]
            times = block["time"]
            requested = str(request["hourly"]).split(",")
            expected = int(request.get("forecast_days", 7)) * 24
            if not isinstance(times, list) or not 48 <= expected <= 384 or len(times) != expected:
                raise ExportArtifactError("Open-Meteo 예보 시간 배열이 불완전합니다.")
            if any(
                not isinstance(block[k], list) or len(block[k]) != len(times) for k in requested
            ):
                raise ExportArtifactError("Open-Meteo 요청 변수 배열 길이가 다릅니다.")
        else:
            times = [raw["current"]["time"]]
    elif provider == "weatherapi":
        if forecast:
            days = raw["forecast"]["forecastday"]
            if len(days) != int(request["days"]) or any(len(day["hour"]) != 24 for day in days):
                raise ExportArtifactError("WeatherAPI 요청 일수/시간 집합이 불완전합니다.")
            times = [hour["time_epoch"] for day in days for hour in day["hour"]]
        else:
            times = [raw["current"]["last_updated_epoch"]]
    else:
        if forecast:
            rows = raw["list"]
            if len(rows) != 40 or int(raw["cnt"]) != len(rows):
                raise ExportArtifactError("OpenWeatherMap 5일/3시간 전체 집합이 아닙니다.")
            times = [row["dt"] for row in rows]
        else:
            times = [raw["dt"]]
    parsed = [parse_datetime(value, tz=UTC) for value in times]
    if not parsed or sorted(set(parsed)) != parsed:
        raise ExportArtifactError("원천 시간 집합이 비었거나 중복/역순입니다.")
    step = 3 if provider == "openweathermap" else 1
    if forecast and any(
        b - a != timedelta(hours=step) for a, b in zip(parsed, parsed[1:], strict=False)
    ):
        raise ExportArtifactError("원천 예보의 필수 시간대에 공백이 있습니다.")
    return parsed


def normalize_external_source(descriptor: RetrievalScopeDescriptor, record: Mapping):
    """사전 선언과 저장 raw를 검증하고 기존 adapter 결과를 독립 반환한다."""
    try:
        payload = record["payload"]
        if len(payload["rows"]) != 1 or not isinstance(payload["rows"][0], Mapping):
            raise ExportArtifactError("외부 원천은 완결된 단일 object 응답이어야 합니다.")
        raw, metadata = payload["rows"][0], payload["response_metadata"]
        if metadata["status"] != 200:
            raise ExportArtifactError("정상 HTTP 응답 증거가 없습니다.")
        fetched = record["fetched_at"]
        if fetched.tzinfo is None or fetched.utcoffset() is None:
            raise ExportArtifactError("실제 조회 시각에는 timezone이 필요합니다.")
        _coordinates(descriptor.provider, raw, metadata["request_params"], descriptor)
        forecast = descriptor.forecast_style == "short"
        times = _raw_times(descriptor.provider, raw, metadata["request_params"], forecast)
        if not forecast and not fetched - timedelta(hours=3) <= times[0] <= fetched:
            raise ExportArtifactError("실관측 원천 시각이 현재 관측 범위를 벗어납니다.")
        future = [t for t in times if t > fetched]
        if forecast and (
            len(future) < (39 if descriptor.provider == "openweathermap" else 24)
            or future[0]
            > fetched + timedelta(hours=3 if descriptor.provider == "openweathermap" else 1)
        ):
            raise ExportArtifactError("원천 예보의 가까운 미래 시간대가 불완전합니다.")
        adapter = ADAPTERS[descriptor.provider](transport=_NoNetwork())
        response = adapter.normalize_response(
            ProviderLocation(descriptor.entity_id, descriptor.latitude, descriptor.longitude, {}),
            dataset_key=descriptor.dataset_key,
            payload=raw,
            metadata=metadata,
            at=fetched,
        )
        if response.source_record["source_record_key"] != record["source_record_key"]:
            raise ExportArtifactError("원본 key와 재정규화한 원천 key가 다릅니다.")
        if not 1 <= len(response.values) <= 5000:
            raise ExportArtifactError("단일 위치 정규화 상한을 넘거나 비었습니다.")
        groups = {}
        bounds = {"TEMP": (-90, 65), "HUMIDITY": (0, 100), "WIND_SPEED": (0, 150)}
        for value in response.values:
            number = value.value_number
            low, high = bounds.get(value.metric_key, (float("-inf"), float("inf")))
            if number is not None and number.is_finite() and low <= number <= high:
                groups.setdefault(value.target_at, set()).add(value.metric_key)
        required = future if forecast else times
        if any(not CORE.issubset(groups.get(at, set())) for at in required):
            raise ExportArtifactError("필수 시간대의 기온/습도/풍속 정상값이 누락됐습니다.")
        return response.values
    except ExportArtifactError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        raise ExportArtifactError("외부 원천의 요청·응답 계약 증명이 불완전합니다.") from exc
