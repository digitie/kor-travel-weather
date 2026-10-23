"""SDK 원천의 HTTP 전 단일 위치 승인 계획과 기존 native ingest 연결."""

import logging
from dataclasses import dataclass

from sqlalchemy import select

from .export_external_collector import ExternalExportCollector
from .export_receipts import ExportAttempt
from .export_sdk import DATASETS, SdkScopeDescriptor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedSdkExport:
    identity: ExportAttempt
    descriptor: SdkScopeDescriptor


def sdk_descriptor(location, *, provider, dataset_key):
    """원본 HTTP 전에 canonical anchor의 필요한 작은 문맥만 복사한다."""
    point = location.metadata.get("measurement_point", {})
    if point.get("provider") != provider:
        raise ValueError("선택 위치가 해당 SDK 관측소가 아닙니다.")
    station = point.get("station_id")
    name = point.get("station_name")
    # AirKorea는 코드가 없는 이름+주소 기반 anchor도 있다. 이름/SIDO를 고정한다.
    if provider == "python-airkorea-api" and not station:
        station = location.location_id
    return SdkScopeDescriptor(
        provider=provider, dataset_key=dataset_key,
        entity_type="airkorea_station" if provider == "python-airkorea-api" else "weather_response",
        entity_id=location.location_id, location_ids=(location.location_id,),
        latitude=location.latitude, longitude=location.longitude,
        station_id=station, station_name=name, sido_name=point.get("sido_name"),
        forecast_style=DATASETS[dataset_key][2],
    )


class SdkExportCollector(ExternalExportCollector):
    def prepare(self, *, location, provider, dataset_key):
        identity = None
        try:
            descriptor = sdk_descriptor(location, provider=provider, dataset_key=dataset_key)
            runs = self.heads.metadata.tables["weather_sync_runs"]
            with self.heads._transaction() as session:
                orchestrator = session.scalar(
                    select(runs.c.orchestrator_run_id).where(runs.c.run_id == self.run_id)
                )
            identity = self.repository.begin_export_attempt(
                run_id=self.run_id, scope_key=descriptor.namespace,
                planned_sources=[descriptor.source_scope],
                execution_mode="dagster" if orchestrator else "native",
            )
            self.heads.lifecycle.claim(identity)
            self.heads.declare(identity, descriptor)
            return PreparedSdkExport(identity, descriptor)
        except Exception:
            logger.warning("sdk_export_prepare_failed run_id=%s", self.run_id)
            if identity is not None:
                self._reject(identity, "PREPARE_FAILED")
            return None
