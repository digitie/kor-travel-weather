"""공개 SDK의 단일 원문 재해석 증명. 전국 응답 완결성은 별도 cohort의 책임이다."""

from collections.abc import Mapping
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .export_artifacts import ExportArtifactError
from .export_receipts import source_scope_key
from .providers import airkorea, khoa, krex, krforest, krforest_dust

DATASETS = {
    "airkorea_realtime_measurement": ("python-airkorea-api", "air_quality", "observed"),
    "krforest_mountain_weather": ("python-krforest-api", "mountain_weather", "observed"),
    "krforest_dust": ("python-krforest-api", "air_quality", "observed"),
    "krex_restarea_weather": ("python-krex-api", "highway_weather", "observed"),
    "khoa_beach_index": ("python-khoa-api", "marine_weather", "short"),
}


class SdkScopeDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: Literal[
        "python-airkorea-api", "python-krforest-api", "python-krex-api", "python-khoa-api"
    ]
    dataset_key: str = Field(min_length=1, max_length=120)
    entity_type: Literal["airkorea_station", "weather_response"] = "weather_response"
    entity_id: str = Field(min_length=1, max_length=120)
    forecast_style: Literal["observed", "short"]
    location_ids: tuple[str, ...] = Field(min_length=1, max_length=1)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    station_id: str = Field(min_length=1, max_length=120)
    station_name: str = Field(min_length=1, max_length=200)
    sido_name: str | None = Field(default=None, max_length=32)
    # 기존 Weather 정규화의 label을 유지한다. KMA parser를 사용한다는 뜻이 아니다.
    normalizer_revision: Literal["kma-v1"] = "kma-v1"
    winner_policy_revision: Literal["latest-retrieval-v1"] = "latest-retrieval-v1"
    coverage_contract: Literal["sdk-location-v1"] = "sdk-location-v1"

    @model_validator(mode="after")
    def canonical(self):
        expected = DATASETS.get(self.dataset_key)
        entity = (
            "airkorea_station" if self.provider == "python-airkorea-api" else "weather_response"
        )
        if (
            expected is None
            or (self.provider, self.forecast_style) != (expected[0], expected[2])
            or self.location_ids != (self.entity_id,)
            or self.entity_type != entity
        ):
            raise ValueError("SDK 원천의 dataset/위치/style 계약이 다릅니다.")
        return self

    @property
    def weather_domain(self):
        return DATASETS[self.dataset_key][1]

    @property
    def source_scope(self):
        return source_scope_key(self.provider, self.dataset_key, self.entity_type, self.entity_id)

    @property
    def namespace(self):
        from .export_heads import _digest

        return _digest(self.model_dump(mode="json"))


def _raw(record, *, air=False):
    payload = record["payload"]
    if air:
        raw = payload["measurement"]
    else:
        if len(payload["rows"]) != 1:
            raise ExportArtifactError("SDK 위치 원문은 정확히 한 object여야 합니다.")
        raw = payload["rows"][0]
    if not isinstance(raw, Mapping):
        raise ExportArtifactError("SDK 원문 object가 없습니다.")
    return dict(raw)


def _match_coordinates(latitude, longitude, descriptor):
    if latitude is None or longitude is None or (
        abs(float(latitude) - descriptor.latitude) > 0.000001
        or abs(float(longitude) - descriptor.longitude) > 0.000001
    ):
        raise ExportArtifactError("SDK 응답 좌표가 사전 선언한 관측소와 다릅니다.")


