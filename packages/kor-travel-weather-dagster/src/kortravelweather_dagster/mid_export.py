"""명시 opt-in 중기 수집: 지역별 한 HTTP·같은 SDK 회차·bounded native 저장."""

import asyncio
import json
import logging
from dataclasses import dataclass

from kma.time_utils import latest_mid_fcst_time

from kortravelweather.export_mid import MidScopeDescriptor
from kortravelweather.models import kst_now
from kortravelweather.providers.kma import (
    mid_land_forecast_to_weather_values,
    mid_temperature_to_weather_values,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MidNativeScope:
    source_part: str
    region_code: str
    location_ids: tuple[str, ...]

    @property
    def entity_id(self):
        return f"mid-{self.source_part}:{self.region_code}"

    def descriptor(self):
        return MidScopeDescriptor(
            entity_id=self.entity_id,
            source_part=self.source_part,
            region_code=self.region_code,
            location_ids=self.location_ids,
            normalizer_revision=f"kma-mid-{self.source_part}-v1",
        )


def _native_scopes(targets):
    groups, seen = {}, set()
    for target in targets:
        if not target.has_mid:
            continue
        for part, region in (
            ("land", target.land_region_code),
            ("temperature", target.temperature_region_code),
        ):
            key = target.location.location_id
            if (key, part) in seen:
                raise ValueError("중기 위치·역할이 중복됩니다.")
            seen.add((key, part))
            groups.setdefault((part, region), []).append(key)
    return [
        MidNativeScope(part, region, tuple(sorted(keys)))
        for (part, region), keys in sorted(groups.items())
    ]


def mid_descriptors(targets):
    return [scope.descriptor() for scope in _native_scopes(targets)]


def _declare_cohort(repository, run_id, descriptors):
    from kortravelweather.export_source_cohorts import declare_source_cohort

    return declare_source_cohort(
        repository,
        run_id=run_id,
        provider="python-kma-api",
        dataset_key="kma_mid_forecast",
        descriptors=descriptors,
    )


def _native_fanout(repository, record, template, locations, heartbeat):
    loaded, batch, location_count = 0, [], 0
    for key in locations:
        if batch and (len(batch) + len(template) > 2500 or location_count >= 50):
            heartbeat()
            loaded += repository.ingest_batch(source_records=[record], values=batch)
            batch, location_count = [], 0
        batch.extend(value.model_copy(update={"location_id": key}) for value in template)
        location_count += 1
    if batch or not template:
        heartbeat()
        loaded += repository.ingest_batch(source_records=[record], values=batch)
    return loaded


async def collect_mid_exports(
    *,
    repository,
    run_id,
    targets,
    data_client,
    artifact_root,
    max_response_rows,
    max_values,
    retries,
    heartbeat,
):
    from kortravelweather.export_mid_collector import MidExportCollector

    from .kma_weather import (
        GRID_RESPONSE_BYTES,
        _bounded_conversion,
        _bounded_rows,
        _kma_provider_call,
        _mid_region,
        _source_spec,
    )

    if data_client is None:
        raise ValueError("중기예보에는 DataGoKrClient가 필요합니다.")
    scopes = _native_scopes(targets)
    if not scopes:
        raise ValueError("중기예보 지역이 비어 있습니다.")
    collector, prepared = None, {}
    try:
        # export의 source/location 상한은 native 유효 입력의 상한과 다르다.
        # 큰 지역 등 승인 불가도 원래 bounded 원본 저장을 막지 않는다.
        descriptors = [scope.descriptor() for scope in scopes]
        # roster와 모든 source plan은 첫 HTTP 전에 선언한다. 선언 실패를 이전
        # 정상 run으로 숨기지 않도록 native INSERT의 intent가 별도로 남는다.
        await asyncio.to_thread(_declare_cohort, repository, run_id, descriptors)
        collector = MidExportCollector(repository, run_id=run_id, artifact_root=artifact_root)
        for descriptor in descriptors:
            prepared[descriptor.entity_id] = await asyncio.to_thread(
                collector.prepare,
                descriptor=descriptor,
            )
    except Exception:
        logger.warning("mid_export_cohort_deferred run_id=%s", run_id)
        collector = None
    # SDK 회차를 한 번만 선택해 land/temperature 모두에 동일하게 명시한다.
    tm_fc = latest_mid_fcst_time(kst_now())
    requests = rows_total = normalized = accepted = loaded = 0
    for descriptor in scopes:
        heartbeat()
        if rows_total >= max_response_rows or normalized >= max_values:
            raise ValueError("중기예보 응답/정규화 예산이 소진되었습니다.")
        method = (
            data_client.mid_land_forecast
            if descriptor.source_part == "land"
            else data_client.mid_temperature_forecast
        )
        for verification in range(2):
            heartbeat()
            fetched = kst_now()
            rows = _bounded_rows(
                await _kma_provider_call(
                    lambda method=method, region=descriptor.region_code: method(
                        reg_id=region, tm_fc=tm_fc
                    ),
                    dataset="kma_mid_forecast",
                    retries=retries,
                ),
                limit=min(1, max_response_rows - rows_total),
                label="중기 지역 원천",
            )
            requests += 1
            rows_total += len(rows)
            if rows:
                break
            if verification == 0:
                # transport 재시도는 SDK가 소유한다. 명시 성공의 빈 집합만 같은
                # 회차로 한 번 재확인하며 새 발표 회차/계획을 만들어 성공으로 숨기지 않는다.
                await asyncio.sleep(0.2)
        if not rows:
            raise ValueError("중기예보 빈 응답을 재확인했으나 전체 원천을 확인할 수 없습니다.")
        _mid_region(rows, descriptor.region_code)
        record = {
            **_source_spec(
                "kma_mid_forecast",
                descriptor.entity_id,
                rows,
                fetched,
            ),
            "run_id": run_id,
        }
        if len(json.dumps(record, ensure_ascii=False, default=str).encode()) > GRID_RESPONSE_BYTES:
            raise ValueError("중기예보 응답 byte 상한을 초과했습니다.")
        converter = (
            mid_land_forecast_to_weather_values
            if descriptor.source_part == "land"
            else mid_temperature_to_weather_values
        )
        template = _bounded_conversion(
            rows,
            converter,
            max_values=min(5000, max_values - normalized),
            location_id=descriptor.location_ids[0],
            source_record_key=record["source_record_key"],
            known_at=fetched,
        )
        facts = len(template) * len(descriptor.location_ids)
        if normalized + facts > max_values:
            raise ValueError("중기예보 위치 fan-out의 fact 상한을 초과했습니다.")
        if collector is None:
            loaded += await asyncio.to_thread(
                _native_fanout,
                repository,
                record,
                template,
                descriptor.location_ids,
                heartbeat,
            )
        else:
            loaded += await asyncio.to_thread(
                collector.ingest_fanout,
                prepared.get(descriptor.entity_id),
                record=record,
                values=template,
                location_ids=descriptor.location_ids,
                heartbeat=heartbeat,
            )
        accepted += len(template)
        normalized += facts
    if not normalized:
        raise ValueError("중기예보에서 normalized weather fact가 생성되지 않았습니다.")
    return {
        "requests_fetched": requests,
        "response_rows": rows_total,
        "values_accepted": accepted,
        "normalized_values": normalized,
        "loaded": loaded,
    }
