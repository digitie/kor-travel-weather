"""기존 SDK 관측망 수집 전의 canonical roster와 승인 계획만 준비한다."""

import logging
from dataclasses import dataclass, field
from types import SimpleNamespace

from sqlalchemy import select

from .export_artifacts import ExportArtifactError
from .export_heads import ExportHeads
from .export_inventory_selection import SDK_NETWORKS, catalog_source_columns
from .export_sdk_collector import SdkExportCollector, sdk_descriptor
from .export_source_cohorts import declare_source_cohort

logger = logging.getLogger(__name__)


@dataclass
class SdkCollectionContext:
    collector: SdkExportCollector
    plans: dict = field(default_factory=dict)
    complete: bool = True

    def invalidate(self):
        """원천 완결 미확인은 capture만 막고 기존 native 이력 수집은 유지한다."""
        self.complete = False
        self.finish()

    def finish(self):
        """반환되지 않은 source 계획을 영구 preparing queue로 남기지 않는다."""
        for prepared in self.plans.values():
            if prepared is not None:
                self.collector._reject(prepared.identity, "SOURCE_COVERAGE_UNCONFIRMED")
        self.plans.clear()

    def observe_krex_page(self, page):
        from .export_sdk_pages import SdkPageReceipt

        try:
            raw = page.raw["list"]
            if not isinstance(raw, list) or len(raw) != len(page.items):
                raise ValueError("부분 파싱 원문은 승인하지 않습니다.")
            receipt = SdkPageReceipt()
            receipt.add(page, raw_count=len(raw))
            if not receipt.complete:
                self.invalidate()
        except (ValueError, TypeError, KeyError, AttributeError, ExportArtifactError):
            self.invalidate()


def prepare_sdk_collection(repository, *, run_id, provider, dataset_key, artifact_root):
    heads = ExportHeads(repository)
    context = SdkCollectionContext(SdkExportCollector(
        repository, run_id=run_id, artifact_root=artifact_root,
    ))
    try:
        catalog = heads.metadata.tables["weather_locations"]
        # arbitrary metadata를 가져오지 않는다. 각 projected 필드에 byte/길이 상한이 있다.
        with heads._transaction() as s:
            rows = s.execute(select(
                catalog.c.location_id, catalog.c.latitude, catalog.c.longitude,
                *catalog_source_columns(catalog),
            ).where(
                catalog.c.enabled.is_(True),
                catalog.c.metadata["measurement_point"]["provider"].as_string() == provider,
                catalog.c.metadata["measurement_point"]["network"].as_string()
                == SDK_NETWORKS[dataset_key],
            ).order_by(catalog.c.location_id).limit(5001)).mappings().all()
        if not 1 <= len(rows) <= 5000:
            raise ValueError("SDK 최초 catalog bootstrap 또는 상한 확인이 필요합니다.")
        locations = [SimpleNamespace(
            location_id=row["location_id"], latitude=row["latitude"], longitude=row["longitude"],
            metadata={"measurement_point": {
                key.removeprefix("point_"): value for key, value in row.items()
                if key.startswith("point_")
            }},
        ) for row in rows]
        declare_source_cohort(
            repository, run_id=run_id, provider=provider, dataset_key=dataset_key,
            descriptors=[sdk_descriptor(location, provider=provider, dataset_key=dataset_key)
                         for location in locations],
        )
        context.plans = {location.location_id: context.collector.prepare(
            location=location, provider=provider, dataset_key=dataset_key,
        ) for location in locations}
    except Exception:
        logger.warning("sdk_collection_prepare_deferred run_id=%s", run_id)
        context.invalidate()
    return context