def _replay(descriptor, record):
    """SDK parser와 기존 Weather adapter를 호출한다. 필드 해석을 복제하지 않는다."""
    fetched = record["fetched_at"]
    dataset = descriptor.dataset_key
    if dataset == "airkorea_realtime_measurement":
        from airkorea import parse_measurement

        raw = _raw(record, air=True)
        parsed = parse_measurement(raw, station_name=descriptor.station_name)
        if parsed.station_name != descriptor.station_name:
            raise ExportArtifactError("AirKorea 관측소명이 사전 선언과 다릅니다.")
        actual_sido = airkorea.normalize_sido_name(raw.get("sidoName"))
        if descriptor.sido_name and actual_sido != descriptor.sido_name:
            raise ExportArtifactError("AirKorea 시도가 사전 선언과 다릅니다.")
        source = airkorea.measurement_source_record(
            parsed, location_id=descriptor.entity_id, fetched_at=fetched
        )
        normalizer, observed = airkorea.measurement_to_weather_values, parsed.data_time
    elif dataset == "krforest_mountain_weather":
        from krforest.parser import parse_mountain_weather

        parsed = parse_mountain_weather(_raw(record))
        if parsed.obs_id != descriptor.station_id:
            raise ExportArtifactError("산악 관측소 코드가 사전 선언과 다릅니다.")
        _match_coordinates(parsed.latitude, parsed.longitude, descriptor)
        source = krforest.mountain_source_record(
            parsed, location_id=descriptor.entity_id, fetched_at=fetched
        )
        normalizer, observed = krforest.mountain_weather_to_weather_values, parsed.observed_at
    elif dataset == "krforest_dust":
        from krforest.parser import parse_forest_dust_measurement

        parsed = parse_forest_dust_measurement(_raw(record))
        if parsed.station_code != descriptor.station_id:
            raise ExportArtifactError("청정넷 관측소 코드가 사전 선언과 다릅니다.")
        source = krforest_dust.dust_source_record(
            parsed, location_id=descriptor.entity_id, fetched_at=fetched
        )
        normalizer, observed = krforest_dust.dust_to_weather_values, parsed.observed_at
    elif dataset == "krex_restarea_weather":
        from krex import parse_rest_area_weather

        parsed = parse_rest_area_weather(_raw(record))
        if parsed.unit_code != descriptor.station_id:
            raise ExportArtifactError("휴게소 코드가 사전 선언과 다릅니다.")
        _match_coordinates(parsed.lat, parsed.lon, descriptor)
        source = krex.restarea_source_record(
            parsed, location_id=descriptor.entity_id, fetched_at=fetched
        )
        normalizer, observed = krex.restarea_weather_to_weather_values, parsed.observed_at
    else:
        from khoa import Observatory, parse_beach_index_place

        raw = _raw(record)
        rows = raw["rows"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 62:
            raise ExportArtifactError("해수욕장 원문 예보가 비었거나 상한을 넘었습니다.")
        # 공개 parser가 Observatory 문맥을 신뢰하므로 원문별 장소와 좌표를 먼저 확인한다.
        for row in rows:
            if row.get("bbchNm") != descriptor.station_name:
                raise ExportArtifactError("해수욕장 원문 장소가 사전 선언과 다릅니다.")
            _match_coordinates(row.get("lat"), row.get("lot"), descriptor)
        parsed = parse_beach_index_place(
            Observatory(
                id=descriptor.station_id, name=descriptor.station_name,
                latitude=descriptor.latitude, longitude=descriptor.longitude,
            ), rows,
        )
        slots = [(item.predicted_on, item.forecast_period) for item in parsed.forecasts]
        if len(set(slots)) != len(slots) or any(
            day is None or period not in {"오전", "오후"} for day, period in slots
        ):
            raise ExportArtifactError("해수욕장 예보 날짜/구간이 중복되거나 불명확합니다.")
        source = khoa.beach_index_source_record(
            parsed, location_id=descriptor.entity_id, fetched_at=fetched
        )
        return source, khoa.beach_index_to_weather_values, parsed, None
    max_age = 3 if dataset == "airkorea_realtime_measurement" else 36
    if observed is None or not fetched - timedelta(hours=max_age) <= observed <= fetched:
        raise ExportArtifactError("실제 관측 시각이 없거나 허용 범위를 벗어났습니다.")
    return source, normalizer, parsed, observed


def normalize_sdk_source(descriptor: SdkScopeDescriptor, record: Mapping):
    """원문에서 독립 재정규화한다. 이 함수는 전국/페이지 완결을 증명하지 않는다."""
    try:
        fetched = record["fetched_at"]
        if fetched.tzinfo is None or fetched.utcoffset() is None:
            raise ExportArtifactError("실제 조회 시각에는 timezone이 필요합니다.")
        source, normalizer, parsed, _ = _replay(descriptor, record)
        if source["source_record_key"] != record["source_record_key"]:
            raise ExportArtifactError("SDK 원본 key가 재해석한 원천 key와 다릅니다.")
        values = normalizer(
            parsed, location_id=descriptor.entity_id,
            source_record_key=source["source_record_key"], known_at=fetched,
        )
        if not 1 <= len(values) <= 5000:
            raise ExportArtifactError("SDK 정규화 결과가 비었거나 상한을 넘었습니다.")
        bounds = {
            "TEMP": (-90, 65), "TEMP_10M": (-90, 65), "DEW_POINT": (-100, 65),
            "GROUND_TEMP": (-100, 100), "WATER_TEMP": (-10, 65),
            "HUMIDITY": (0, 100), "HUMIDITY_10M": (0, 100),
            "WIND_SPEED": (0, 150), "WIND_SPEED_10M": (0, 150),
        }
        numeric = False
        for value in values:
            number = value.value_number
            if number is not None:
                low, high = bounds.get(value.metric_key, (0, float("inf")))
                if not number.is_finite() or not low <= number <= high:
                    raise ExportArtifactError("SDK 수치가 유효 관측 범위를 벗어났습니다.")
                numeric = True
        if not numeric:
            raise ExportArtifactError("SDK 응답에 유효한 수치가 없습니다.")
        if descriptor.forecast_style == "short":
            future = [v.target_at for v in values if v.target_at > fetched]
            if not future or min(future) > fetched + timedelta(hours=36) or (
                max(future) > fetched + timedelta(days=7)
            ):
                raise ExportArtifactError("해수욕장 예보의 가까운 미래 범위가 없습니다.")
        return values
    except ExportArtifactError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        raise ExportArtifactError("SDK 원천 증명이 불완전합니다.") from exc
